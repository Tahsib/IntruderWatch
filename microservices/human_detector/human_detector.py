import base64
import gc
import json
import logging
import os
import queue
import socket
import threading
import time
from datetime import datetime
from wsgiref.simple_server import make_server

import cv2
import numpy as np
from prometheus_client import REGISTRY, Counter, Gauge, Histogram, make_wsgi_app
from shared.rabbitmq_client import connect_rabbitmq
from ultralytics import YOLO

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
logging.getLogger("pika").setLevel(logging.WARNING)

# Prometheus Metrics
FRAMES_PROCESSED = Counter(
    "human_detector_frames_processed_total",
    "Total frames processed",
    ["camera_id", "worker_id"],
)
HUMANS_DETECTED = Counter(
    "human_detector_humans_detected_total",
    "Total humans detected",
    ["camera_id", "worker_id"],
)
PROCESSING_TIME = Histogram(
    "human_detector_processing_seconds",
    "Time spent processing a frame",
    ["camera_id", "worker_id"],
)
ERRORS_TOTAL = Counter(
    "human_detector_errors_total",
    "Total processing errors",
    ["camera_id", "worker_id", "error_type"],
)
ACTIVE_PROCESSING = Gauge(
    "human_detector_active_processing",
    "Number of frames currently being processed",
    ["worker_id"],
)

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
        self.is_ready = False


class AlertDispatcher:
    """Background worker for non-blocking disk writes and alert queue publishing."""

    def __init__(self, save_quality: int, queue_size: int = 50):
        self.save_quality = save_quality
        self.queue = queue.Queue(maxsize=queue_size)
        self.connection = None
        self.channel = None
        self.thread = threading.Thread(target=self._worker, daemon=True, name="alert-dispatcher")

    def start(self):
        self.thread.start()

    def submit(self, frame: np.ndarray, filename: str, alert_payload: dict):
        try:
            self.queue.put_nowait((frame, filename, alert_payload))
        except queue.Full:
            logging.error("Alert dispatcher queue full; dropping alert to maintain real-time throughput.")

    def _reset_channel(self):
        try:
            if self.channel and not self.channel.is_closed:
                self.channel.close()
        except Exception:
            pass
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
        except Exception:
            pass
        self.channel = None
        self.connection = None

    def _ensure_channel(self):
        if self.connection is None or self.connection.is_closed or self.channel is None or self.channel.is_closed:
            logging.info("Connecting alert dispatcher to RabbitMQ...")
            self.connection, self.channel = connect_rabbitmq(["alert_queue"])

    def _publish_with_retry(self, alert_payload: dict, retries: int = 3) -> bool:
        body = json.dumps(alert_payload)
        for attempt in range(1, retries + 1):
            try:
                self._ensure_channel()
                self.channel.basic_publish(
                    exchange="",
                    routing_key="alert_queue",
                    body=body,
                )
                return True
            except Exception as pub_err:
                logging.warning(f"Publish to alert_queue failed (attempt {attempt}/{retries}): {pub_err}")
                self._reset_channel()
                if attempt < retries:
                    time.sleep(0.5)
        return False

    def _worker(self):
        while True:
            try:
                frame, filename, alert_payload = self.queue.get()
                try:
                    detection_dir = os.path.dirname(filename)
                    os.makedirs(detection_dir, exist_ok=True)
                    success = cv2.imwrite(filename, frame, [cv2.IMWRITE_JPEG_QUALITY, self.save_quality])
                    if not success:
                        logging.error(f"Failed to write image: {filename}")

                    published = self._publish_with_retry(alert_payload)
                    if not published:
                        logging.error(f"Failed to dispatch alert to queue after retries for {filename}")

                    camera_id = alert_payload.get("camera", "?")
                    count = alert_payload.get("human_count", 1)
                    conf = alert_payload.get("max_confidence", 0.0)
                    logging.info(
                        f"*** HUMAN DETECTED (Cam {camera_id}, Count: {count}, Conf: {conf:.2f}) *** Saved to {filename}"
                    )
                except Exception as e:
                    logging.error(f"Error saving or publishing alert: {e}")
                finally:
                    self.queue.task_done()
            except Exception as outer_e:
                logging.error(f"Unexpected error in alert dispatcher worker: {outer_e}")
                time.sleep(1)


def memory_manager(state):
    """Background thread to safely release RAM during idle periods without hanging ROCm."""
    parked = False
    while True:
        time.sleep(60)  # Check every minute
        idle_duration = time.time() - state.last_frame_time
        if idle_duration > 300:
            if not parked:
                logging.info(f"AI idle for {int(idle_duration)}s. Parking memory...")
                gc.collect()
                # Note: Avoid torch.cuda.empty_cache() from secondary daemon threads on ROCm,
                # as HIP cross-thread memory deallocation can trigger driver spinlock/deadlocks.
                logging.info("Memory parked successfully.")
                parked = True
        else:
            parked = False


