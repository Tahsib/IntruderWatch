import time
import os
import json
import logging
import threading
import requests
from flask import Flask, request, jsonify
from twilio.rest import Client
from shared.rabbitmq_client import connect_rabbitmq
from prometheus_client import start_http_server, Counter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [alert_service] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Prometheus Metrics
ALERTS_TOTAL = Counter(
    "alert_service_alerts_total", "Total alerts received from queue", ["camera_id"]
)
ALERTS_SUPPRESSED = Counter(
    "alert_service_alerts_suppressed_total",
    "Total alerts suppressed by cooldown",
    ["camera_id"],
)
NOTIFICATIONS_SENT = Counter(
    "alert_service_notifications_sent_total",
    "Total notifications attempted",
    ["type", "destination"],
)
NOTIFICATION_ERRORS = Counter(
    "alert_service_notification_errors_total",
    "Total notification errors",
    ["type", "destination", "error_type"],
)

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
ALERT_PHONE_NUMBERS = os.getenv("ALERT_PHONE_NUMBERS", "")
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "90"))

# ntfy Configuration (Self-Hosted Private Alerts)
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", "http://localhost:8081")
NTFY_INTERNAL_URL = os.getenv("NTFY_INTERNAL_URL", "http://ntfy")
PUBLIC_DOMAIN = os.getenv("PUBLIC_DOMAIN", "yourdomain.com")
# Mobile notifications use the secure public tunnel
VIEWER_PUBLIC_URL = f"https://watch.{PUBLIC_DOMAIN}"
ALERT_BYPASS_TOKEN = os.getenv("ALERT_BYPASS_TOKEN")

# --- Flask Webhook Receiver ---
app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def webhook():
    """Receives JSON alerts from Alertmanager and sends clean ntfy messages."""
    # Mapping of allowed keys to safe static topic names
    TOPIC_MAP = {
        "intruder-alerts": "intruder-alerts",
        "infra-alerts": "infra-alerts",
        "hardware-alerts": "hardware-alerts",
    }

    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data"}), 400

        # Determine the ntfy topic key
        requested_topic = request.args.get("topic", "infra-alerts")
        safe_topic = TOPIC_MAP.get(requested_topic)

        if not safe_topic:
            # Sanitize for logging
            sanitized_requested = (
                str(requested_topic).replace("\n", "").replace("\r", "")[:50]
            )
            logging.warning(f"Unauthorized topic requested: {sanitized_requested}")
            return jsonify({"error": "Invalid topic"}), 403

        # Alertmanager sends multiple alerts in one POST
        for alert in data.get("alerts", []):
            summary = alert.get("annotations", {}).get("summary", "No summary provided")
            alertname = alert.get("labels", {}).get("alertname", "Unknown Alert")
            severity = alert.get("labels", {}).get("severity", "warning")
            status = alert.get("status", "firing")

            # Use the safe mapped topic for URL construction
            url = f"{NTFY_INTERNAL_URL}/{safe_topic}"

            # Format the clean message
            if status == "firing":
                icon = "🚨 "
                message = f"{icon}{summary}"
                tag = "warning"
                priority = "5" if severity == "critical" else "3"
            else:
                icon = "✅ "
                message = f"{icon}RESOLVED: {alertname}"
                tag = "white_check_mark"
                priority = "1"

            headers = {
                "Title": str(alertname)[:100],
                "Priority": priority,
                "Tags": tag,
            }

            # Send clean text body to ntfy
            response = requests.post(url, data=message, headers=headers, timeout=10)
            response.raise_for_status()

            # Aggressive sanitization for logging
            safe_log_msg = str(message).replace("\n", " ").replace("\r", "")[:200]
            logging.info(f"Clean alert sent to ntfy/{safe_topic}: {safe_log_msg}")
            NOTIFICATIONS_SENT.labels(type="ntfy_text", destination=safe_topic).inc()

        return jsonify({"status": "ok"}), 200

    except Exception:
        # Avoid logging the raw exception if CodeQL is sensitive
        logging.error("Webhook processing failed due to an internal error.")
        return jsonify({"error": "Internal server error"}), 500


def run_webhook_server():
    """Runs the Flask server in a separate thread."""
    # Use port 8082 to avoid conflict with Prometheus metrics (8002)
    app.run(host="0.0.0.0", port=8082)


def mask_phone(phone):
    """Masks all but the last 4 digits of a phone number for secure logging."""
    if not phone or len(phone) < 4:
        return "****"
    return "*" * (len(phone) - 4) + phone[-4:]


