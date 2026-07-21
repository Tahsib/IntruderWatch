# IntruderWatch Microservices Architecture

## Overview

IntruderWatch is a high-performance intruder detection system designed as a distributed set of microservices orchestrated via RabbitMQ. It captures high-fidelity frames from RTSP security cameras, performs surgical human detection using GPU-accelerated **YOLO11 Medium**, dispatches multi-channel alerts (Twilio + ntfy), and provides a secure web-based interface for visual audit.

The architecture is engineered for high-end hardware, leveraging an **Intel i7-12700K** for ingestion and an **AMD RX 6800 XT** (via ROCm) for real-time detection inference.

```
[RTSP Source] --> [Ingestion Pipeline (i7 CPU)] --> [Motion Filter (MSE)] --> [Message Broker (RabbitMQ)] --> [Inference Engine (AMD GPU)] --> [Notification Engine] --> [Twilio/ntfy]
                                                                                                                                      |
                                                                                                                    [Persistent Captures] <-- [Secure Viewer Service]
```

---

## Service Catalog

### 1. Ingestion Engine (`frame_capturer/`)

**Purpose:** Establishes high-resolution camera links and standardizes frame data for downstream processing.

**Mechanism:**
- Utilizes `ffmpeg` to interface with RTSP streams.
- Extracts raw BGR24 frames at **3 FPS** (configurable), striking a balance between detection accuracy and thermal safety.
- Performs **MSE-based Motion Filtering** to eliminate redundant processing of static frames (ignoring sensor grain/noise).
- Encodes standardized frames as high-quality **JPEG (85%)**, significantly reducing broker bandwidth while maintaining evidence-grade detail.

**Primary Configuration:**
| Variable | Description | Default |
|---|---|---|
| `FPS` | Targeted extraction frame rate | `3` |
| `MOTION_THRESHOLD` | Sensitivity for MSE motion detection | `5.0` |
| `JPEG_QUALITY` | Evidence compression level | `85` |

---

### 2. Core Inference Engine (`human_detector/`)

**Purpose:** Runs deep learning models to identify human targets within motion-qualified frames.

**Mechanism:**
- Powered by **YOLO11 Medium** (PyTorch FP16 execution) via **AMD ROCm**.
- Consumes frame data directly from RabbitMQ (`frame_queue`).
- Performs detection inference at `1280x1280` image resolution.
- Filters predictions to target `class 0` (Person) with confidence $\ge 0.8$.
- Draws bounding boxes and saves high-resolution audit images to volume storage.
- Publishes lightweight alert payloads to RabbitMQ (`alert_queue`).

**Resource Allocations:**
- **VRAM Target**: $\approx 4\text{GB}$ VRAM on AMD RX 6800 XT.
- **Memory Profile**: 6GB RAM per cluster.

---

### 3. Notification Engine (`alert_service/`)

**Purpose:** Manages asynchronous delivery of security alerts across multiple urgent and visual channels.

**Mechanism:**
- Consumes events from the centralized RabbitMQ `alert_queue`.
- **Redundant Dispatch:**
  - **Urgent:** Triggers async phone calls via Twilio API.
  - **Visual:** Delivers high-res detection images via self-hosted **ntfy** topics.
- **Crash-Resilient Rate Limiting:** Uses RabbitMQ native `prefetch_count=1` with delayed ACKs and `NTFY_RATE_LIMIT_SEC` (default: **3.0s**) to deliver 100% of captured frames to the ntfy app at a steady pace without app lockup or frame loss on container restart.
- **Suppression Logic:** Enforces an `ALERT_COOLDOWN` (default: **120s**) for voice call alerts to prevent phone spam.

---

### 4. Secure Viewer Service (`viewer_service/`)

**Purpose:** Provides a centralized, authenticated interface for historical evidence review.

**Mechanism:**
- FastAPI-based web application with high-performance static serving.
- Implements a **Dual-Layer Authentication** model:
  - **Dashboard:** HTTP Basic Auth.
  - **Instant Alerts:** Secure Bypass Tokens for seamless mobile image preview.

---

### 5. Observability Suite (Master Command Center)

**Purpose:** Delivers real-time operational visibility into system performance and hardware health.

**Components:**
- **Prometheus**: Aggregates RED (Rate, Errors, Duration) metrics from all services.
- **AMD GPU Exporter**: Monitors RX 6800 XT Core frequency, **Junction Temperature**, and **Power Draw (Watts)**.
- **cAdvisor**: Provides granular container-level resource consumption data.
- **Node Exporter**: Tracks host-level hardware telemetry.
- **Grafana**: Orchestrates data into the **Master Command Center** dashboard with real-time hardware status and service uptime timelines.

**Key Performance Indicators (KPIs):**
- **Inference Latency**: Milliseconds per detection cycle.
- **Ingestion Throughput**: Aggregate FPS across the camera network.
- **Hardware Saturation**: USE (Utilization, Saturation, Errors) metrics for CPU, GPU, and RAM.
- **Thermal Footprint**: Real-time junction temperature tracking to ensure long-term hardware health.

---

## Operational Excellence

### Production Integrity
- **Stateless Scaling**: Detectors can be scaled horizontally without data loss.
- **Persistence Layer**: Dedicated Docker volumes for logs, metrics, and captures ensure data integrity across reboots.
- **Security Posture**: Non-root execution environments and isolated internal networks.
