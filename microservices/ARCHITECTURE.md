# IntruderWatch Microservices Architecture

## Overview

IntruderWatch is a high-performance intruder detection system designed as a distributed set of microservices orchestrated via RabbitMQ. It captures high-fidelity frames from RTSP security cameras, performs surgical human detection using GPU-accelerated **YOLO11 Large**, dispatches multi-channel alerts (Twilio + ntfy), and provides a secure web-based interface for visual audit.

The architecture is engineered for high-end hardware, leveraging an **Intel i7-12700K** for ingestion and an **AMD RX 6800 XT** (via ROCm) for real-time detection inference.

```
[RTSP Source] --> [Ingestion Pipeline (i7 CPU)] --> [Message Broker (RabbitMQ)] --> [Inference Engine (AMD GPU)] --> [Notification Engine] --> [Twilio/ntfy]
                                                                                                                      |
                                                                                                    [Persistent Captures] <-- [Secure Viewer Service]
```

---

## Service Catalog

### 1. Ingestion Engine (`frame_capturer/`)

**Purpose:** Establishes high-resolution camera links and standardizes frame data for downstream processing.

**Mechanism:**
- Utilizes `ffmpeg` to interface with RTSP streams.
- Extracts raw BGR24 frames at **6 FPS** (configurable), ensuring high temporal resolution for motion tracking.
- Performs **Temporal Deduplication** via SHA-256 hashing to eliminate redundant processing of static frames.
- Encodes standardized frames as high-quality **JPEG (85%)**, significantly reducing broker bandwidth while maintaining evidence-grade detail.

**Primary Configuration:**
| Variable | Description | Default |
|---|---|---|
| `STREAM_IP` | Camera/NVR Network Address | - |
| `CHANNEL` | Stream Channel Identifier | - |
| `FPS` | Targeted Frame Rate | 6 |
| `FRAME_WIDTH` | Capture Resolution (Width) | 1920 (1080P) |
| `FRAME_HEIGHT` | Capture Resolution (Height) | 1080 (1080P) |
| `JPEG_QUALITY` | Encoder Quality Profile | 85 |

---

### 2. Core Inference Engine (`human_detector/`)

**Purpose:** Executes deep learning-based object detection on high-resolution frame buffers.

**Mechanism:**
- Deploys the **YOLO11 Large** model, optimized for maximum detection accuracy.
- Leverages **AMD ROCm** hardware acceleration on the RX 6800 XT.
- Employs **FP16 (Half-Precision)** math to double inference throughput and reduce VRAM bandwidth pressure.
- Implements **Serialized GPU Warm-up** to ensure driver stability during multi-replica initialization.
- **Decision Logic:**
  - Performs spatial detection for human classes.
  - Persists annotated evidence frames to secure storage.
  - Publishes detection events to the notification exchange.

**Hardware Specifications:**
- **Inference Resolution**: 1280px (Standardized high-fidelity input).
- **Math Precision**: FP16 (Half-precision).
- **Concurrency**: 4 Replicas (Optimized for 48+ aggregate FPS).
- **Memory Profile**: 12GB RAM per cluster.

---

### 3. Notification Engine (`alert_service/`)

**Purpose:** Manages asynchronous delivery of security alerts across multiple urgent and visual channels.

**Mechanism:**
- Consumes events from the centralized RabbitMQ alert exchange.
- **Redundant Dispatch:**
  - **Urgent:** Triggers async phone calls via Twilio API.
  - **Visual:** Delivers high-res detection images via self-hosted **ntfy** topics.
- **Suppression Logic:** Enforces a **90s global cooldown** to prevent notification fatigue during ongoing incidents.

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
- **AMD GPU Exporter**: Monitors RX 6800 XT Core frequency, temperature, and VRAM utilization.
- **cAdvisor**: Provides granular container-level resource consumption data.
- **Node Exporter**: Tracks host-level hardware telemetry.
- **Grafana**: Orchestrates data into the **Master Command Center** dashboard.

**Key Performance Indicators (KPIs):**
- **Inference Latency**: Milliseconds per detection cycle.
- **Ingestion Throughput**: Aggregate FPS across the camera network.
- **Hardware Saturation**: USE (Utilization, Saturation, Errors) metrics for CPU, GPU, and RAM.

---

## Operational Excellence

### Production Integrity
- **Stateless Scaling**: Detectors can be scaled horizontally without data loss.
- **Persistence Layer**: Dedicated Docker volumes for logs, metrics, and captures ensure data integrity across reboots.
- **Security Posture**: Non-root execution environments and isolated internal networks.
