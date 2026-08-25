# Use official AMD ROCm PyTorch image as base
FROM rocm/pytorch:latest@sha256:4449f856653602317e4101a76fce599c7fcd58ccec2e539951fce5f73083179e

WORKDIR /app

# Install system dependencies for OpenCV and other tools
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Dhaka /etc/localtime \
    && echo 'Asia/Dhaka' > /etc/timezone \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Using the pre-installed torch/torchvision in the ROCm image
COPY human_detector/requirements.txt .
# hadolint ignore=DL3013
RUN pip install --no-cache-dir ultralytics pika prometheus-client

# Environment variables for ROCm and YOLO
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YOLO_CONFIG_DIR=/tmp/.ultralytics \
    YOLO_VERBOSE=False \
    HSA_OVERRIDE_GFX_VERSION=10.3.0

# Copy application code
COPY shared/ ./shared/
COPY human_detector/ .

# Pre-download YOLO models (YOLO11m Medium default, yolo11l Large optional)
RUN python3 -c "from ultralytics import YOLO; YOLO('yolo11m.pt'); YOLO('yolo11l.pt')" \
    && mkdir -p /tmp/.ultralytics \
    && chmod -R 777 /tmp/.ultralytics

EXPOSE 8000

CMD ["python3", "human_detector.py"]
