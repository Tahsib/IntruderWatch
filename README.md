# IntruderWatch

IntruderWatch is a real-time intruder detection system with frame ingestion, human detection, and a web-based image viewer. Built as scalable microservices with RTSP camera capture, RabbitMQ message broker, and YOLOv8n for human detection.

## Repository Structure

**Microservices** (recommended):
- `microservices/frame_capturer/` - Captures frames from RTSP cameras via ffmpeg, publishes to RabbitMQ
- `microservices/human_detector/` - Consumes frames, runs YOLOv8n detection, saves detection images
- `microservices/alert_service/` - Consumes alerts and sends notifications (Twilio)
- `microservices/viewer_service/` - Web UI for browsing captured images by camera and date
- `microservices/prometheus.yml` & `microservices/grafana/` - Monitoring stack for metrics collection and visualization

**Monolith** (legacy):
- `monolith/` - Original single-container detector using MobileNet-SSD (kept for reference)

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
  - Consumes `frame_queue`, validates hash, decodes image, runs YOLOv8n for human detection, saves detection frames to `CAPTURES_DIR/camera_<id>/<YYYY-MM-DD>/`.
  - Detection confidence threshold configurable via `DETECTION_CONFIDENCE` (default: 0.7).
  - Runs 5 replicas for parallel frame processing.

- `microservices/alert_service/alert_service.py`
  - Consumes `alert_queue` and sends Twilio notifications.
  - Rate-limited alerts via `ALERT_COOLDOWN` (default: 90 seconds).

- `microservices/viewer_service/viewer_service.py`
  - Web-based image viewer for browsing captures organized by camera and date.
  - REST APIs: `/api/cameras`, `/api/cameras/{camera}/dates`, `/api/cameras/{camera}/dates/{date}/images`.
  - Lazy-loaded image grid with lightbox modal and pagination.

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

## Docker / Docker Compose

The repository includes `docker-compose.yaml` files for both microservices and monolith.

### Microservices (Recommended)

```bash
cd microservices

# Copy and configure environment
cp .env.example .env
# Edit .env with your camera IP, Twilio credentials, etc.

# Run all services
docker compose up -d

# Run specific cameras only (using profiles)
docker compose --profile cam1,cam2 up -d

# View logs
docker compose logs -f viewer_service
```

**Access:**
- Viewer UI: http://localhost:8085
- RabbitMQ Management: http://localhost:15672
- API: http://localhost:8085/api/cameras
- Grafana Dashboard: http://localhost:3000 (login: admin/admin)
- Prometheus Metrics: http://localhost:9090

### Monolith (Legacy)

```bash
cd monolith

cp .env.example .env
# Edit .env

# Run all channels
docker compose --profile ch1,ch2,ch3 up -d
```

**Configuration via `.env`:**
- `STREAM_IP`, `STREAM_USERNAME`, `STREAM_PASSWORD` - Camera credentials
- `RABBITMQ_HOST`, `RABBITMQ_USER`, `RABBITMQ_PASS` - RabbitMQ settings
- `TWILIO_*` - SMS/Call alerts
- `START_TIME`, `END_TIME` - Capture window (e.g., "00:00:00", "23:59:59")
- `DETECTION_CONFIDENCE` - YOLOv8n threshold (0.0-1.0)
- `ALERT_COOLDOWN` - Alert rate limit in seconds

---

## Quick Start (Microservices)

1. **Setup:**
   ```bash
   cd microservices
   cp .env.example .env
   # Edit .env with your camera IP/credentials and Twilio account (for alerts)
   ```

2. **Start services:**
   ```bash
   docker compose --profile cam1 up -d
   docker compose logs -f
   ```

3. **Check status:**
   ```bash
   # View captured images
   ls captures/camera_1/$(date +%Y-%m-%d)/

   # Test viewer API
   curl http://localhost:8085/api/cameras
   ```

4. **Browse images:**
   - Open http://localhost:8085 in browser
   - Select camera → date → view detection grid

---

## Troubleshooting & notes

- Frame reshape errors: ensure `FRAME_WIDTH`/`FRAME_HEIGHT` match the camera stream. If you don't know the resolution, try a conservative small one or probe the camera with ffprobe.
- Large message payloads: images are PNG-encoded by default (lossless and larger). To reduce bandwidth and RAM, consider switching to JPEG encoding with adjustable quality (TODO in repo).
- Disk growth: saved frames accumulate. Add a pruning job or cron to remove files older than N days.
- RTSP URL differences: some cameras require different RTSP URL formats. The capturer uses a common pattern, but you may need to adapt `frame_capturer.py` to your camera vendor.

---

## Future Improvements

- Add image pruning/retention policy (auto-delete frames older than N days)
- Add database backend (PostgreSQL) for detection metadata and statistics
- Support multiple detection models (edge/cloud deployment)
- Add authenticated access to viewer service
- Implement object tracking for multi-frame intruder sequences

---