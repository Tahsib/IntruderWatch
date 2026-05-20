import time
import os
import json
import logging
import threading
import requests
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
ALERTS_TOTAL = Counter('alert_service_alerts_total', 'Total alerts received from queue', ['camera_id'])
ALERTS_SUPPRESSED = Counter('alert_service_alerts_suppressed_total', 'Total alerts suppressed by cooldown', ['camera_id'])
NOTIFICATIONS_SENT = Counter('alert_service_notifications_sent_total', 'Total notifications attempted', ['type', 'destination'])
NOTIFICATION_ERRORS = Counter('alert_service_notification_errors_total', 'Total notification errors', ['type', 'destination', 'error_type'])

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
ALERT_PHONE_NUMBERS = os.getenv("ALERT_PHONE_NUMBERS", "")
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "90"))

# ntfy Configuration (Self-Hosted Private Alerts)
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", "http://ntfy")
VIEWER_BASE_URL = os.getenv("VIEWER_BASE_URL", "http://localhost:8085")
ALERT_BYPASS_TOKEN = os.getenv("ALERT_BYPASS_TOKEN")

def send_call_alert(client, to_phone_number):
    try:
        call = client.calls.create(
            url="http://demo.twilio.com/docs/voice.xml",
            to=to_phone_number,
            from_=TWILIO_PHONE_NUMBER,
        )
        logging.info(f"Call alert sent to {to_phone_number}. SID: {call.sid}")
        NOTIFICATIONS_SENT.labels(type="twilio_call", destination=to_phone_number).inc()
    except Exception as e:
        logging.error(f"Failed to send call to {to_phone_number}: {e}")
        NOTIFICATION_ERRORS.labels(type="twilio_call", destination=to_phone_number, error_type=type(e).__name__).inc()

def send_ntfy_photo(camera_id, timestamp, filename):
    # Truncate milliseconds for a cleaner title
    clean_timestamp = timestamp.split('.')[0] if '.' in timestamp else timestamp
    
    # Topic is unique per camera: e.g. camera_1, camera_2
    topic = f"camera_{camera_id}"
    url = f"{NTFY_BASE_URL}/{topic}"
    
    # Construct the image URL for the mobile app to download from viewer_service
    # Expected internal filename: /app/captures/camera_1/2026-05-20/det_...jpg
    try:
        parts = filename.split('/')
        if len(parts) >= 4:
            cam_folder = parts[-3]
            date_folder = parts[-2]
            file_name = parts[-1]
            
            # Use public-facing base URL for the phone app
            image_url = f"{VIEWER_BASE_URL}/images/{cam_folder}/{date_folder}/{file_name}"
            if ALERT_BYPASS_TOKEN:
                image_url += f"?token={ALERT_BYPASS_TOKEN}"
        else:
            logging.error(f"Could not parse image path for URL: {filename}")
            return

        headers = {
            "Title": "Human Detected",
            "Message": f"Time: {clean_timestamp}",
            "Priority": "5",
            "Tags": "warning,camera",
            "Attach": image_url,
            "Click": image_url
        }
        
        # Simple POST with headers is best for URL-based attachments
        response = requests.post(url, headers=headers, timeout=15)
        response.raise_for_status()
            
        logging.info(f"ntfy private photo alert link sent for Camera {camera_id}.")
        NOTIFICATIONS_SENT.labels(type="ntfy_photo", destination=camera_id).inc()
    except Exception as e:
        logging.error(f"Failed to send ntfy photo link for Cam {camera_id}: {e}")
        NOTIFICATION_ERRORS.labels(type="ntfy_photo", destination=camera_id, error_type=type(e).__name__).inc()

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
            logging.info(f"Cooldown active for camera {camera_id}, suppression in effect.")
            ALERTS_SUPPRESSED.labels(camera_id=camera_id).inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        logging.info(f"!!! ALERT !!! Human detected on Camera {camera_id} at {timestamp}")
        last_alert_time = current_time
        
        # Dispatch alerts in background
        threading.Thread(target=_dispatch_alerts, args=(twilio_client, ALERT_PHONE_NUMBERS, camera_id, timestamp, filename), daemon=True).start()
        ch.basic_ack(delivery_tag=method.delivery_tag)

    connection, channel = connect_rabbitmq(queue_name)
    channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=False)
    logging.info(f"Alert Service running. Cooldown: {ALERT_COOLDOWN}s")
    channel.start_consuming()

if __name__ == "__main__":
    try:
        start_http_server(8002)
        logging.info("Prometheus metrics started on 8002")
    except Exception as e:
        logging.error(f"Failed to start metrics: {e}")

    alert_service(queue_name="alert_queue")
