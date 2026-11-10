import asyncio
import logging
from datetime import datetime, timezone
from app.database import SessionLocal
from app.models import CameraSourceModel
from app.config import STALE_THRESHOLD_SECONDS, OFFLINE_THRESHOLD_SECONDS

logger = logging.getLogger(__name__)

async def start_staleness_monitor(poll_interval: int = 10):
    try:
        while True:
            await asyncio.sleep(poll_interval)
            db = SessionLocal()
            now = datetime.now(timezone.utc)

            monitored_cams = db.query(CameraSourceModel).filter(
                CameraSourceModel.current_status.in_(["live", "replay", "stale", "recovering"])
            ).all()

            for cam in monitored_cams:
                if not cam.last_measurement_time:
                    if cam.current_status not in ["initializing", "replay_not_live"]:
                        cam.current_status = "offline"
                    continue

                diff = (now - cam.last_measurement_time.replace(tzinfo=timezone.utc)).total_seconds()

                if diff > OFFLINE_THRESHOLD_SECONDS:
                    if cam.current_status != "offline":
                        cam.current_status = "offline"
                        logger.warning(f"Camera {cam.id} transitioned to OFFLINE (inactive for {int(diff)}s)")
                elif diff > STALE_THRESHOLD_SECONDS:
                    if cam.current_status in ["live", "recovering"]:
                        cam.current_status = "stale"
                        logger.warning(f"Camera {cam.id} transitioned to STALE (inactive for {int(diff)}s)")

            db.commit()
            db.close()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in staleness health monitor: {e}")
