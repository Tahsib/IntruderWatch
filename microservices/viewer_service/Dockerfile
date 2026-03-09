FROM python:3.12-slim
WORKDIR /app
COPY viewer_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY viewer_service/ .

EXPOSE 8003

CMD ["uvicorn", "viewer_service:app", "--host", "0.0.0.0", "--port", "8080"]

