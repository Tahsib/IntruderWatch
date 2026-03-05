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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def is_within_time_frame(start_time, end_time):
    now = datetime.now().time()
    return start_time <= now <= end_time


def capture_frames(ip, channel, stream, username, password, queue_name, frame_width, frame_height):
    # Parse start and end times from environment variables
    start_time = datetime.strptime(
        os.getenv("START_TIME", "00:00:00"), "%H:%M:%S").time()
    end_time = datetime.strptime(
        os.getenv("END_TIME", "23:59:59"), "%H:%M:%S").time()
    last_log_time = 0
    FRAME_SLEEP = float(os.getenv("FRAME_SLEEP", "1.0"))  # seconds between frames

    #rtsp_url = f"rtsp://{ip}:554/user={username}&password={password}&channel={channel}&stream={stream}.sdp"
    rtsp_url = f"rtsp://{username}:{password}@{ip}:554/cam/realmonitor?channel={channel}&subtype={stream}"
    logging.info(f"Attempting to connect to: {rtsp_url}")

    while True:
        pipe = None
        connection = None
        ffmpeg_running = False
        try:
            width, height = frame_width, frame_height  # adjust to your stream resolution
            frame_size = width * height * 3
            mq_connection, mq_channel = connect_rabbitmq(queue_name, frame_max=131072)
            last_sent_time = 0
            while True:
                if is_within_time_frame(start_time, end_time):
                    if not ffmpeg_running:
                        # Start ffmpeg in quiet mode to avoid progress lines mixing with app logs
                        # request ffmpeg to output 1 frame per second (fps=1) to reduce processing
                        ffmpeg_cmd = [
                            "ffmpeg",
                            "-hide_banner",
                            "-loglevel", "error",
                            "-nostats",
                            "-i", rtsp_url,
                            "-vf", "fps=1",
                            "-f", "image2pipe",
                            "-pix_fmt", "bgr24",
                            "-vcodec", "rawvideo",
                            "-"
                        ]
                        pipe = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=4096)
                        ffmpeg_running = True
                        logging.info("FFmpeg process started for RTSP stream.")
                    raw_frame = pipe.stdout.read(frame_size)
                    if len(raw_frame) != frame_size:
                        logging.error("Incomplete frame received. Reinitializing capture...")
                        try:
                            pipe.kill()
                            pipe.wait(timeout=1)
                        except Exception:
                            pass
                        pipe = None
                        ffmpeg_running = False
                        break
                    frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3))
                    # ensure we send at most one frame per second regardless of what ffmpeg outputs
                    now_ts = time.time()
                    if now_ts - last_sent_time < 1.0:
                        # skip this frame
                        # small sleep to avoid tight loop if ffmpeg produces faster
                        time.sleep(0.01)
                        continue
                    success, img_encode = cv2.imencode('.png', frame)
                    
                    # Save frame locally with human-readable timestamp (hour, minute, second, microsecond)
                    # now = datetime.now()
                    # date_str = now.strftime("%Y-%m-%d")
                    # time_str = now.strftime("%H-%M-%S-%f")  # e.g., 05-00-34-123456
                    # date_dir = f"/app/captures/camera_{channel}/{date_str}"
                    # if not os.path.exists(date_dir):
                    #     os.makedirs(date_dir, exist_ok=True)
                    # cv2.imwrite(f"{date_dir}/frame_{time_str}.png", frame)  # Save frame locally
                    
                    if success:
                        byte_data = img_encode.tobytes()
                        hash_val = hashlib.sha256(byte_data).hexdigest()
                        payload = {
                            "camera": channel,
                            "hash": hash_val,
                            "image": base64.b64encode(byte_data).decode('utf-8')
                        }
                        message = json.dumps(payload)
                        mq_channel.basic_publish(exchange='', routing_key=queue_name, body=message.encode('utf-8'), properties=pika.BasicProperties(delivery_mode=2))
                        last_sent_time = now_ts
                        logging.info("Frame sent to queue.")
                    else:
                        logging.error("Frame encoding failed!!")
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
                        logging.info("FFmpeg process terminated (outside time frame).")
                    current_time = time.time()
                    if current_time - last_log_time >= 30 * 60:
                        logging.info(f"Outside of active hours ({start_time} to {end_time}). Waiting...")
                        last_log_time = current_time
                    time.sleep(60)
        except Exception as e:
            logging.error(f"Capture failed with error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        finally:
            if pipe is not None:
                pipe.terminate()
                pipe = None
                logging.info("FFmpeg process terminated.")
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
