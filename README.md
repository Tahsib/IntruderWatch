# IntruderWatch | High-Performance Computer Vision Security

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

IntruderWatch is an industry-grade, real-time intruder detection system optimized for high-end hardware (**Intel i7-12700K & AMD RX 6800 XT**). It leverages **YOLO11 Large (yolo11l.pt)** for a perfect balance of surgical precision and thermal efficiency, alongside **AMD ROCm** for high-speed GPU-accelerated FP16 inference.

## 🏗️ System Architecture

```mermaid
graph TD
    subgraph "Ingestion Network"
        subgraph "Cam 1 Pipeline"
            C1[Camera 1: 1080P]
            FC1[Capturer Container 1]
        end
        subgraph "Cam 2 Pipeline"
            C2[Camera 2: 1080P]
            FC2[Capturer Container 2]
        end
        subgraph "Scalability..."
            CX[Other Channels...]
        end
    end

    subgraph "Intel i7-12700K (Host CPU)"
        subgraph "Processing Stack"
            MSE[MSE Motion Filtering]
            ENC[JPEG Encoder]
        end

        subgraph "Message Broker (RAM)"
            RMQ[(RabbitMQ: 2GB tmpfs)]
        end

        subgraph "Observability Suite"
            PROM[Prometheus]
            GRAF[Grafana Master Dashboard]
            CADV[cAdvisor]
            NODE[Node Exporter]
            GPU_EXP[AMD GPU Exporter]
        end
    end

    subgraph "AMD RX 6800 XT (GPU)"
        subgraph "Detection Inference (ROCm)"
            DET[Detector Cluster: YOLO11 Large]
            BUF[High-Res Inference Buffer]
        end
    end

    subgraph "Notifications & Storage"
        ALRT[Alert Service]
        VIEW[FastAPI Viewer]
        NOTI[ntfy / Twilio]
        DISK[(SSD Captures)]
    end

    %% Traffic Flow
    C1 -- "RTSP" --> FC1
    C2 -- "RTSP" --> FC2

    FC1 & FC2 -- "Raw BGR" --> MSE
    MSE -- "Motion Detected?" --> ENC
    ENC -- "Base64 JPEG" --> RMQ
    RMQ -- "Queue: frame_queue" --> DET
    DET -- "ROCm Acceleration" --> BUF
    BUF -- "Human? = YES" --> RMQ
    RMQ -- "Queue: alert_queue" --> ALRT
    BUF -- "Persist Frame" --> DISK
    ALRT -- "Async Notify" --> NOTI
    DISK -- "Secure Serve" --> VIEW

    %% Monitoring Flow
    FC1 & FC2 & DET & ALRT & RMQ -- "RED Metrics" --> PROM
    NODE & CADV & GPU_EXP -- "USE Metrics" --> PROM
    PROM -- "PromQL" --> GRAF
```

## 🚀 Key Features (High Efficiency Mode)

- **Core Inference Engine**: Powered by **YOLO11 Large (yolo11l.pt)** for high-precision human detection with minimum false positives.
- **Hardware Accelerated**: Full **AMD GPU acceleration** via ROCm FP16 (Half-Precision) math, enabling high-throughput inference.
- **Crash-Resilient Rate Limiting**: RabbitMQ native prefetch (`prefetch_count=1`) rate limiting to deliver 100% of detection frames to **ntfy** at a steady pace without app freezes.
- **Unified Alerting Stack**: Multi-channel alerting supporting **ntfy** push notifications and **Twilio** voice call alerts with cooldown suppression.
- **Secure Remote Access**: Integrated **Cloudflare Tunnel** for encrypted, zero-port-forwarding remote access to feeds and alerts.
- **Motion Pre-Filtering**: Advanced **MSE (Mean Squared Error)** filtering on CPU to ignore noise and eliminate redundant GPU processing.
- **High-Fidelity Source**: Captures at **1080P (1920x1080 @ 3 FPS)** and processes at **1600px** high-res inference resolution.
- **Master Command Center**: Professional-grade **Grafana dashboard** with real-time GPU junction temperature, power draw, latency, and throughput metrics.

