import cv2
import numpy as np
import time
import os
import logging
import hashlib
import base64
import json
from datetime import datetime
from ultralytics import YOLO
from shared.rabbitmq_client import connect_rabbitmq

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def detect_humans(model, frame, confidence_threshold):
    results = model(frame, classes=[0], conf=confidence_threshold, verbose=False)[0]
    human_detected = False
    for box in results.boxes:
        human_detected = True
        x1, y1, x2, y2 = box.xyxy[0].int().tolist()
        conf = box.conf[0].item()
        logging.info(f"Human detected: Box = ({x1}, {y1}, {x2}, {y2})")
        logging.info(f"Confidence = {conf:.4f}")
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    return human_detected, frame


# Consume frames from the queue
def consume_frames(queue_name):
    model = YOLO("yolov8n.pt")
    connection, channel = connect_rabbitmq(["frame_queue", "alert_queue"])

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
                
                human_detected, processed_frame = detect_humans(model, frame, DETECTION_CONFIDENCE)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if human_detected:
                    camera_id = payload.get("camera", "unknown")
                    alert_payload = json.dumps({
                        "camera": camera_id,
                        "timestamp": timestamp,
                    })
                    logging.info("Publishing alert to alert_queue...")
                    channel.basic_publish(
                        exchange="", routing_key="alert_queue", body=alert_payload
                    )

                    date_only = timestamp.split()[0]
                    detection_dir = os.path.join(f"/app/captures/camera_{camera_id}", date_only)
                    if not os.path.exists(detection_dir):
                        os.makedirs(detection_dir, exist_ok=True)
                        logging.info(f"{detection_dir} directory created!")

                    # Save the frame with detected human
                    filename = f"{detection_dir}/detection_{timestamp}.png"
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
