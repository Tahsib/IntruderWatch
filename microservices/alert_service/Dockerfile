FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY alert_service/requirements.txt .

# Set the timezone to Bangladesh
RUN apt-get update && apt-get install -y tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Dhaka /etc/localtime \
    && echo 'Asia/Dhaka' > /etc/timezone \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

# Copy shared module and application code
COPY shared/ ./shared/
COPY alert_service/ .

# Run the application
CMD ["python", "alert_service.py"]
