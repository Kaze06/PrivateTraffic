import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import UPLOAD_DIR
from app.database import Base, engine, SessionLocal
from app.camera_service import initialize_camera_database
from app.health_monitor import start_staleness_monitor
from app.routers import cameras, measurements

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Ensure DB tables exist locally
    Base.metadata.create_all(bind=engine)
    # 2. Seed cameras from 511MN.json on startup
    db = SessionLocal()
    initialize_camera_database(db)
    db.close()
    # 3. Start background staleness health monitor
    monitor_task = asyncio.create_task(start_staleness_monitor(poll_interval=10))
    yield
    monitor_task.cancel()

app = FastAPI(title="PrivateTraffic Core API", version="0.9", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routers
app.include_router(cameras.router)
app.include_router(measurements.router)

# Mount uploaded backup videos for browser playback (AVA-1.2)
if os.path.exists(UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Mount Frontend static files
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
