import asyncio
import logging
from datetime import datetime, timezone
from app.database import SessionLocal
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.config import STALE_THRESHOLD_SECONDS, OFFLINE_THRESHOLD_SECONDS

logger = logging.getLogger(__name__)

async def start_staleness_monitor(poll_interval: int = 10):
    while True:
        try:
            await asyncio.sleep(poll_interval)
            db = SessionLocal()
            now = datetime.now(timezone.utc)

            active_cams = db.query(CameraSourceModel).filter(
                CameraSourceModel.current_status.in_(["live_fresh", "live", "recovering", "stale"])
            ).all()

            for cam in active_cams:
                if not cam.last_measurement_time:
                    cam.current_status = "offline"
                    continue

                diff = (now - cam.last_measurement_time.replace(tzinfo=timezone.utc)).total_seconds()
                if diff > OFFLINE_THRESHOLD_SECONDS:
                    if cam.current_status != "offline":
                        cam.current_status = "offline"
                        logger.warning(f"Camera {cam.id} marked OFFLINE (no update for {int(diff)}s)")
                elif diff > STALE_THRESHOLD_SECONDS:
                    if cam.current_status != "stale":
                        cam.current_status = "stale"
                        logger.warning(f"Camera {cam.id} marked STALE (no update for {int(diff)}s)")

            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Error in staleness health monitor: {e}")
