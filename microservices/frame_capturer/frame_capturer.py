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

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [capturer:%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def is_within_time_frame(start_time, end_time):
    now = datetime.now().time()
    if start_time <= end_time:
        # Standard case: 08:00 to 18:00
        return start_time <= now <= end_time
    else:
        # Overnight case: 22:00 to 05:00
        return now >= start_time or now <= end_time


def capture_frames(ip, channel, stream, username, password, queue_name, frame_width, frame_height):
    # Set logger name to include camera channel for easier identification
    logger = logging.getLogger(f"cam_{channel}")
    
    # Parse start and end times from environment variables
    start_time_env = os.getenv("START_TIME", "00:00:00")
    end_time_env = os.getenv("END_TIME", "23:59:59")
    start_time = datetime.strptime(start_time_env, "%H:%M:%S").time()
    end_time = datetime.strptime(end_time_env, "%H:%M:%S").time()
    
    last_log_time = 0
    FRAME_SLEEP = float(os.getenv("FRAME_SLEEP", "1.0"))  # seconds between frames

    rtsp_url = f"rtsp://{username}:{password}@{ip}:554/cam/realmonitor?channel={channel}&subtype={stream}"
    logger.info(f"Service initialized. Monitoring channel {channel} ({start_time_env} to {end_time_env})")

    # Persistent tracking across reconnects
    last_sent_time = 0
    last_sent_hash = None
    
    # Statistics for heartbeat
    frames_captured = 0
    frames_sent = 0
    frames_duplicate = 0
    frames_skipped = 0
    app_start_time = time.time()

    while True:
        pipe = None
        mq_connection = None
        ffmpeg_running = False
        try:
            width, height = frame_width, frame_height
            frame_size = width * height * 3
            mq_connection, mq_channel = connect_rabbitmq(queue_name)
            
            while True:
                if is_within_time_frame(start_time, end_time):
                    if not ffmpeg_running:
                        ffmpeg_cmd = [
                            "ffmpeg",
                            "-hide_banner",
                            "-loglevel", "error",
                            "-nostats",
                            "-rtsp_transport", "tcp",
                            "-thread_queue_size", "1024",
                            "-probesize", "10M",
                            "-analyzeduration", "10M",
                            "-i", rtsp_url,
                            "-vf", f"fps=1,scale={width}:{height}",
                            "-f", "image2pipe",
                            "-pix_fmt", "bgr24",
                            "-vcodec", "rawvideo",
                            "-"
                        ]
                        pipe = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_size)
                        ffmpeg_running = True
                        logger.info(f"Stream connection established (TCP). Capture started.")
                    
                    raw_frame = b""
                    while len(raw_frame) < frame_size:
                        chunk = pipe.stdout.read(frame_size - len(raw_frame))
                        if not chunk:
                            break
                        raw_frame += chunk
                    
                    if len(raw_frame) != frame_size:
                        logger.error("Network sync lost (incomplete frame). Reconnecting stream...")
                        try:
                            pipe.kill()
                            pipe.wait(timeout=1)
                        except Exception:
                            pass
                        pipe = None
                        ffmpeg_running = False
                        break
                    
                    frames_captured += 1
                    frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3))
                    raw_hash = hashlib.sha256(raw_frame).hexdigest()

                    # Heartbeat log every 100 frames
                    if frames_captured % 100 == 0:
                        elapsed = time.time() - app_start_time
                        logger.info(f"Heartbeat: Captured {frames_captured}, Sent {frames_sent}, Duplicates {frames_duplicate}, Rate-limited {frames_skipped}. Uptime: {int(elapsed)}s.")

                    now_ts = time.time()
                    if now_ts - last_sent_time < 1.0:
                        frames_skipped += 1
                        time.sleep(0.01)
                        continue

                    if raw_hash == last_sent_hash:
                        frames_duplicate += 1
                        logger.debug(f"Duplicate frame suppressed ({raw_hash[:8]})")
                        last_sent_time = now_ts
                        continue

                    success, img_encode = cv2.imencode('.png', frame)
                    if success:
                        byte_data = img_encode.tobytes()
                        # Calculate hash on the actual data being sent (PNG bytes)
                        png_hash = hashlib.sha256(byte_data).hexdigest()
                        
                        payload = {
                            "camera": channel,
                            "hash": png_hash,
                            "image": base64.b64encode(byte_data).decode('utf-8')
                        }
                        message = json.dumps(payload)
                        mq_channel.basic_publish(
                            exchange='', 
                            routing_key=queue_name, 
                            body=message.encode('utf-8'), 
                            properties=pika.BasicProperties(delivery_mode=2)
                        )
                        last_sent_time = now_ts
                        last_sent_hash = raw_hash # Keep tracking raw_hash for duplicate detection
                        frames_sent += 1
                        logger.debug(f"Frame sent to queue (hash: {png_hash[:8]})")
                    else:
                        logger.error("Image encoding failed!")
                    
                    time.sleep(FRAME_SLEEP)
                else:
                    if ffmpeg_running:
                        try:
                            pipe.kill()
                            pipe.wait(timeout=1)
                        except Exception:
                            pass
                        pipe = None
                        ffmpeg_running = False
                        logger.info("Outside of scheduled hours. Stream disconnected.")
                    
                    current_time = time.time()
                    if current_time - last_log_time >= 3600: # Log once per hour while sleeping
                        logger.info(f"Service sleeping (Schedule: {start_time_env} to {end_time_env})")
                        last_log_time = current_time
                    time.sleep(60)
        except Exception as e:
            logger.error(f"Capturer encountered a fatal error: {e}")
            time.sleep(10)
        finally:
            if pipe is not None:
                pipe.terminate()
                pipe = None
            if mq_connection is not None:
                try:
                    mq_connection.close()
                except Exception:
                    pass


if __name__ == "__main__":
    stream_ip = os.getenv("STREAM_IP")
    stream_username = os.getenv("STREAM_USERNAME")
    stream_password = os.getenv("STREAM_PASSWORD")
    stream_channel = int(os.getenv("CHANNEL"))
    stream_subtype = int(os.getenv("SUBTYPE"))
    frame_height = int(os.getenv("FRAME_HEIGHT", "720"))
    frame_width = int(os.getenv("FRAME_WIDTH", "1280"))

    capture_frames(
        ip=stream_ip,
        channel=stream_channel,
        stream=stream_subtype,
        username=stream_username,
        password=stream_password,
        queue_name="frame_queue",
        frame_width=frame_width,
        frame_height=frame_height
    )
