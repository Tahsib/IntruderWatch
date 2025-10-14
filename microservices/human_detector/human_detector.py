import cv2
import numpy as np
import pika
import time
import os
import logging
import hashlib
import base64
import json
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# Load MobileNet-SSD model
def load_mobilenet_ssd():
    prototxt_path = "deploy.prototxt"
    model_path = "mobilenet_iter_73000.caffemodel"
    net = cv2.dnn.readNetFromCaffe(prototxt_path, model_path)
    return net


# Detect humans
def detect_human_mobilenet_ssd(net, frame):
    blob = cv2.dnn.blobFromImage(
        cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5
    )
    net.setInput(blob)
    detections = net.forward()

    (h, w) = frame.shape[:2]
    human_detected = False
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > DETECTION_CONFIDENCE:  # Adjust threshold as needed
            idx = int(detections[0, 0, i, 1])
            if idx == 15:
                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                (startX, startY, endX, endY) = box.astype("int")
                human_detected = True
                logging.info(
                    f"Human detected: Box = ({startX}, {startY}, {endX}, {endY})"
                )
                logging.info(f"Detection {i}: Confidence = {confidence}")
                cv2.rectangle(frame, (startX, startY), (endX, endY), (0, 255, 0), 2)
    return human_detected, frame


def rabbitmq_connection(queue_name):
    # Fetch RabbitMQ connection details from environment variables
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "")
    rabbitmq_user = os.getenv("RABBITMQ_USER", "")
    rabbitmq_password = os.getenv("RABBITMQ_PASS", "")

    # Create RabbitMQ credentials
    credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_password)

    # Set up connection parameters
    connection_params = pika.ConnectionParameters(
        host=rabbitmq_host, credentials=credentials
    )

    try:
        # Establish connection and channel
        connection = pika.BlockingConnection(connection_params)
        channel = connection.channel()

        # Declare the queues
        channel.queue_declare(queue=queue_name, durable=True)  # frame queue
        channel.queue_declare(queue="alert_queue", durable=True)  # alert queue

        print(f"Connected to RabbitMQ at {rabbitmq_host}, queue: {queue_name}")
        return connection, channel
    except pika.exceptions.ProbableAuthenticationError:
        print("Authentication failed! Check RabbitMQ credentials.")
        raise
    except Exception as e:
        print(f"Failed to connect to RabbitMQ: {e}")
        raise


# Consume frames from the queue
def consume_frames(queue_name):
    def connect_rabbitmq():
        while True:
            try:
                connection, channel = rabbitmq_connection("frame_queue")
                logging.info("Connected to RabbitMQ.")
                return connection, channel
            except Exception as e:
                logging.error(
                    f"Failed to connect to RabbitMQ: {e}. Retrying in 5 seconds..."
                )
                time.sleep(5)

    net = load_mobilenet_ssd()
    connection, channel = connect_rabbitmq()

    if not os.path.exists("captures"):
        os.makedirs("captures")
        logging.info("capture directory created!")
    else:
        logging.info("capture directory exists!")

    def callback(ch, method, properties, body):
        try:
            payload = json.loads(body.decode('utf-8'))
            byte_data = base64.b64decode(payload["image"])
            expected_hash = payload["hash"]

            actual_hash = hashlib.sha256(byte_data).hexdigest()
            if actual_hash != expected_hash:
                logging.warning("Hash mismatch! Frame may be corrupted.")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return
            
            frame_np = np.frombuffer(byte_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_np, cv2.IMREAD_COLOR)
            if frame is not None:
                # Processing frame...
                logging.info(">->->->->->->->")
                logging.info("Processing frame...")
                
                # Save frame locally with human-readable timestamp (hour, minute, second, microsecond)
                # now = datetime.now()
                # date_str = now.strftime("%Y-%m-%d")
                # time_str = now.strftime("%H-%M-%S-%f")  # e.g., 05-00-34-123456
                # camera_id = payload.get("camera", "unknown")
                # date_dir = f"/app/captures/camera_{camera_id}/{date_str}"
                # if not os.path.exists(date_dir):
                #     os.makedirs(date_dir, exist_ok=True)
                # cv2.imwrite(f"{date_dir}/frame_{time_str}.png", frame)  # Save frame locally
                
                human_detected, processed_frame = detect_human_mobilenet_ssd(net, frame)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if human_detected:
                    alert_message = f"Human detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    logging.info("Publishing alert to alert_queue...")
                    channel.basic_publish(
                        exchange="", routing_key="alert_queue", body=alert_message
                    )

                    date_only = timestamp.split()[0]
                    camera_id = payload.get("camera", "unknown")
                    detection_dir = os.path.join(f"/app/captures/camera_{camera_id}", date_only)
                    if not os.path.exists(detection_dir):
                        os.makedirs(detection_dir, exist_ok=True)
                        logging.info(f"{detection_dir} directory created!")

                    # Save the frame with detected human
                    filename = f"{detection_dir}/detection_{timestamp}.jpg"
                    cv2.imwrite(filename, processed_frame)
                    logging.info(f"Saved detection frame to {filename}")

                logging.info("Processing done!")
                # Acknowledge manually
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logging.info("Frame acknowledged!")
                logging.info("#-#-#-#-#-#-#-#")

            else:
                logging.warning("Failed to decode frame.")
        except Exception as e:
            logging.error(f"Error processing frame: {e}")

    try:
        channel.basic_consume(
            queue=queue_name, on_message_callback=callback, auto_ack=False
        )
        channel.start_consuming()
        logging.info("Waiting for frames...")
    except Exception as e:
        logging.error(f"Consumer failed with error: {e}")
    finally:
        connection.close()


if __name__ == "__main__":
    DETECTION_CONFIDENCE = float(os.getenv("DETECTION_CONFIDENCE"))
    consume_frames(queue_name="frame_queue")
