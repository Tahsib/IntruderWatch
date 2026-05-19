# IntruderWatch Microservices Architecture

## Overview

IntruderWatch is a high-performance intruder detection system built as a set of microservices connected via RabbitMQ. It captures high-fidelity frames from RTSP security cameras, runs surgical human detection using GPU-accelerated **YOLO11 Large**, alerts via Twilio, and provides a web-based image viewer for browsing detections.

The architecture is specifically tuned for high-end hardware, leveraging an **Intel i7-12700K** for ingestion and an **AMD RX 6800 XT** (via ROCm) for AI inference.

```
[RTSP Cameras] --> [Frame Capturers (i7 CPU)] --> [RabbitMQ (In-Memory)] --> [Human Detectors (AMD GPU)] --> [RabbitMQ] --> [Alert Service] --> [Twilio]
                                                                                                                      |
                                                                                                    [Captures] <-- [Viewer Service] (Web UI)
```

---

## Services

### 1. Frame Capturer (`frame_capturer/`)

**Purpose:** Connects to RTSP camera streams and publishes frames to RabbitMQ at high resolution.

**How it works:**
- Spawns an `ffmpeg` subprocess that connects to the camera's RTSP stream.
- ffmpeg outputs raw video frames (BGR24) at **6 fps** (configurable) via a pipe, providing high-precision motion tracking.
- Each frame is encoded as high-quality **JPEG (85%)**, base64-encoded, and published to `frame_queue`.
- Switched from PNG to JPEG to reduce bandwidth by **90%**, enabling real-time 1080P transmission.
- Uses SHA-256 hashing for frame deduplication to ensure the AI only processes unique movement.

**Key config (environment variables):**
| Variable | Description | Default |
|---|---|---|
| `STREAM_IP` | Camera DVR IP address | - |
| `CHANNEL` | Camera channel number | - |
| `FPS` | Target capture rate | 6 |
| `FRAME_WIDTH` | Frame width in pixels | 1920 (1080P) |
| `FRAME_HEIGHT` | Frame height in pixels | 1080 (1080P) |
| `JPEG_QUALITY` | Compression quality | 85 |
| `FRAME_SLEEP` | Seconds between frames | 0.05 (Optimized for high-end CPU) |

---

### 2. Human Detector (`human_detector/`)

**Purpose:** Consumes frames from RabbitMQ, runs GPU-accelerated human detection, and publishes alerts.

**How it works:**
- Loads the **YOLO11 Large** model (`yolo11l.pt`, ~100MB, pre-downloaded in Docker image).
- Leveraging **AMD ROCm** for hardware acceleration on the **RX 6800 XT** GPU.
- Processes frames at **1280px AI Vision** resolution using **FP16 (Half-Precision)** for maximum stability and speed.
- Implements a **staggered initialization** (one-by-one startup) to prevent GPU memory contention.
- If humans are detected:
  - Draws bounding boxes on the frame.
  - Saves the annotated frame as a JPEG to `/app/captures/camera_{id}/{date}/`.
  - Publishes a JSON alert to `alert_queue`.

**Hardware Optimization:**
- **Inference Size**: 1280px (Standardized high-fidelity input).
- **Precision**: FP16 (Half-precision math, 2x faster, 50% less VRAM bandwidth).
- **Replica Count**: 4 (Scaled to handle 48+ FPS in real-time).
- **RAM**: 12GB allocated to handle high-res buffers.

---

### 3. Alert Service (`alert_service/`)

**Purpose:** Consumes alerts from RabbitMQ and places phone calls via Twilio when humans are detected.

**How it works:**
- Consumes messages from `alert_queue`.
- Triggers async phone calls to all configured numbers.
- Implements a **90s global cooldown** to prevent redundant notifications during a single incident.

---

### 4. Viewer Service (`viewer_service/`)

**Purpose:** Web-based UI for browsing captured detection images organized by camera and date.

**How it works:**
- FastAPI backend serving high-res JPEG captures.
- organized by `camera` -> `date` -> `time`.

---

### 5. Observability Stack (Master Command Center)

**Purpose:** Provides industry-standard "nitty-gritty" visibility into hardware and services.

**Components:**
- **Prometheus**: Scrapes metrics from every service and hardware exporter.
- **AMD GPU Exporter**: Real-time tracking of RX 6800 XT AI Core activity and VRAM.
- **cAdvisor**: Per-container CPU/RAM monitoring for all microservices.
- **Node Exporter**: Host-level tracking for the i7-12700K.
- **Grafana**: Visualizes everything in the **Master Command Center** dashboard.

**Key Metrics:**
- **AI Latency**: Milliseconds taken per detection per camera.
- **System FPS**: Total frames processed per second across the cluster.
- **Hardware USE**: Utilization, Saturation, and Errors for CPU, GPU, and RAM.

---

## Deployment Strategy

### Image Build
- **Multi-Stage Builds**: All Dockerfiles use a `builder` stage to isolate dependencies, resulting in lean production images.
- **Non-Root**: All services run as a dedicated `appuser` for security.

### Hardware Allocation (32GB / i7-12700K / 6800XT)
- **Detector**: 12GB RAM / 4 CPUs / Direct GPU Access.
- **Capturers**: 512MB RAM / 1 CPU per camera.
- **RabbitMQ**: 2GB tmpfs (In-memory buffer for high-throughput 1080P data).
