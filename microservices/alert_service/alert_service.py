import time
import os
import json
import logging
import threading
from twilio.rest import Client
from shared.rabbitmq_client import connect_rabbitmq
from prometheus_client import start_http_server, Counter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Prometheus Metrics
ALERTS_TOTAL = Counter('alert_service_alerts_total', 'Total alerts received from queue', ['camera_id'])
ALERTS_SUPPRESSED = Counter('alert_service_alerts_suppressed_total', 'Total alerts suppressed by cooldown', ['camera_id'])
NOTIFICATIONS_SENT = Counter('alert_service_notifications_sent_total', 'Total notification calls attempted', ['phone_number'])
NOTIFICATION_ERRORS = Counter('alert_service_notification_errors_total', 'Total notification call errors', ['phone_number', 'error_type'])


def create_twilio_client():
    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_call_alert(client, to_phone_number):
    try:
        call = client.calls.create(
            url="http://demo.twilio.com/docs/voice.xml",
            to=to_phone_number,
            from_=TWILIO_PHONE_NUMBER,
        )
        logging.info(f"Call alert sent to {to_phone_number}. SID: {call.sid}")
        NOTIFICATIONS_SENT.labels(phone_number=to_phone_number).inc()
    except Exception as e:
        logging.error(f"Failed to send call to {to_phone_number}: {e}")
        NOTIFICATION_ERRORS.labels(phone_number=to_phone_number, error_type=type(e).__name__).inc()


def _call_all_numbers(client, phone_numbers):
    for number in phone_numbers.split(":"):
        send_call_alert(client, number)


def alert_service(queue_name):
    client = create_twilio_client()
    last_alert_time = 0

    def callback(ch, method, properties, body):
        nonlocal last_alert_time
        current_time = time.time()
        raw = body.decode("utf-8")

        try:
            data = json.loads(raw)
            camera_id = data.get("camera", "unknown")
            timestamp = data.get("timestamp", "")
        except json.JSONDecodeError:
            camera_id = "unknown"
            timestamp = raw

        ALERTS_TOTAL.labels(camera_id=camera_id).inc()

        if current_time - last_alert_time <= ALERT_COOLDOWN:
            logging.info(f"Camera {camera_id}: cooldown active, alert suppressed.")
            ALERTS_SUPPRESSED.labels(camera_id=camera_id).inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        logging.info(f"Camera {camera_id}: human detected at {timestamp}, calling alert numbers.")
        last_alert_time = current_time
        threading.Thread(
            target=_call_all_numbers, args=(client, ALERT_PHONE_NUMBERS), daemon=True
        ).start()
        ch.basic_ack(delivery_tag=method.delivery_tag)

    connection, channel = connect_rabbitmq(queue_name)
    channel.basic_consume(
        queue=queue_name, on_message_callback=callback, auto_ack=False
    )
    logging.info("Alert Service is running...")
    logging.info(f"Cooldown: {ALERT_COOLDOWN}s")
    channel.start_consuming()


if __name__ == "__main__":
    # Start Prometheus metrics server
    try:
        start_http_server(8002)
        logging.info("Prometheus metrics server started on port 8002")
    except Exception as e:
        logging.error(f"Failed to start Prometheus metrics server: {e}")

    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
    ALERT_PHONE_NUMBERS = os.getenv("ALERT_PHONE_NUMBERS")
    ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", 90))

    alert_service(queue_name="alert_queue")
