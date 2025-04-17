import pika
import time
import os
import logging
from twilio.rest import Client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def send_sms_alert(to_phone_number, message):
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=to_phone_number, from_=TWILIO_PHONE_NUMBER, body=message
        )
        logging.info("Alert sent to your mobile!")
    except Exception as e:
        logging.error(f"Failed to send alert: {e}")


def send_call_alert(to_phone_number):
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        call = client.calls.create(
            url="http://demo.twilio.com/docs/voice.xml",
            to=to_phone_number,
            from_=TWILIO_PHONE_NUMBER,
        )
        logging.info(f"Call alert sent. Call SID: {call.sid}")
    except Exception as e:
        logging.error(f"Failed to send call alert: {e}")


def rabbitmq_connection(queue_name):
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "")
    rabbitmq_user = os.getenv("RABBITMQ_USER", "")
    rabbitmq_password = os.getenv("RABBITMQ_PASS", "")
    credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_password)
    connection_params = pika.ConnectionParameters(
        host=rabbitmq_host, credentials=credentials
    )

    try:
        connection = pika.BlockingConnection(connection_params)
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)
        logging.info(f"Connected to RabbitMQ queue: {queue_name}")
        return connection, channel
    except Exception as e:
        logging.error(f"Failed to connect to RabbitMQ: {e}")
        raise


def alert_service(queue_name):
    last_alert_time = 0

    def callback(ch, method, properties, body):
        nonlocal last_alert_time
        current_time = time.time()
        if current_time - last_alert_time > ALERT_COOLDOWN:
            alert_message = body.decode("utf-8")
            logging.info(f"Processing alert: {alert_message}")

            for number in ALERT_PHONE_NUMBERS.split(":"):
                # send_sms_alert(TO_PHONE_NUMBER, f"Alert: {alert_message} at {timestamp}")
                send_call_alert(number)
            last_alert_time = current_time
        else:
            logging.info("Cooldown period active. Alert suppressed.")

        ch.basic_ack(delivery_tag=method.delivery_tag)

    connection, channel = rabbitmq_connection(queue_name)
    channel.basic_consume(
        queue=queue_name, on_message_callback=callback, auto_ack=False
    )
    logging.info("Alert Service is running...")
    channel.start_consuming()


if __name__ == "__main__":
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
    ALERT_PHONE_NUMBERS = os.getenv("ALERT_PHONE_NUMBERS")
    # Default cooldown is 90 seconds
    ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", 90))

    alert_service(queue_name="alert_queue")
