FROM python:3.12-slim AS builder

WORKDIR /app

COPY human_detector/requirements.txt .

RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata libgl1 libglib2.0-0 \
    && ln -snf /usr/share/zoneinfo/Asia/Dhaka /etc/localtime \
    && echo 'Asia/Dhaka' > /etc/timezone \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY shared/ ./shared/
COPY human_detector/ .

# Pre-download YOLOv8n model and setup config directory with correct permissions
RUN mkdir -p /root/.config/Ultralytics && \
    python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" && \
    mkdir -p /.ultralytics && \
    chmod -R 777 /.ultralytics /root/.config

ENV YOLO_CONFIG_DIR=/.ultralytics

EXPOSE 8000

CMD ["python", "human_detector.py"]
