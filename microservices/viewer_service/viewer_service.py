import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from prometheus_client import Counter, Histogram, start_http_server
from pydantic import BaseModel

# Auth Configuration
VIEWER_USERNAME = os.getenv("VIEWER_USERNAME", "admin")
VIEWER_PASSWORD = os.getenv("VIEWER_PASSWORD", "password123")
ALERT_BYPASS_TOKEN = os.getenv("ALERT_BYPASS_TOKEN")

# Session Token Configuration (HMAC-signed tokens)
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    SESSION_SECRET = hashlib.sha256(f"{VIEWER_USERNAME}:{VIEWER_PASSWORD}:intruderwatch".encode()).hexdigest()
SESSION_SECRET_BYTES = SESSION_SECRET.encode()
SESSION_MAX_AGE = 7 * 86400  # 7 days


def create_session_token(username: str) -> str:
    """Generates a tamper-proof session token valid for 7 days."""
    expires_at = int(time.time()) + SESSION_MAX_AGE
    payload = f"{username}:{expires_at}".encode()
    signature = hmac.new(SESSION_SECRET_BYTES, payload, hashlib.sha256).hexdigest()
    raw = f"{username}:{expires_at}:{signature}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def verify_session_token(token: str) -> str | None:
    """Validates the HMAC session token signature and expiration."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        parts = raw.split(":")
        if len(parts) != 3:
            return None
        username, expires_at_str, signature = parts
        expires_at = int(expires_at_str)
        if time.time() > expires_at:
            return None
        payload = f"{username}:{expires_at}".encode()
        expected_sig = hmac.new(SESSION_SECRET_BYTES, payload, hashlib.sha256).hexdigest()
        if secrets.compare_digest(signature, expected_sig):
            if secrets.compare_digest(username, VIEWER_USERNAME):
                return username
    except Exception:
        return None
    return None


def get_session_user(request: Request) -> str | None:
    """Checks session cookie strictly (for web UI pages / and /login)."""
    token = request.cookies.get("session_token")
    if token:
        return verify_session_token(token)
    return None


def get_authenticated_user(request: Request) -> str | None:
    """Checks session cookie first, then Basic Auth header (for CLI, curl, automated scripts)."""
    # 1. Check Session Cookie
    user = get_session_user(request)
    if user:
        return user

    # 2. Check Basic Auth header (for CLI, curl, automated scripts)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        try:
            b64_creds = auth_header.split(" ", 1)[1].strip()
            decoded = base64.b64decode(b64_creds).decode("utf-8")
            username, password = decoded.split(":", 1)
            if secrets.compare_digest(username, VIEWER_USERNAME) and secrets.compare_digest(password, VIEWER_PASSWORD):
                return username
        except Exception:
            pass

    return None


def require_auth(request: Request) -> str:
    """Dependency that ensures the user is authenticated, without triggering browser popup."""
    user = get_authenticated_user(request)
    if not user:
        # Crucial: DO NOT include WWW-Authenticate header to prevent native browser popup dialog
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def validate_path_param(value: str, name: str = "parameter"):
    """Validates that a path parameter contains no directory traversal sequences."""
    if not value:
        raise HTTPException(status_code=400, detail=f"Empty {name}")
    if ".." in value or "/" in value or "\\" in value:
        raise HTTPException(status_code=400, detail=f"Invalid character in {name}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Prometheus metrics server
    try:
        start_http_server(8003)
    except Exception as e:
        logging.error(f"Failed to start Prometheus metrics: {e}")
    yield


app = FastAPI(lifespan=lifespan)

# Prometheus Metrics
HTTP_REQUESTS_TOTAL = Counter(
    "viewer_service_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram("viewer_service_http_request_duration_seconds", "HTTP request latency", ["endpoint"])
IMAGES_SERVED_TOTAL = Counter("viewer_service_images_served_total", "Total images served")


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


CAPTURES_DIR = Path("/app/captures")


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/login")
async def serve_login(request: Request):
    """Serve custom Uber-themed login page if unauthenticated, otherwise redirect to home."""
    user = get_session_user(request)
    if user:
        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    response = FileResponse("login.html", media_type="text/html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/login-bg.jpg")
async def serve_login_bg():
    """Serve background scenery illustration for login page."""
    bg_file = Path("uber_login_bg.jpg")
    if not bg_file.exists():
        raise HTTPException(status_code=404, detail="Background image not found")
    return FileResponse(bg_file, media_type="image/jpeg")


@app.post("/api/login")
async def api_login(payload: LoginRequest, response: Response):
    """Authenticate credentials and issue a secure session cookie."""
    is_correct_username = secrets.compare_digest(payload.username, VIEWER_USERNAME)
    is_correct_password = secrets.compare_digest(payload.password, VIEWER_PASSWORD)

    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_session_token(payload.username)
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"status": "ok", "user": payload.username}


@app.get("/logout")
@app.post("/api/logout")
async def logout():
    """Clear session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        key="session_token",
        path="/",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/")
