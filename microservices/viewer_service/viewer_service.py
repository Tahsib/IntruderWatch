import os
import secrets
import time
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import start_http_server, Counter, Histogram

# Auth Configuration
security = HTTPBasic()
VIEWER_USERNAME = os.getenv("VIEWER_USERNAME", "admin")
VIEWER_PASSWORD = os.getenv("VIEWER_PASSWORD", "password123")
ALERT_BYPASS_TOKEN = os.getenv("ALERT_BYPASS_TOKEN")


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    is_correct_username = secrets.compare_digest(credentials.username, VIEWER_USERNAME)
    is_correct_password = secrets.compare_digest(credentials.password, VIEWER_PASSWORD)
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Prometheus metrics server
    try:
        start_http_server(8003)
    except Exception:
        pass
    yield


app = FastAPI(lifespan=lifespan)

# Prometheus Metrics
HTTP_REQUESTS_TOTAL = Counter(
    "viewer_service_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "viewer_service_http_request_duration_seconds", "HTTP request latency", ["endpoint"]
)
IMAGES_SERVED_TOTAL = Counter(
    "viewer_service_images_served_total", "Total images served"
)


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

    HTTP_REQUESTS_TOTAL.labels(
        method=request.method, endpoint=endpoint, status_code=str(response.status_code)
    ).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

    return response


CAPTURES_DIR = Path("/app/captures")


@app.get("/", dependencies=[Depends(verify_credentials)])
async def serve_root():
    """Serve the main HTML page."""
    return FileResponse("index.html", media_type="text/html")


@app.get("/api/cameras", dependencies=[Depends(verify_credentials)])
async def get_cameras():
    """List all camera folders sorted."""
    if not CAPTURES_DIR.exists():
        return []

    cameras = sorted(
        [
            d.name
            for d in CAPTURES_DIR.iterdir()
            if d.is_dir() and d.name.startswith("camera_")
        ]
    )
    return cameras


@app.get("/api/cameras/{camera}/dates", dependencies=[Depends(verify_credentials)])
async def get_dates(camera: str):
    """List date folders for a camera, sorted descending (newest first)."""
    camera_path = (CAPTURES_DIR / camera).resolve()
    if not str(camera_path).startswith(str(CAPTURES_DIR.resolve())):
        return []

    if not camera_path.exists():
        return []

    dates = sorted([d.name for d in camera_path.iterdir() if d.is_dir()], reverse=True)

    return dates


@app.get(
    "/api/cameras/{camera}/dates/{date}/images",
    dependencies=[Depends(verify_credentials)],
)
async def get_images(camera: str, date: str):
    """List image filenames for a camera/date."""
    date_path = (CAPTURES_DIR / camera / date).resolve()
    if not str(date_path).startswith(str(CAPTURES_DIR.resolve())):
        return []

    if not date_path.exists():
        return []

    # Get all image files with their modification times
    image_files = [
        f
        for f in date_path.iterdir()
        if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]
    ]

    # Sort by modification time descending (newest first)
    image_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    return [f.name for f in image_files]


@app.get("/images/{camera}/{date}/{filename}")
async def serve_image(
    camera: str, date: str, filename: str, token: str = None, request: Request = None
):
    """Serve image file. Supports Basic Auth OR a valid Alert Bypass Token."""

    # 1. Check for valid Bypass Token
    authenticated = False
    if ALERT_BYPASS_TOKEN and token == ALERT_BYPASS_TOKEN:
        authenticated = True

    # 2. Fallback to Basic Auth if no token
    if not authenticated:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        # Manually trigger HTTPBasic logic for this specific route
        try:
            # security(request) parses the header and handles basic validation
            credentials = await security(request)
            verify_credentials(credentials)
            authenticated = True
        except Exception as e:
            raise e

    # 3. Path Security & File Serving
    # Prevent path injection by ensuring the final path is strictly inside CAPTURES_DIR
    try:
        # Construct path and resolve it to remove any .. or symlinks
        image_path = (CAPTURES_DIR / camera / date / filename).resolve()
        base_path = CAPTURES_DIR.resolve()

        # Check if the resolved path starts with the base captures directory
        if not str(image_path).startswith(str(base_path)):
            logging.warning(f"Blocked potential path injection attempt: {image_path}")
            raise HTTPException(
                status_code=403, detail="Forbidden: Path traversal blocked"
            )

        if not image_path.exists() or not image_path.is_file():
            raise HTTPException(status_code=404, detail="Image not found")

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logging.error("Error validating path while serving image.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path request"
        )

    IMAGES_SERVED_TOTAL.inc()
    return FileResponse((CAPTURES_DIR / camera / date / filename).resolve())
