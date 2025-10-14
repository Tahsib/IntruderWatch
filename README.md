# IntruderWatch

IntruderWatch is a lightweight camera-frame ingestion and human-detection prototype built as small microservices. It captures frames from RTSP cameras, publishes frames over RabbitMQ, and runs a human detector that saves per-camera detection images and publishes alerts.

This repository contains:
- `microservices/frame_capturer/` 
  - captures frames from RTSP using `ffmpeg` (image2pipe), encodes frames and publishes to RabbitMQ.
- `microservices/human_detector/` 
  - consumes frame messages, runs MobileNet-SSD to detect humans, saves detection images and publishes alerts.
- `microservices/alert_service/` 
  - (simple) alert consumer used for integrations/notifications.
- `monolith/` 
  - an older monolithic detector (kept for reference).

Goals:
- Reliable RTSP ingestion (supports camera auth) using ffmpeg.
- Throttle to ~1 frame per second to reduce RAM/CPU footprint.
- Per-camera payloads and per-camera saved detections.

---

## Components & important files

- `microservices/frame_capturer/frame_capturer.py`
  - Uses `ffmpeg` (`image2pipe`) to read raw frames and request `-vf fps=1` to reduce duplicates.
  - Encodes frames (PNG by default), computes SHA-256, and publishes JSON payload to RabbitMQ with fields: `camera`, `hash`, `image` (base64).
  - Environment-driven (see env vars below) and writes to `CAPTURES_DIR` when configured.

- `microservices/human_detector/human_detector.py`
  - Consumes `frame_queue`, validates hash, decodes image, runs MobileNet-SSD, and writes detection frames to `CAPTURES_DIR/camera_<id>/<YYYY-MM-DD>/`.
  - Default detection confidence can be set via `DETECTION_CONFIDENCE`.

- `microservices/alert_service/alert_service.py`
  - Example consumer that listens on `alert_queue` to handle alerts.

---

## Recommended environment variables

Common env vars used across services (summarized):

- RabbitMQ
  - `RABBITMQ_HOST` 
  - RabbitMQ hostname (e.g., `rabbitmq`)
  - `RABBITMQ_USER` 
  - username
  - `RABBITMQ_PASS` 
  - password

- Frame capturer
  - `STREAM_IP`, `STREAM_USERNAME`, `STREAM_PASSWORD` 
  - camera connection info
  - `CHANNEL` 
  - camera channel (integer)
  - `SUBTYPE` 
  - stream subtype (integer)
  - `FRAME_WIDTH`, `FRAME_HEIGHT` 
  - expected frame resolution (used for reshape)
  - `FRAME_SLEEP` 
  - additional sleep after each processed frame (default `1.0`)
  - `START_TIME`, `END_TIME` 
  - active capture window (HH:MM:SS)
  - `FRAME_QUEUE_NAME` 
  - queue name to publish frames to (defaults to `frame_queue`)

- Human detector
  - `DETECTION_CONFIDENCE` 
  - float threshold, default `0.5` if not set
  - `CAPTURES_DIR` 
  - base dir for saving frames/detections (default `/app/captures`)

Note: `FRAME_WIDTH` and `FRAME_HEIGHT` must match the camera stream resolution. If these are wrong the code will attempt to reshape the raw frame buffer and fail.

---

## Docker / Docker Compose (example)

This repo includes Dockerfiles for the microservices, but does not ship a top-level `docker-compose.yml`. Below is a minimal example you can copy and adapt to run the services locally.

Save the following to `docker-compose.local.yml` in the repo root:

```yaml
version: '3.8'
services:
  rabbitmq:
    image: rabbitmq:3-management
    ports:
      - '5672:5672'
      - '15672:15672'

  frame_capturer:
    build: ./microservices/frame_capturer
    environment:
      - RABBITMQ_HOST=rabbitmq
      - RABBITMQ_USER=guest
      - RABBITMQ_PASS=guest
      - STREAM_IP=192.168.1.100
      - STREAM_USERNAME=admin
      - STREAM_PASSWORD=pass
      - CHANNEL=1
      - SUBTYPE=0
      - FRAME_WIDTH=1280
      - FRAME_HEIGHT=720
      - FRAME_QUEUE_NAME=frame_queue
      - CAPTURES_DIR=/app/captures
    volumes:
      - ./cap_local:/app/captures
    depends_on:
      - rabbitmq

  human_detector:
    build: ./microservices/human_detector
    environment:
      - RABBITMQ_HOST=rabbitmq
      - RABBITMQ_USER=guest
      - RABBITMQ_PASS=guest
      - DETECTION_CONFIDENCE=0.5
      - CAPTURES_DIR=/app/captures
    volumes:
      - ./cap_local:/app/captures
    depends_on:
      - rabbitmq

  alert_service:
    build: ./microservices/alert_service
    environment:
      - RABBITMQ_HOST=rabbitmq
      - RABBITMQ_USER=guest
      - RABBITMQ_PASS=guest
    depends_on:
      - rabbitmq

networks:
  default:
    driver: bridge
```

Run locally (example):

```bash
# build images
docker compose -f docker-compose.local.yml build

# start services
docker compose -f docker-compose.local.yml up
```

Notes: adjust `STREAM_IP`, credentials, and `FRAME_WIDTH`/`FRAME_HEIGHT` to match your camera. The example mounts `./cap_local` to persist captured/detection frames on the host.

---

## How to test (smoke test)

1. Prepare `docker-compose.local.yml` (see above), set camera credentials and resolution.
2. Run `docker compose -f docker-compose.local.yml up --build`.
3. Watch logs for `frame_capturer` to show "FFmpeg process started" and periodic "Frame sent to queue." messages (~1x/s).
4. Watch `human_detector` logs for detection messages. If a human is detected, the detector will save a JPEG under `cap_local/camera_<id>/<YYYY-MM-DD>/`.
5. Confirm the `cap_local` directory contains per-camera dated folders and detection images.

Quick shell checks:

```bash
# show running containers
docker ps

# tail logs for a service
docker compose -f docker-compose.local.yml logs -f frame_capturer

# list saved detections
ls -R cap_local | sed -n '1,200p'
```

---

## Troubleshooting & notes

- Frame reshape errors: ensure `FRAME_WIDTH`/`FRAME_HEIGHT` match the camera stream. If you don't know the resolution, try a conservative small one or probe the camera with ffprobe.
- Large message payloads: images are PNG-encoded by default (lossless and larger). To reduce bandwidth and RAM, consider switching to JPEG encoding with adjustable quality (TODO in repo).
- Disk growth: saved frames accumulate. Add a pruning job or cron to remove files older than N days.
- RTSP URL differences: some cameras require different RTSP URL formats. The capturer uses a common pattern, but you may need to adapt `frame_capturer.py` to your camera vendor.

---

## Next improvements (recommended)

- Switch PNG -> JPEG with configurable quality to reduce message size and memory.
- Add a pruning/rotation microservice to clean old frames.
- Add metrics (Prometheus) to monitor frames produced, published, and ffmpeg restarts.
- Add unit tests and CI checks for linting and basic static analysis.

---