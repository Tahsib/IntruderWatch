# IntruderWatch | High-Performance Computer Vision Security

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

IntruderWatch is an industry-grade, real-time intruder detection system optimized for high-end hardware (**Intel i7-12700K & AMD RX 6800 XT**). It leverages **YOLO11 Medium** for a perfect balance of surgical precision and thermal efficiency, alongside **AMD ROCm** for high-speed GPU-accelerated inference.

## 🏗️ System Architecture

```mermaid
graph TD
    subgraph "External Camera Network"
        C1[Camera 1..8: RTSP 1080P]
    end

    subgraph "Ingestion Engine (Intel i7 CPU)"
        FC[Frame Capturers: 8 Channels]
        MSE[MSE Motion Filtering]
        ENC[JPEG Encoder: 85%]
    end

    subgraph "Message Broker (RAM)"
        RMQ_F[(RabbitMQ: frame_queue)]
        RMQ_A[(RabbitMQ: alert_queue)]
    end

    subgraph "Core Inference Engine (AMD RX 6800 XT / ROCm)"
        DET[Detector Cluster: YOLO11 Medium]
        FP16[FP16 Half-Precision Inference]
    end

    subgraph "Notifications & Storage"
        DISK[(SSD Captures Storage)]
        VIEW[FastAPI Viewer Service: Port 8085]
        NTFY[ntfy Push Server: Port 8081]
        ALRT[Alert Service: Webhook & Cooldown]
    end

    subgraph "Secure Remote Access Layer"
        TUNNEL[cloudflared_tunnel Container]
        CF[Cloudflare Edge: QUIC / HTTPS]
        PHONE[Mobile App / Remote User]
    end

    subgraph "Observability Suite"
        PROM[Prometheus + Alertmanager]
        GRAF[Grafana Master Command Center]
        LOKI[Loki + Promtail Logs]
        EXP[cAdvisor / Node / AMD GPU Exporters]
    end

    %% Ingestion Flow
    C1 -- "RTSP" --> FC
    FC -- "Raw BGR24" --> MSE
    MSE -- "Motion Detected" --> ENC
    ENC -- "Base64 JPEG" --> RMQ_F

    %% Detection Flow
    RMQ_F -- "Consume Frame" --> DET
    DET --> FP16
    FP16 -- "Human Detected" --> DISK
    FP16 -- "Publish Alert" --> RMQ_A

    %% Alerting Flow
    RMQ_A --> ALRT
    ALRT -- "Local HTTP" --> NTFY
    ALRT -- "Voice Call API" --> TWILIO[Twilio Voice Call]

    %% Remote Access Flow
    DISK -- "Secure Serve" --> VIEW
    VIEW & NTFY <--> TUNNEL
    TUNNEL <== "Outbound QUIC Tunnel" ==> CF
    CF <== "watch.tahsib.dev / alerts.tahsib.dev" ==> PHONE

    %% Observability Flow
    FC & DET & ALRT & VIEW & EXP -- "Metrics" --> PROM
    FC & DET & ALRT & VIEW -- "Container Logs" --> LOKI
    PROM & LOKI --> GRAF
```

## 🚀 Key Features (High Efficiency Mode)

- **Core Inference Engine**: Upgraded to **YOLO11 Medium (v11m)** for elite accuracy with significantly reduced thermal impact.
- **Hardware Accelerated**: Full **AMD GPU acceleration** via ROCm, enabling real-time high-resolution inference.
- **Unified Alerting Stack**: Professional-grade monitoring using **Prometheus Alertmanager** and **ntfy** for instant, beautifully formatted notifications.
- **Secure Remote Access**: Integrated **Cloudflare Tunnel** for encrypted, zero-port-forwarding access to camera feeds and alerts from anywhere.
- **Real-Time Push**: Configured with upstream push servers for **instant mobile delivery** on iOS and Android.
- **Motion Pre-Filtering**: Advanced **MSE (Mean Squared Error)** filtering on the CPU to ignore sensor noise and prevent redundant GPU work.
- **High-Fidelity Source**: Captures at **1080P (1920x1080)** and processes at **1280px** inference resolution.
- **Master Command Center**: Industry-standard **Grafana dashboard** with real-time GPU junction temperature and power draw monitoring.

---

## 📂 Repository Structure

**Microservices** (Current):
- `microservices/frame_capturer/` - 1080P/6FPS RTSP capture via ffmpeg.
- `microservices/human_detector/` - GPU-accelerated YOLO11L detection.
- `microservices/alert_service/` - Unified notification engine with "Alert Beautifier" logic.
- `microservices/viewer_service/` - FastAPI web UI for secure detection browsing.
- `microservices/tunnel/` - Cloudflare Tunnel for secure remote access.
- `microservices/grafana/` & `prometheus/` - 'Master Command Center' observability suite.

---

## 🔔 Proactive Alerting

The system monitors more than just intruders. You receive real-time notifications for:

- **Security**: Instant intruder detection with photo attachments.
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
