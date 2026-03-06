FROM python:3.12-slim

WORKDIR /app

COPY alert_service/requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Dhaka /etc/localtime \
    && echo 'Asia/Dhaka' > /etc/timezone \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY alert_service/ .

CMD ["python", "alert_service.py"]