async def serve_root(request: Request):
    """Serve the main HTML page if authenticated via session cookie, or redirect to /login."""
    user = get_session_user(request)
    if not user:
        response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    response = FileResponse("index.html", media_type="text/html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/api/cameras", dependencies=[Depends(require_auth)])
async def get_cameras():
    """List all camera folders sorted."""
    if not CAPTURES_DIR.exists():
        return []

    cameras = sorted([d.name for d in CAPTURES_DIR.iterdir() if d.is_dir() and d.name.startswith("camera_")])
    return cameras


@app.get("/api/cameras/{camera}/dates", dependencies=[Depends(require_auth)])
async def get_dates(camera: str):
    """List date folders for a camera, sorted descending (newest first)."""
    validate_path_param(camera, "camera")
    camera_path = CAPTURES_DIR / camera

    if not camera_path.exists():
        return []

    dates = sorted([d.name for d in camera_path.iterdir() if d.is_dir()], reverse=True)
    return dates


@app.get(
    "/api/cameras/{camera}/dates/{date}/images",
    dependencies=[Depends(require_auth)],
)
async def get_images(camera: str, date: str):
    """List image filenames for a camera/date."""
    validate_path_param(camera, "camera")
    validate_path_param(date, "date")
    date_path = CAPTURES_DIR / camera / date

    if not date_path.exists():
        return []

    # Get all image files with their modification times
    image_files = [f for f in date_path.iterdir() if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]]

    # Sort by modification time descending (newest first)
    image_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return [f.name for f in image_files]


@app.get("/images/{camera}/{date}/{filename}")
async def serve_image(camera: str, date: str, filename: str, token: str = None, request: Request = None):
    """Serve image file. Supports Session Cookie, Alert Bypass Token, OR Basic Auth."""
    validate_path_param(camera, "camera")
    validate_path_param(date, "date")
    validate_path_param(filename, "filename")

    authenticated = False

    # 1. Check for valid Bypass Token
    if ALERT_BYPASS_TOKEN and token == ALERT_BYPASS_TOKEN:
        authenticated = True

    # 2. Check Session Cookie or Basic Auth
    if not authenticated and request:
        user = get_authenticated_user(request)
        if user:
            authenticated = True

    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # 3. Path Security & File Serving
    # Prevent path injection by ensuring the final path is strictly inside CAPTURES_DIR
    try:
        image_path = (CAPTURES_DIR / camera / date / filename).resolve()
        base_path = CAPTURES_DIR.resolve()

        if not str(image_path).startswith(str(base_path)):
            logging.warning("Blocked potential path injection attempt.")
            raise HTTPException(status_code=403, detail="Forbidden: Path traversal blocked")

        if not image_path.exists() or not image_path.is_file():
            raise HTTPException(status_code=404, detail="Image not found")

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logging.error(f"Error validating path: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path request")

    IMAGES_SERVED_TOTAL.inc()
    return FileResponse(image_path)
