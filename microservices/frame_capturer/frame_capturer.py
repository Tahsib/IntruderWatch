import subprocess
import cv2
import numpy as np
import os
import time
import pika
import logging
import hashlib
import base64
import json
from datetime import datetime
from shared.rabbitmq_client import connect_rabbitmq
from prometheus_client import start_http_server, Counter

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [capturer:%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Prometheus Metrics
FRAMES_CAPTURED = Counter(
    "frame_capturer_captured_total", "Total frames captured from stream", ["camera_id"]
)
FRAMES_SENT = Counter(
    "frame_capturer_sent_total", "Total frames sent to queue", ["camera_id"]
)
FRAMES_SKIPPED = Counter(
    "frame_capturer_skipped_total",
    "Total frames skipped (duplicate or rate-limit)",
    ["camera_id", "reason"],
)
CAPTURE_ERRORS = Counter(
    "frame_capturer_errors_total",
    "Total capture or processing errors",
    ["camera_id", "error_type"],
)

# Configuration
STREAM_IP = os.getenv("STREAM_IP")
STREAM_USERNAME = os.getenv("STREAM_USERNAME")
STREAM_PASSWORD = os.getenv("STREAM_PASSWORD")
STREAM_CHANNEL = int(os.getenv("CHANNEL", "1"))
STREAM_SUBTYPE = int(os.getenv("SUBTYPE", "0"))
FRAME_HEIGHT = int(os.getenv("FRAME_HEIGHT", "1080"))
FRAME_WIDTH = int(os.getenv("FRAME_WIDTH", "1920"))
FPS = int(os.getenv("FPS", "4"))
FRAME_SLEEP = float(os.getenv("FRAME_SLEEP", "0.05"))
START_TIME_ENV = os.getenv("START_TIME", "00:00:00")
END_TIME_ENV = os.getenv("END_TIME", "23:59:59")
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "85"))
# MSE threshold for "motion" vs "noise". 5.0 is typically safe for noisy nights.
MOTION_THRESHOLD = float(os.getenv("MOTION_THRESHOLD", "5.0"))


def is_within_time_frame(start_time, end_time):
    """Checks if current time is within the configured monitoring window."""
    now = datetime.now().time()
    if start_time <= end_time:
        return start_time <= now <= end_time
    return now >= start_time or now <= end_time


