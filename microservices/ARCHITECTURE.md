# IntruderWatch Microservices Architecture

## Overview

IntruderWatch is an intruder detection system built as a set of microservices connected via RabbitMQ. It captures frames from RTSP security cameras, runs human detection using YOLOv8, alerts via Twilio, and provides a web-based image viewer for browsing detections.

```
[RTSP Cameras] --> [Frame Capturers] --> [RabbitMQ: frame_queue] --> [Human Detectors] --> [RabbitMQ: alert_queue] --> [Alert Service] --> [Twilio]
                                                                                                                |
                                                                                              [Captures] <-- [Viewer Service] (Web UI)
```

---

## Services

### 1. Frame Capturer (`frame_capturer/`)

**Purpose:** Connects to RTSP camera streams and publishes frames to RabbitMQ.

**How it works:**
- Spawns an `ffmpeg` subprocess that connects to the camera's RTSP stream
- ffmpeg outputs raw video frames (BGR24) at 1 fps via a pipe, with optional scaling
- Each frame is encoded as PNG (lossless), base64-encoded, and published to `frame_queue` as a JSON payload containing the image data, a SHA-256 hash for integrity verification, and the camera channel ID
- Operates within a configurable time window (START_TIME to END_TIME) — outside this window, ffmpeg is stopped and the service sleeps
- One instance per camera, all sharing the same Docker image

**Key config (environment variables):**
| Variable | Description | Default |
|---|---|---|
| `STREAM_IP` | Camera DVR IP address | - |
| `STREAM_USERNAME` | RTSP username | - |
| `STREAM_PASSWORD` | RTSP password | - |
| `CHANNEL` | Camera channel number | - |
| `SUBTYPE` | Stream subtype (0=main, 1=sub) | 0 |
| `FRAME_WIDTH` | Frame width in pixels | 1280 |
| `FRAME_HEIGHT` | Frame height in pixels | 720 |
| `START_TIME` | Active hours start (HH:MM:SS) | 00:00:00 |
| `END_TIME` | Active hours end (HH:MM:SS) | 23:59:59 |
| `FRAME_SLEEP` | Seconds between frames | 1.0 |

**Message format published to `frame_queue`:**
```json
{
  "camera": 3,
  "hash": "sha256hex...",
  "image": "base64-encoded PNG bytes"
}
```

---

### 2. Human Detector (`human_detector/`)

**Purpose:** Consumes frames from RabbitMQ, runs YOLOv8n human detection, saves annotated frames, and publishes alerts.

**How it works:**
- Loads the YOLOv8n model (`yolov8n.pt`, ~6MB, auto-downloaded on first run)
- Consumes frames from `frame_queue`, decodes the base64 PNG, and verifies the SHA-256 hash
- Runs YOLOv8n inference with `classes=[0]` (COCO person class) and a configurable confidence threshold
- If humans are detected:
  - Draws bounding boxes on the frame
  - Saves the annotated frame as a PNG to `/app/captures/camera_{id}/{date}/`
  - Publishes a JSON alert to `alert_queue` with camera ID and timestamp
- Runs with multiple replicas (default 5) for parallel processing

**Key config:**
| Variable | Description | Default |
|---|---|---|
| `DETECTION_CONFIDENCE` | Minimum confidence threshold | 0.7 |

**Message format published to `alert_queue`:**
```json
{
  "camera": "3",
  "timestamp": "2026-03-06 04:57:47"
}
```

**Detection output directory structure:**
```
captures/
  camera_3/
    2026-03-06/
      detection_2026-03-06 04:57:47.png
      detection_2026-03-06 05:12:07.png
  camera_8/
    2026-03-06/
      detection_2026-03-06 04:57:15.png
```

---

### 3. Alert Service (`alert_service/`)

**Purpose:** Consumes alerts from RabbitMQ and places phone calls via Twilio when humans are detected.

