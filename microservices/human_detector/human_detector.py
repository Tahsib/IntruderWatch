import cv2
import numpy as np
import time
import os
import logging
import hashlib
import base64
import json
import socket
import gc
import threading
import torch
from datetime import datetime
from ultralytics import YOLO
from shared.rabbitmq_client import connect_rabbitmq
from prometheus_client import start_http_server, Counter, Histogram, Gauge

# Get the container hostname
INSTANCE_ID = socket.gethostname()

# Per-camera last hash to prevent re-processing
last_saved_hashes = {}

# Warm-up tracker: Stores how many frames we've seen per camera since startup
# We skip the first 30 frames (~5s) to allow RTSP/ffmpeg to stabilize
camera_warmup = {}
WARMUP_LIMIT = 30

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=f"%(asctime)s [detector:{INSTANCE_ID[:6]}] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Prometheus Metrics
FRAMES_PROCESSED = Counter('human_detector_frames_processed_total', 'Total frames processed', ['camera_id', 'worker_id'])
HUMANS_DETECTED = Counter('human_detector_humans_detected_total', 'Total humans detected', ['camera_id', 'worker_id'])
PROCESSING_TIME = Histogram('human_detector_processing_seconds', 'Time spent processing a frame', ['camera_id', 'worker_id'])
ERRORS_TOTAL = Counter('human_detector_errors_total', 'Total processing errors', ['camera_id', 'worker_id', 'error_type'])
ACTIVE_PROCESSING = Gauge('human_detector_active_processing', 'Number of frames currently being processed', ['worker_id'])

# Configuration
DETECTION_CONFIDENCE = float(os.getenv("DETECTION_CONFIDENCE", "0.8"))
SAVE_QUALITY = int(os.getenv("SAVE_QUALITY", "85"))
INFERENCE_SIZE = int(os.getenv("INFERENCE_SIZE", "1600"))

# Shared state class
class DetectionState:
    def __init__(self):
        self.frame_counter = 0
        self.start_time = time.time()
        self.last_frame_time = time.time()

def memory_manager(state):
    """Background thread to release RAM/VRAM during idle periods."""
    while True:
        time.sleep(300) # Check every 5 minutes
        idle_duration = time.time() - state.last_frame_time
        if idle_duration > 300:
            logging.info(f"AI idle for {int(idle_duration)}s. Parking memory...")
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logging.info("Memory parked successfully.")

