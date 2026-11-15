import asyncio
import logging
import math
from datetime import datetime, timezone
from app.database import SessionLocal
from app.models import CameraSourceModel
from app.config import STALE_THRESHOLD_SECONDS, OFFLINE_THRESHOLD_SECONDS

logger = logging.getLogger(__name__)


def utc_time(value):
    # SQLite stores UTC datetimes without an offset. API inputs are checked before storage.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def is_replay_source(camera):
    return camera.current_status in {"replay", "replay_not_live"} or bool(camera.stream_url and camera.stream_url.startswith("/uploads/"))


def calibration_valid(camera):
    low, high = camera.calibration_t1, camera.calibration_t2
    return (low is not None and high is not None and math.isfinite(low) and math.isfinite(high)
            and 0 < low < high and bool(camera.calibration_note and camera.calibration_note.strip()))


def traffic_level_for(camera, flow):
    if not calibration_valid(camera):
        return "unable_to_determine"
    return "clear" if flow < camera.calibration_t1 else "normal" if flow < camera.calibration_t2 else "congested"


def source_state(camera, now=None):
    if is_replay_source(camera):
        return "replay_not_live"
    if camera.current_status not in {"live", "live_fresh", "stale", "recovering"}:
        return camera.current_status
    if camera.last_measurement_time is None:
        return "recovering" if camera.current_status == "recovering" else "offline"
    age = ((now or datetime.now(timezone.utc)) - utc_time(camera.last_measurement_time)).total_seconds()
    if age < 0 or age > OFFLINE_THRESHOLD_SECONDS:
        return "offline"
    if age > STALE_THRESHOLD_SECONDS:
        return "stale"
    return camera.current_status


async def start_staleness_monitor(poll_interval: int = 10):
    while True:
        try:
            await asyncio.sleep(poll_interval)
            with SessionLocal() as db:
                for camera in db.query(CameraSourceModel).all():
                    camera.current_status = source_state(camera)
                db.commit()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Staleness monitor failed; retrying next interval")
