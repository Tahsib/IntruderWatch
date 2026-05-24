# IntruderWatch | High-Performance Computer Vision Security

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

IntruderWatch is an industry-grade, real-time intruder detection system optimized for high-end hardware (**Intel i7-12700K & AMD RX 6800 XT**). It leverages **YOLO11 Medium** for a perfect balance of surgical precision and thermal efficiency, alongside **AMD ROCm** for high-speed GPU-accelerated inference.

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
            DET[Detector Cluster: YOLO11 Medium]
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

- **Core Inference Engine**: Upgraded to **YOLO11 Medium (v11m)** for elite accuracy with significantly reduced thermal impact.
- **Hardware Accelerated**: Full **AMD GPU acceleration** via ROCm, enabling real-time high-resolution inference.
- **Motion Pre-Filtering**: Advanced **MSE (Mean Squared Error)** filtering on the CPU to ignore sensor noise and prevent redundant GPU work.
- **High-Fidelity Source**: Captures at **1080P (1920x1080)** and processes at **1280px** inference resolution.
- **Temporal Balance**: **4 FPS** capture rate (configurable) for optimal motion tracking and hardware longevity.
- **Master Command Center**: Industry-standard **Grafana dashboard** with real-time GPU junction temperature and power draw monitoring.

---

## 📂 Repository Structure

**Microservices** (Current):
- `microservices/frame_capturer/` - 1080P/6FPS RTSP capture via ffmpeg.
- `microservices/human_detector/` - GPU-accelerated YOLO11L detection.
- `microservices/alert_service/` - Multi-channel (Twilio + ntfy) notification engine.
- `microservices/viewer_service/` - FastAPI web UI for secure detection browsing.
- `microservices/grafana/` & `prometheus/` - 'Master Command Center' observability suite.

**Legacy**:
- `monolith/` - Reference single-container detector (MobileNet-SSD).

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
- `STREAM_IP`, `STREAM_USERNAME`, `STREAM_PASSWORD` - Camera credentials.
- `INFERENCE_SIZE` - Detection resolution (Default: **1280**).
- `DETECTION_CONFIDENCE` - Confidence threshold (Default: **0.8**).
- `FRAME_WIDTH`, `FRAME_HEIGHT` - Capture resolution (Default: **1920x1080**).
- `JPEG_QUALITY` - Image compression (Default: **85**).
- `ALERT_COOLDOWN` - Suppression window between notifications (Default: **90s**).

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

- **Multi-Stage Builds**: Docker images are built in stages to ensure the final runtime is lean and secure.
- **Non-Root Execution**: All services run as a dedicated `appuser` for improved security posture.
- **Aggressive Caching**: Model weights (`yolo11l.pt`) are pre-downloaded during build to ensure instant deployment.
- **Resource Caps**: Precise CPU/RAM limits ensure the system stays stable without starving host resources.

---