def capture_frames(ip, channel, stream, username, password, queue_name):
    logger = logging.getLogger(f"cam_{channel}")

    start_time = datetime.strptime(START_TIME_ENV, "%H:%M:%S").time()
    end_time = datetime.strptime(END_TIME_ENV, "%H:%M:%S").time()

    # Support for Custom RTSP URLs
    custom_url = os.getenv("CUSTOM_RTSP_URL")
    if custom_url:
        rtsp_url = custom_url
        logger.info(f"Using Custom RTSP URL for channel {channel}")
    else:
        rtsp_url = f"rtsp://{username}:{password}@{ip}:554/cam/realmonitor?channel={channel}&subtype={stream}"

    logger.info(
        f"Service initialized. Monitoring channel {channel} ({START_TIME_ENV} to {END_TIME_ENV})"
    )
    logger.info(
        f"Resolution: {FRAME_WIDTH}x{FRAME_HEIGHT}, FPS: {FPS}, Format: JPEG ({JPEG_QUALITY})"
    )

    last_sent_time = 0
    last_frame_gray = None
    frames_captured = 0
    frames_sent = 0
    frames_duplicate = 0
    frames_skipped = 0
    app_start_time = time.time()
    last_log_time = 0

    # Calculate minimal interval between frames based on FPS
    min_interval = (1.0 / FPS) * 0.9

    while True:
        pipe = None
        mq_connection = None
        ffmpeg_running = False
        try:
            frame_size = FRAME_WIDTH * FRAME_HEIGHT * 3
            mq_connection, mq_channel = connect_rabbitmq(queue_name)

            while True:
                # --- SCHEDULING CHECK ---
                if is_within_time_frame(start_time, end_time):
                    if not ffmpeg_running:
                        ffmpeg_cmd = [
                            "ffmpeg",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-nostats",
                            "-rtsp_transport",
                            "tcp",
                            "-thread_queue_size",
                            "1024",
                            "-probesize",
                            "10M",
                            "-analyzeduration",
                            "10M",
                            "-i",
                            rtsp_url,
                            "-vf",
                            f"fps={FPS},scale={FRAME_WIDTH}:{FRAME_HEIGHT}",
                            "-f",
                            "image2pipe",
                            "-pix_fmt",
                            "bgr24",
                            "-vcodec",
                            "rawvideo",
                            "-",
                        ]
                        pipe = subprocess.Popen(
                            ffmpeg_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            bufsize=frame_size,
                        )
                        ffmpeg_running = True
                        logger.info(
                            f"Stream connection established. Capture started at {FPS} FPS."
                        )

                    raw_frame = b""
                    while len(raw_frame) < frame_size:
                        chunk = pipe.stdout.read(frame_size - len(raw_frame))
                        if not chunk:
                            break
                        raw_frame += chunk

                    if len(raw_frame) != frame_size:
                        logger.error("Network sync lost. Reconnecting...")
                        CAPTURE_ERRORS.labels(
                            camera_id=channel, error_type="network_sync_lost"
                        ).inc()
                        try:
                            pipe.kill()
                            pipe.wait(timeout=1)
                        except Exception as kill_err:
                            logger.warning(f"Failed to kill ffmpeg pipe: {kill_err}")
                        pipe = None
                        ffmpeg_running = False
                        break

                    frames_captured += 1
                    FRAMES_CAPTURED.labels(camera_id=channel).inc()

                    if frames_captured % 100 == 0:
                        elapsed = time.time() - app_start_time
                        logger.info(
                            f"Heartbeat: {frames_sent}/{frames_captured} frames sent. Uptime: {int(elapsed)}s."
                        )

                    now_ts = time.time()
                    if now_ts - last_sent_time < min_interval:
                        frames_skipped += 1
                        FRAMES_SKIPPED.labels(
                            camera_id=channel, reason="rate_limit"
                        ).inc()
                        continue

                    frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape(
                        (FRAME_HEIGHT, FRAME_WIDTH, 3)
                    )

                    # --- MOTION FILTERING (THERMAL OPTIMIZATION) ---
                    # Ignore pixel-level noise (night grain) to save GPU energy
                    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if last_frame_gray is not None:
                        # Resize for extremely fast CPU comparison
                        small_current = cv2.resize(frame_gray, (160, 90))
                        small_last = cv2.resize(last_frame_gray, (160, 90))

                        # Mean Squared Error: determines if anything ACTUALLY moved
                        mse = np.mean(
                            (small_current.astype("float") - small_last.astype("float"))
                            ** 2
                        )

                        if mse < MOTION_THRESHOLD:
                            frames_duplicate += 1
                            FRAMES_SKIPPED.labels(
                                camera_id=channel, reason="duplicate"
                            ).inc()
                            last_sent_time = now_ts
                            continue

                    last_frame_gray = frame_gray

                    # Encode as JPEG
                    success, img_encode = cv2.imencode(
                        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                    )
                    if success:
                        byte_data = img_encode.tobytes()
                        img_hash = hashlib.sha256(byte_data).hexdigest()

                        payload = {
                            "camera": channel,
                            "hash": img_hash,
                            "image": base64.b64encode(byte_data).decode("utf-8"),
                        }
                        mq_channel.basic_publish(
                            exchange="",
                            routing_key=queue_name,
                            body=json.dumps(payload).encode("utf-8"),
                            properties=pika.BasicProperties(delivery_mode=2),
                        )
                        last_sent_time = now_ts
                        frames_sent += 1
                        FRAMES_SENT.labels(camera_id=channel).inc()
                    else:
                        logger.error("JPEG encoding failed!")
                        CAPTURE_ERRORS.labels(
                            camera_id=channel, error_type="encoding_failed"
                        ).inc()

                    time.sleep(FRAME_SLEEP)

                # --- OUTSIDE SCHEDULED HOURS ---
                else:
                    if ffmpeg_running:
                        try:
                            pipe.kill()
                            pipe.wait(timeout=1)
                        except Exception as kill_err:
                            logger.warning(
                                f"Failed to kill ffmpeg pipe during schedule sleep: {kill_err}"
                            )
                        pipe = None
                        ffmpeg_running = False
                        logger.info("Outside scheduled hours. Disconnected.")

                    if time.time() - last_log_time >= 3600:
                        logger.info(f"Service sleeping until {START_TIME_ENV}")
                        last_log_time = time.time()
                    time.sleep(60)
        except Exception as e:
            logger.error(f"Capturer encountered an error: {e}")
            time.sleep(10)
        finally:
            if pipe:
                pipe.terminate()
            if mq_connection:
                try:
                    mq_connection.close()
                except Exception as close_err:
                    logger.warning(f"Failed to close RabbitMQ connection: {close_err}")


if __name__ == "__main__":
    try:
        start_http_server(8001)
        logging.info("Prometheus metrics started on 8001")
    except Exception as e:
        logging.error(f"Failed to start metrics: {e}")

    capture_frames(
        ip=STREAM_IP,
        channel=STREAM_CHANNEL,
        stream=STREAM_SUBTYPE,
        username=STREAM_USERNAME,
        password=STREAM_PASSWORD,
        queue_name="frame_queue",
    )