**How it works:**
- Consumes messages from `alert_queue`
- On first detection: immediately triggers a phone call to all configured numbers
- Starts a global cooldown timer (default 90s) — all subsequent detections from any camera are suppressed during this period
- Twilio API calls run in a background thread so the RabbitMQ consumer stays responsive and doesn't miss messages or drop the connection

**Why global cooldown (not per-camera):** Multiple cameras detecting a human at the same time would trigger simultaneous calls, which is noisy and redundant. A single global cooldown ensures you get one call per incident window.

**Why threaded Twilio calls:** `client.calls.create()` is a blocking HTTP request to Twilio (1-5+ seconds). If the RabbitMQ callback blocks on this, messages pile up in the queue and the consumer can lose its connection due to heartbeat timeout — causing delayed or missed alerts.

**Key config:**
| Variable | Description | Default |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio account SID | - |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | - |
| `TWILIO_PHONE_NUMBER` | Twilio sender number | - |
| `ALERT_PHONE_NUMBERS` | Recipient numbers (colon-separated) | - |
| `ALERT_COOLDOWN` | Seconds between alerts | 90 |

---

### 4. Viewer Service (`viewer_service/`)

**Purpose:** Web-based UI for browsing captured detection images organized by camera and date.

**How it works:**
- FastAPI backend serving REST APIs and a single-page HTML/CSS/JS UI
- Read-only access to `/app/captures/` mounted from the host
- APIs:
  - `GET /api/cameras` — List all cameras (sorted)
  - `GET /api/cameras/{camera}/dates` — List dates for a camera (newest first)
  - `GET /api/cameras/{camera}/dates/{date}/images` — List images for a camera/date
  - `GET /images/{camera}/{date}/{filename}` — Serve image file
- Frontend features:
  - Camera sidebar for selection
  - Date dropdown for chosen camera
  - Lazy-loaded image grid (50 images per page)
  - Auto-load more on scroll
  - Lightbox modal for fullscreen viewing
  - Dark theme UI

**Key config:**
| Variable | Description | Default |
|---|---|---|
| Port | Container port | 8080 |
| Volume | Captures directory (read-only) | `/app/captures` |

**Access:**
- Web UI: `http://localhost:8085` (or configured port)
- API: `http://localhost:8085/api/cameras`

---

### 5. Shared Module (`shared/`)

**`rabbitmq_client.py`** — Common RabbitMQ connection helper used by all services.
- Connects with retry logic (default 5 attempts, 5s delay)
- Declares queues as durable
- Accepts a `frame_max` parameter for connection tuning (frame_capturer uses 131072 for large frame payloads)

---

### 6. RabbitMQ

Message broker connecting all services. Two queues:
- **`frame_queue`** (durable) — carries camera frames from capturers to detectors
- **`alert_queue`** (durable) — carries detection alerts from detectors to alert service

Management UI available at port 15672.

---

## Docker & Deployment

### Image build strategy
- **frame_capturer**: Single image (`microservices_frame_capturer:latest`) shared by all camera instances
- **human_detector**: Single image with 5 replicas via `deploy.replicas`
- **alert_service**: Single instance
- **viewer_service**: Single instance (FastAPI web UI)

### Camera management via profiles
Cameras are controlled via Docker Compose profiles. Edit `COMPOSE_PROFILES` in `.env`:
```bash
# Enable cameras 2, 3, 4, 7, 8
COMPOSE_PROFILES=cam2,cam3,cam4,cam7,cam8

# Disable camera 7 — just remove it
COMPOSE_PROFILES=cam2,cam3,cam4,cam8
```

### Configuration
All secrets live in `.env` (gitignored). See `.env.example` for required variables.
`docker-compose.yaml` contains no secrets and is safe to commit.

### Build & run
```bash
# Build all images
docker compose build frame_capturer human_detector alert_service viewer_service

# Start (cameras determined by COMPOSE_PROFILES in .env)
docker compose up -d

# View logs
docker compose logs -f viewer_service
```

### Quick access
- **Viewer UI**: http://localhost:8085
- **RabbitMQ Management**: http://localhost:15672 (user: `admin`, pass from `.env`)