def consume_frames(queue_name):
    state = DetectionState()
    
    # Staggered initialization: Prevent multiple processes from hitting the GPU at once
    # We use a deterministic delay based on the container hostname (INSTANCE_ID)
    try:
        instance_num = int(INSTANCE_ID.split('_')[-1])
    except Exception:
        try:
            instance_num = int(INSTANCE_ID.split('-')[-1])
        except Exception:
            import random
            instance_num = random.randint(1, 5)
    
    init_delay = (instance_num - 1) * 3
    logging.info(f"Staggered start: Waiting {init_delay}s to initialize GPU...")
    time.sleep(init_delay)

    # Start the memory manager thread
    threading.Thread(target=memory_manager, args=(state,), daemon=True).start()

    # Load model once - Using YOLO11 MEDIUM for a balance of speed and accuracy
    model = YOLO("yolo11m.pt")
    connection, channel = connect_rabbitmq(["frame_queue", "alert_queue"])
    channel.basic_qos(prefetch_count=1)

    if not os.path.exists("captures"):
        os.makedirs("captures")
        logging.info("Captures directory initialized.")

    def callback(ch, method, properties, body):
        camera_id = "unknown"
        state.last_frame_time = time.time()
        try:
            payload = json.loads(body.decode('utf-8'))
            camera_id = payload.get("camera", "unknown")
            expected_hash = payload.get("hash", "")

            # Warm-up Logic: Skip initial frames after restart to avoid macroblocking noise
            current_count = camera_warmup.get(camera_id, 0)
            if current_count < WARMUP_LIMIT:
                camera_warmup[camera_id] = current_count + 1
                logging.debug(f"Warming up Cam {camera_id}: skipping frame {camera_warmup[camera_id]}/{WARMUP_LIMIT}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            state.frame_counter += 1
            if state.frame_counter % 100 == 0:
                elapsed = time.time() - state.start_time
                logging.info(f"Heartbeat: Processed {state.frame_counter} frames. Uptime: {int(elapsed)}s.")

            # Deduplication
            if last_saved_hashes.get(camera_id) == expected_hash:
                logging.debug(f"Skipping duplicate {expected_hash[:8]} (Cam {camera_id})")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            byte_data = base64.b64decode(payload["image"])
            
            # Simple hash check
            actual_hash = hashlib.sha256(byte_data).hexdigest()
            if actual_hash != expected_hash:
                logging.warning(f"Hash mismatch for camera {camera_id}!")
                ERRORS_TOTAL.labels(camera_id=camera_id, worker_id=INSTANCE_ID, error_type="hash_mismatch").inc()
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            # Decode JPEG
            frame_np = np.frombuffer(byte_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_np, cv2.IMREAD_COLOR)

            if frame is None:
                logging.error(f"Failed to decode image from camera {camera_id}")
                ERRORS_TOTAL.labels(camera_id=camera_id, worker_id=INSTANCE_ID, error_type="decode_error").inc()
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            with PROCESSING_TIME.labels(camera_id=camera_id, worker_id=INSTANCE_ID).time():
                with ACTIVE_PROCESSING.labels(worker_id=INSTANCE_ID).track_inprogress():
                    # Optimized inference: INFERENCE_SIZE (1280) on GPU (device=0)
                    # Using half=True (FP16) to double speed and prevent GPU hangs
                    results = model(frame, classes=[0], conf=DETECTION_CONFIDENCE, imgsz=INFERENCE_SIZE, device=0, verbose=False, half=True)[0]
                    
                    human_detected = False
                    for box in results.boxes:
                        human_detected = True
                        x1, y1, x2, y2 = box.xyxy[0].int().tolist()
                        conf = box.conf[0].item()
                        logging.debug(f"Human detected: Box = ({x1}, {y1}, {x2}, {y2}), Conf = {conf:.4f}")
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    
                    if human_detected:
                        last_saved_hashes[camera_id] = expected_hash
                        timestamp_dt = datetime.now()
                        timestamp = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
                        
                        date_only = timestamp.split()[0]
                        detection_dir = os.path.join(f"/app/captures/camera_{camera_id}", date_only)
                        os.makedirs(detection_dir, exist_ok=True)

                        # Save as JPEG (faster and smaller than PNG)
                        filename = f"{detection_dir}/det_{timestamp}_{expected_hash[:8]}_{INSTANCE_ID[:6]}.jpg"
                        success = cv2.imwrite(filename, frame, [cv2.IMWRITE_JPEG_QUALITY, SAVE_QUALITY])
                        
                        # Add filename to payload for the alert service
                        alert_payload = json.dumps({"camera": camera_id, "timestamp": timestamp, "filename": filename})
                        channel.basic_publish(exchange="", routing_key="alert_queue", body=alert_payload)
                        HUMANS_DETECTED.labels(camera_id=camera_id, worker_id=INSTANCE_ID).inc()

                        if success:
                            logging.info(f"*** HUMAN DETECTED (Cam {camera_id}) *** Saved to {filename}")
                        else:
                            logging.error(f"Failed to save detection for camera {camera_id}")

            FRAMES_PROCESSED.labels(camera_id=camera_id, worker_id=INSTANCE_ID).inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            logging.error(f"Error processing frame: {e}")
            ERRORS_TOTAL.labels(camera_id=camera_id, worker_id=INSTANCE_ID, error_type="exception").inc()
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                pass

    try:
        channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=False)
        logging.info("Human detector waiting for frames...")
        channel.start_consuming()
    except Exception as e:
        logging.error(f"Consumer error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    try:
        start_http_server(8000)
        logging.info("Prometheus metrics started on 8000")
    except Exception as e:
        logging.error(f"Failed to start metrics: {e}")

    consume_frames(queue_name="frame_queue")