def send_call_alert(client, to_phone_number):
    try:
        call = client.calls.create(
            url="http://demo.twilio.com/docs/voice.xml",
            to=to_phone_number,
            from_=TWILIO_PHONE_NUMBER,
        )
        logging.info(
            f"Call alert sent to {mask_phone(to_phone_number)}. SID: {call.sid}"
        )
        NOTIFICATIONS_SENT.labels(type="twilio_call", destination=to_phone_number).inc()
    except Exception as e:
        logging.error(f"Failed to send call to {mask_phone(to_phone_number)}: {e}")
        NOTIFICATION_ERRORS.labels(
            type="twilio_call", destination=to_phone_number, error_type=type(e).__name__
        ).inc()


def send_ntfy_photo(camera_id, timestamp, filename):
    # Truncate milliseconds for a cleaner title
    clean_timestamp = timestamp.split(".")[0] if "." in timestamp else timestamp

    # Unified topic for all cameras
    topic = "intruder-alerts"
    url = f"{NTFY_INTERNAL_URL}/{topic}"

    # Construct the image URL for the mobile app to download from viewer_service
    try:
        parts = filename.split("/")
        if len(parts) >= 4:
            cam_folder = parts[-3]
            date_folder = parts[-2]
            file_name = parts[-1]

            # Use the secure public-facing domain for the phone app
            image_url = (
                f"{VIEWER_PUBLIC_URL}/images/{cam_folder}/{date_folder}/{file_name}"
            )
            if ALERT_BYPASS_TOKEN:
                image_url += f"?token={ALERT_BYPASS_TOKEN}"
        else:
            logging.error(f"Could not parse image path for URL: {filename}")
            return

        headers = {
            "Title": f"Intruder: Camera {camera_id}",
            "Message": f"Detection at {clean_timestamp}",
            "Priority": "5",
            "Tags": "rotating_light,camera",
            "Attach": image_url,
            "Click": image_url,
        }

        # Simple POST with headers is best for URL-based attachments
        response = requests.post(url, headers=headers, timeout=15)
        response.raise_for_status()

        logging.info(
            f"ntfy private photo alert link sent for Camera {camera_id} (Token: [HIDDEN])."
        )
        NOTIFICATIONS_SENT.labels(type="ntfy_photo", destination=camera_id).inc()
    except Exception as e:
        logging.error(f"Failed to send ntfy photo link for Cam {camera_id}: {e}")
        NOTIFICATION_ERRORS.labels(
            type="ntfy_photo", destination=camera_id, error_type=type(e).__name__
        ).inc()


def _dispatch_alerts(twilio_client, phone_numbers, camera_id, timestamp, filename):
    # 1. Dispatch Twilio Calls (Urgent / Redundancy)
    for number in phone_numbers.split(":"):
        if number.strip():
            send_call_alert(twilio_client, number.strip())

    # 2. Dispatch ntfy Photo (Visual / Private)
    send_ntfy_photo(camera_id, timestamp, filename)


def alert_service(queue_name):
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    last_alert_time = 0

    def callback(ch, method, properties, body):
        nonlocal last_alert_time
        current_time = time.time()

        try:
            data = json.loads(body.decode("utf-8"))
            camera_id = data.get("camera", "unknown")
            timestamp = data.get("timestamp", "unknown")
            filename = data.get("filename", "")
        except Exception:
            camera_id = "unknown"
            timestamp = "unknown"
            filename = ""

        ALERTS_TOTAL.labels(camera_id=camera_id).inc()

        if current_time - last_alert_time <= ALERT_COOLDOWN:
            logging.info(
                f"Cooldown active for camera {camera_id}, suppression in effect."
            )
            ALERTS_SUPPRESSED.labels(camera_id=camera_id).inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        logging.info(
            f"!!! ALERT !!! Human detected on Camera {camera_id} at {timestamp}"
        )
        last_alert_time = current_time

        # Dispatch alerts in background
        threading.Thread(
            target=_dispatch_alerts,
            args=(twilio_client, ALERT_PHONE_NUMBERS, camera_id, timestamp, filename),
            daemon=True,
        ).start()
        ch.basic_ack(delivery_tag=method.delivery_tag)

    connection, channel = connect_rabbitmq(queue_name)
    channel.basic_consume(
        queue=queue_name, on_message_callback=callback, auto_ack=False
    )
    logging.info(f"Alert Service running. Cooldown: {ALERT_COOLDOWN}s")
    channel.start_consuming()


if __name__ == "__main__":
    # 1. Start Prometheus metrics server
    try:
        start_http_server(8002)
        logging.info("Prometheus metrics started on 8002")
    except Exception as e:
        logging.error(f"Failed to start metrics: {e}")

    # 2. Start the Webhook Receiver thread
    threading.Thread(target=run_webhook_server, daemon=True).start()
    logging.info("Alert Beautifier Webhook started on 8082")

    # 3. Run the RabbitMQ consumer
    alert_service(queue_name="alert_queue")
