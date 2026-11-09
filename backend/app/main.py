import asyncio
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import UPLOAD_DIR
from app.database import Base, engine, SessionLocal
from app.camera_service import initialize_camera_database
from app.health_monitor import start_staleness_monitor
from app.routers import cameras, measurements, alerts

class QuietPollingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for noisy_endpoint in ["/frame", "/status", "/active-focus", "/alerts/events", "/detections"]:
            if noisy_endpoint in msg:
                return False
        return True

logging.getLogger("uvicorn.access").addFilter(QuietPollingFilter())

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    initialize_camera_database(db)
    db.close()
    monitor_task = asyncio.create_task(start_staleness_monitor(poll_interval=10))
    yield
    monitor_task.cancel()

app = FastAPI(title="PrivateTraffic Core API", version="1.0", lifespan=lifespan)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router)
app.include_router(measurements.router)
app.include_router(alerts.router)

if os.path.exists(UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
