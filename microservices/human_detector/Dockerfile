# Use official AMD ROCm PyTorch image as base
FROM rocm/pytorch:latest

WORKDIR /app

# Install system dependencies for OpenCV and other tools
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

# Pre-download YOLO11l model (Large model for better accuracy and fewer false positives)
RUN python3 -c "from ultralytics import YOLO; YOLO('yolo11l.pt')" \
    && mkdir -p /tmp/.ultralytics \
    && chmod -R 777 /tmp/.ultralytics

EXPOSE 8000

CMD ["python3", "human_detector.py"]
