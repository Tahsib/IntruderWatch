from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from prometheus_client import start_http_server, Counter, Histogram
import time

app = FastAPI()

# Prometheus Metrics
HTTP_REQUESTS_TOTAL = Counter('viewer_service_http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status_code'])
REQUEST_LATENCY = Histogram('viewer_service_http_request_duration_seconds', 'HTTP request latency', ['endpoint'])
IMAGES_SERVED_TOTAL = Counter('viewer_service_images_served_total', 'Total images served')

@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    
    # Simple endpoint classification
    endpoint = request.url.path
    if endpoint.startswith("/images/"):
        endpoint = "/images/{camera}/{date}/{filename}"
    elif endpoint.startswith("/api/cameras/"):
        parts = endpoint.split("/")
        if len(parts) == 4:
            endpoint = "/api/cameras/{camera}/dates"
        elif len(parts) == 6:
            endpoint = "/api/cameras/{camera}/dates/{date}/images"
    
    HTTP_REQUESTS_TOTAL.labels(method=request.method, endpoint=endpoint, status_code=str(response.status_code)).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)
    
    return response

@app.on_event("startup")
async def startup_event():
    # Start Prometheus metrics server
    try:
        start_http_server(8003)
    except Exception:
        pass

CAPTURES_DIR = Path("/app/captures")


@app.get("/")
async def serve_root():
    """Serve the main HTML page."""
    return FileResponse("index.html", media_type="text/html")


@app.get("/api/cameras")
async def get_cameras():
    """List all camera folders sorted."""
    if not CAPTURES_DIR.exists():
        return []

    cameras = sorted([
        d.name for d in CAPTURES_DIR.iterdir()
        if d.is_dir() and d.name.startswith("camera_")
    ])
    return cameras


@app.get("/api/cameras/{camera}/dates")
async def get_dates(camera: str):
    """List date folders for a camera, sorted descending (newest first)."""
    camera_path = CAPTURES_DIR / camera

    if not camera_path.exists():
        return []

    dates = sorted([
        d.name for d in camera_path.iterdir()
        if d.is_dir()
    ], reverse=True)

    return dates


@app.get("/api/cameras/{camera}/dates/{date}/images")
async def get_images(camera: str, date: str):
    """List image filenames for a camera/date."""
    date_path = CAPTURES_DIR / camera / date

    if not date_path.exists():
        return []

    # Get all image files with their modification times
    image_files = [
        f for f in date_path.iterdir()
        if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]
    ]
    
    # Sort by modification time descending (newest first)
    image_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    
    return [f.name for f in image_files]


@app.get("/images/{camera}/{date}/{filename}")
async def serve_image(camera: str, date: str, filename: str):
    """Serve image file."""
    image_path = CAPTURES_DIR / camera / date / filename

    # Security: prevent directory traversal
    if not image_path.exists() or not image_path.is_file():
        return FileResponse("", status_code=404)

    if not str(image_path).startswith(str(CAPTURES_DIR)):
        return FileResponse("", status_code=403)

    IMAGES_SERVED_TOTAL.inc()
    return FileResponse(image_path)
