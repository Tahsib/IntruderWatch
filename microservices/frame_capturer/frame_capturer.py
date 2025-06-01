import cv2
import numpy as np
import os
import time
import pika
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def connect_rabbitmq(queue_name):
    # Fetch RabbitMQ connection details from environment variables
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "")
    rabbitmq_user = os.getenv("RABBITMQ_USER", "")
    rabbitmq_password = os.getenv("RABBITMQ_PASS", "")

    # Create RabbitMQ credentials
    credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_password)

    # Set up connection parameters
    connection_params = pika.ConnectionParameters(
        host=rabbitmq_host, credentials=credentials, frame_max=131072)

    try:
        # Establish connection and channel
        connection = pika.BlockingConnection(connection_params)
        channel = connection.channel()

        # Declare the queue
        channel.queue_declare(queue=queue_name, durable=True)

        print(f"Connected to RabbitMQ at {rabbitmq_host}, queue: {queue_name}")
        return connection, channel
    except pika.exceptions.ProbableAuthenticationError:
        print("Authentication failed! Check RabbitMQ credentials.")
        raise
    except Exception as e:
        print(f"Failed to connect to RabbitMQ: {e}")
        raise


def is_within_time_frame(start_time, end_time):
    now = datetime.now().time()
    return start_time <= now <= end_time


def capture_frames(ip, channel, stream, username, password, queue_name):
    # Parse start and end times from environment variables
    start_time = datetime.strptime(
        os.getenv("START_TIME", "00:00:00"), "%H:%M:%S").time()
    end_time = datetime.strptime(
        os.getenv("END_TIME", "23:59:59"), "%H:%M:%S").time()
    last_log_time = 0

    rtsp_url = f"rtsp://{ip}:554/user={username}&password={password}&channel={channel}&stream={stream}.sdp"
    logging.info(f"Attempting to connect to: {rtsp_url}")

    while True:
        try:
            cap = cv2.VideoCapture(rtsp_url)
            if not cap.isOpened():
                logging.error(
                    "Failed to open stream. Retrying in 5 seconds...")
                time.sleep(5)
                continue

            logging.info("Successfully connected to stream!")
            connection, channel = connect_rabbitmq(queue_name)

            while cap.isOpened():
                # Check if the current time is within the allowed time frame
                if is_within_time_frame(start_time, end_time):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        success, img_encode = cv2.imencode('.png', frame)
                        if success:
                            data_encode = np.array(img_encode) 
                            byte_data = data_encode.tobytes()
                            channel.basic_publish(exchange='', routing_key=queue_name, body=byte_data, properties=pika.BasicProperties(delivery_mode=2))
                            logging.info("Frame sent to queue.")
                        else:
                            logging.error("Frame encoding failed!!")
                        time.sleep(0.2)  # Adjust frame rate
                    else:
                        logging.error(
                            "Failed to read frame. Reinitializing capture...")
                        break  # Exit loop to reinitialize capture
                else:
                    # Check if 30 minutes have passed since last log
                    current_time = time.time()
                    if current_time - last_log_time >= 30 * 60:  # 30 minutes
                        logging.info(f"Outside of active hours ({start_time} to {end_time}). Waiting...")
                        last_log_time = current_time  # Update last log time
                    time.sleep(60)  # Wait for 1 minute before checking again
        except Exception as e:
            logging.error(f"Capture failed with error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        finally:
            cap.release()


if __name__ == "__main__":
    stream_ip = os.getenv("STREAM_IP")
    stream_username = os.getenv("STREAM_USERNAME")
    stream_password = os.getenv("STREAM_PASSWORD")
    stream_channel = int(os.getenv("CHANNEL"))

    capture_frames(
        ip=stream_ip,
        channel=stream_channel,
        stream=0,
        username=stream_username,
        password=stream_password,
        queue_name="frame_queue"
    )
