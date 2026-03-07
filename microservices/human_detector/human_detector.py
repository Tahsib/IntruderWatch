import cv2
import numpy as np
import time
import os
import logging
import hashlib
import base64
import json
import socket
from datetime import datetime
from ultralytics import YOLO
from shared.rabbitmq_client import connect_rabbitmq

# Get the container hostname to identify the detector instance
INSTANCE_ID = socket.gethostname()

# Per-camera last hash to prevent saving identical frames (Global for this worker)
last_saved_hashes = {}

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=f"%(asctime)s [worker:{INSTANCE_ID[:6]}] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def detect_humans(model, frame, confidence_threshold):
    results = model(frame, classes=[0], conf=confidence_threshold, verbose=False)[0]
    human_detected = False
    for box in results.boxes:
        human_detected = True
        x1, y1, x2, y2 = box.xyxy[0].int().tolist()
        conf = box.conf[0].item()
        logging.info(f"Human detected: Box = ({x1}, {y1}, {x2}, {y2}), Conf = {conf:.4f}")
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    return human_detected, frame


# Consume frames from the queue
def consume_frames(queue_name):
    model = YOLO("yolov8n.pt")
    connection, channel = connect_rabbitmq(["frame_queue", "alert_queue"])
    
    # Set prefetch count so frames are distributed more evenly among the 8 replicas
    channel.basic_qos(prefetch_count=1)
    
    # Statistics for heartbeat
    frame_counter = 0
    start_time = time.time()

    if not os.path.exists("captures"):
        os.makedirs("captures")
        logging.info("capture directory created!")
    else:
        logging.info("capture directory exists!")

    def callback(ch, method, properties, body):
        nonlocal frame_counter
        try:
            payload = json.loads(body.decode('utf-8'))
            camera_id = payload.get("camera", "unknown")
            expected_hash = payload.get("hash", "")

            # Heartbeat logic: log every 100 frames processed by THIS worker
            frame_counter += 1
            if frame_counter % 100 == 0:
                elapsed = time.time() - start_time
                logging.info(f"Heartbeat: Processed {frame_counter} frames. Active for {int(elapsed)}s.")

            # Deduplication: don't re-process identical frames for same camera
            if last_saved_hashes.get(camera_id) == expected_hash:
                logging.debug(f"Skipping duplicate frame {expected_hash[:8]} from camera {camera_id}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            byte_data = base64.b64decode(payload["image"])

            # Validate hash
            actual_hash = hashlib.sha256(byte_data).hexdigest()
            if actual_hash != expected_hash:
                logging.warning(f"Hash mismatch! Frame may be corrupted.")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            # Decode frame
            frame_np = np.frombuffer(byte_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_np, cv2.IMREAD_COLOR)

            # Validate frame was decoded successfully
            if frame is None:
                logging.error(f"cv2.imdecode failed - frame is corrupted or incomplete.")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            # NOW perform slow YOLO processing
            logging.debug(f"Processing frame from camera {camera_id} with hash {expected_hash[:8]}...")

            human_detected, processed_frame = detect_humans(model, frame, DETECTION_CONFIDENCE)
            timestamp_dt = datetime.now()
            timestamp = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

            if human_detected:
                # Store this hash to prevent re-processing the same image
                last_saved_hashes[camera_id] = expected_hash
                
                alert_payload = json.dumps({
                    "camera": camera_id,
                    "timestamp": timestamp,
                })
                logging.info(f"*** HUMAN DETECTED (Camera {camera_id}) *** Publishing alert...")
                channel.basic_publish(
                    exchange="", routing_key="alert_queue", body=alert_payload
                )

                date_only = timestamp.split()[0]
                detection_dir = os.path.join(f"/app/captures/camera_{camera_id}", date_only)
                if not os.path.exists(detection_dir):
                    os.makedirs(detection_dir, exist_ok=True)
                    logging.info(f"{detection_dir} directory created!")

                # Save the frame with detected human, the hash, and the worker instance ID in filename
                filename = f"{detection_dir}/det_{timestamp}_{expected_hash[:8]}_{INSTANCE_ID[:6]}.png"
                success = cv2.imwrite(filename, processed_frame)
                if success:
                    logging.info(f"Saved detection frame to {filename}")
                else:
                    logging.error(f"Failed to save frame to {filename}")

            logging.debug("Processing done!")
            
            # Acknowledge ONLY after successful processing
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            logging.error(f"Error processing frame: {e}")
            # Still acknowledge so message doesn't pile up
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except:
                pass

    try:
        channel.basic_consume(
            queue=queue_name, on_message_callback=callback, auto_ack=False
        )
        logging.info("Waiting for frames...")
        channel.start_consuming()
    except Exception as e:
        logging.error(f"Consumer failed with error: {e}")
    finally:
        connection.close()


if __name__ == "__main__":
    DETECTION_CONFIDENCE = float(os.getenv("DETECTION_CONFIDENCE", "0.8"))
    consume_frames(queue_name="frame_queue")
