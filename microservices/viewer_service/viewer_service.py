from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI()

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

    images = sorted([
        f.name for f in date_path.iterdir()
        if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]
    ], reverse=True)

    return images


@app.get("/images/{camera}/{date}/{filename}")
async def serve_image(camera: str, date: str, filename: str):
    """Serve image file."""
    image_path = CAPTURES_DIR / camera / date / filename

    # Security: prevent directory traversal
    if not image_path.exists() or not image_path.is_file():
        return FileResponse("", status_code=404)

    if not str(image_path).startswith(str(CAPTURES_DIR)):
        return FileResponse("", status_code=403)

    return FileResponse(image_path)