---

## 📂 Repository Structure

**Microservices**:
- `microservices/frame_capturer/` - 1080P/3FPS RTSP capture via ffmpeg with MSE motion filtering.
- `microservices/human_detector/` - GPU-accelerated YOLO11 Large detection engine with deduplication & warm-up logic.
- `microservices/alert_service/` - Notification engine with RabbitMQ native rate limiting, Twilio voice calls, and Alertmanager beautifier webhook.
- `microservices/viewer_service/` - FastAPI web UI for secure visual audit and detection browsing.
- `microservices/tunnel/` - Cloudflare Tunnel for secure public ingress (`watch.tahsib.dev`).
- `microservices/grafana/` & `prometheus/` - Observability stack (Grafana, Prometheus, Loki, Promtail, cAdvisor, AMD GPU Exporter).

---

## 🔔 Proactive Alerting

The system monitors more than just intruders. You receive real-time notifications for:

- **Security**: Instant intruder detection with snapshot photo attachments.
- **Hardware Health**: GPU Junction Temp (>75°C) and GPU Power (>150W) warnings.
- **Infrastructure**: Host CPU load (>70%), Low Disk Space (<20%), and Service outages.
- **Connectivity**: Real-time push notifications delivered via a secure public tunnel—no VPN required.

---

## 🖥️ System Observability

The system includes a professional-grade monitoring stack accessible at **http://localhost:3000** (admin/admin).

- **Hardware Performance**: Real-time Host CPU load, GPU Core Activity, RAM Utilization, and VRAM pressure.
- **Detection Analytics**: Per-camera Inference Latency (ms), Throughput (FPS), and Detection Statistics.
- **Operational Health**: Per-service RAM/CPU consumption for all production containers.

---

## 🛠️ Configuration (Recommended)

### Hardware Reference
The current deployment is tuned for:
- **CPU**: Intel i7-12700K (12 Cores / 20 Threads)
- **GPU**: AMD Radeon RX 6800 XT (16GB VRAM)
- **RAM**: 32GB DDR4

### Environment Variables (.env)
- `STREAM_IP`, `STREAM_USERNAME`, `STREAM_PASSWORD` - Camera network credentials.
- `INFERENCE_SIZE` - Detection inference resolution (Default: **1600**).
- `DETECTION_CONFIDENCE` - Confidence threshold (Default: **0.8**).
- `FRAME_WIDTH`, `FRAME_HEIGHT` - Capture resolution (Default: **1920x1080**).
- `FPS` - Frame capture extraction rate (Default: **3**).
- `JPEG_QUALITY` - Image compression (Default: **85**).
- `ALERT_COOLDOWN` - Voice call suppression window between phone alerts (Default: **120s**).
- `NTFY_RATE_LIMIT_SEC` - System-wide rate-limit pacing for ntfy push notifications to prevent mobile app crashes (Default: **3.0s**).

---

## 🐳 Quick Start (Microservices)

1. **Setup Hardware Drivers**: Ensure AMD ROCm drivers are installed on the host.
2. **Configure**:
   ```bash
   cd microservices
   cp .env.example .env
   # Edit .env with your camera and Twilio/ntfy details
   ```
3. **Launch**:
   ```bash
   docker compose up -d --build
   ```
4. **Monitor**:
   - Open **Grafana**: http://localhost:3000
   - Open **Viewer**: http://localhost:8085

---

## 💎 Optimization Highlights

- **Multi-Stage Builds**: Docker images are built in stages to ensure runtime containers remain lean.
- **Non-Root Execution**: Services run as dedicated unprivileged users for security compliance.
- **Aggressive Caching**: Model weights (`yolo11l.pt`) are pre-cached during build to ensure instant deployment.
- **Resource Caps**: Precise CPU/RAM limits ensure cluster stability without starving host resources.