def consume_frames(queue_name: str, state: DetectionState | None = None):
    if state is None:
        state = DetectionState()

    alert_dispatcher = AlertDispatcher(save_quality=SAVE_QUALITY)
    alert_dispatcher.start()

    # Staggered initialization: Prevent multiple processes from hitting the GPU at once
    # We use a deterministic delay based on the container hostname (INSTANCE_ID)
    try:
        instance_num = int(INSTANCE_ID.split("_")[-1])
    except Exception:
        try:
            instance_num = int(INSTANCE_ID.split("-")[-1])
        except Exception:
            import random

            instance_num = random.randint(1, 5)

    init_delay = (instance_num - 1) * 3
    logging.info(f"Staggered start: Waiting {init_delay}s to initialize GPU...")
    time.sleep(init_delay)

    # Start the memory manager thread
    threading.Thread(target=memory_manager, args=(state,), daemon=True).start()

    # Load model - Configurable via YOLO_MODEL env var (defaults to YOLO11 Medium)
    yolo_model_name = os.getenv("YOLO_MODEL", "yolo11m.pt")
    logging.info(f"Loading YOLO model: {yolo_model_name}")
    model = YOLO(yolo_model_name)
    connection, channel = connect_rabbitmq(["frame_queue", "alert_queue"])
    channel.basic_qos(prefetch_count=1)

    state.is_ready = True
    logging.info("Human detector initialized and ready for inference.")

    if not os.path.exists("captures"):
        os.makedirs("captures")
        logging.info("Captures directory initialized.")

    def callback(ch, method, properties, body):
        camera_id = "unknown"
        state.last_frame_time = time.time()
        try:
            payload = json.loads(body.decode("utf-8"))
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
            if expected_hash and last_saved_hashes.get(camera_id) == expected_hash:
                logging.debug(f"Skipping duplicate {expected_hash[:8]} (Cam {camera_id})")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            byte_data = base64.b64decode(payload["image"])

            # Decode JPEG
            frame_np = np.frombuffer(byte_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_np, cv2.IMREAD_COLOR)

            if frame is None:
                logging.error(f"Failed to decode image from camera {camera_id}")
                ERRORS_TOTAL.labels(
                    camera_id=camera_id,
                    worker_id=INSTANCE_ID,
                    error_type="decode_error",
                ).inc()
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            with PROCESSING_TIME.labels(camera_id=camera_id, worker_id=INSTANCE_ID).time():
                with ACTIVE_PROCESSING.labels(worker_id=INSTANCE_ID).track_inprogress():
                    # Optimized inference: INFERENCE_SIZE on GPU (device=0)
                    # Using half=True (FP16) to double speed and prevent GPU hangs
                    results = model(
                        frame,
                        classes=[0],
                        conf=DETECTION_CONFIDENCE,
                        imgsz=INFERENCE_SIZE,
                        device=0,
                        verbose=False,
                        half=True,
                    )[0]

                    boxes_data = []
                    for box in results.boxes:
                        x1, y1, x2, y2 = box.xyxy[0].int().tolist()
                        conf = float(box.conf[0].item())
                        boxes_data.append({"box": [x1, y1, x2, y2], "confidence": round(conf, 4)})
                        logging.debug(f"Human detected: Box = ({x1}, {y1}, {x2}, {y2}), Conf = {conf:.4f}")
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        label = f"Person {int(conf * 100)}%"
                        cv2.putText(
                            frame,
                            label,
                            (x1, max(y1 - 6, 15)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 255, 0),
                            1,
                            cv2.LINE_AA,
                        )

                    if boxes_data:
                        if expected_hash:
                            last_saved_hashes[camera_id] = expected_hash
                        timestamp_dt = datetime.now()
                        timestamp = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

                        date_only = timestamp.split()[0]
                        detection_dir = os.path.join(f"/app/captures/camera_{camera_id}", date_only)

                        # Save as JPEG (faster and smaller than PNG)
                        filename = f"{detection_dir}/det_{timestamp}_{expected_hash[:8]}_{INSTANCE_ID[:6]}.jpg"
                        max_conf = max(b["confidence"] for b in boxes_data)
                        alert_payload = {
                            "camera": camera_id,
                            "timestamp": timestamp,
                            "filename": filename,
                            "human_count": len(boxes_data),
                            "max_confidence": max_conf,
                            "boxes": boxes_data,
                        }

                        # Offload disk write and alert queue publishing to background thread
                        alert_dispatcher.submit(frame, filename, alert_payload)
                        HUMANS_DETECTED.labels(camera_id=camera_id, worker_id=INSTANCE_ID).inc(len(boxes_data))

            FRAMES_PROCESSED.labels(camera_id=camera_id, worker_id=INSTANCE_ID).inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            logging.error(f"Error processing frame: {e}")
            ERRORS_TOTAL.labels(camera_id=camera_id, worker_id=INSTANCE_ID, error_type="exception").inc()
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as ack_err:
                logging.warning(f"Failed to basic_ack after processing error: {ack_err}")

    try:
        channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=False)
        logging.info("Human detector waiting for frames...")
        channel.start_consuming()
    except Exception as e:
        logging.error(f"Consumer error: {e}")
    finally:
        state.is_ready = False
        try:
            connection.close()
        except Exception:
            pass


def start_http_service(port: int, state: DetectionState):
    """Starts WSGI server exposing Prometheus /metrics and /healthz liveness probe."""
    prom_app = make_wsgi_app(REGISTRY)

    def wsgi_app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path in ("/healthz", "/health"):
            if state.is_ready:
                start_response("200 OK", [("Content-Type", "text/plain")])
                return [b"OK\n"]
            else:
                start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
                return [b"INITIALIZING\n"]
        return prom_app(environ, start_response)

    server = make_server("0.0.0.0", port, wsgi_app)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="http-service")
    t.start()
    logging.info(f"Prometheus metrics and /healthz listening on port {port}")
    return server


if __name__ == "__main__":
    state = DetectionState()
    try:
        start_http_service(8000, state)
    except Exception as e:
        logging.error(f"Failed to start HTTP service: {e}")

    consume_frames(queue_name="frame_queue", state=state)
