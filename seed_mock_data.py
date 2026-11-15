"""Seed isolated synthetic history; never relabel real cameras as live."""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("DATABASE_PATH", str(ROOT / "demo-history.db"))
sys.path.insert(0, str(ROOT / "backend"))
from app.database import initialize_schema, SessionLocal
from app.models import CameraSourceModel, TrafficMeasurementModel


def seed():
    initialize_schema()
    with SessionLocal() as db:
        camera_id = "demo/synthetic-history"
        if db.get(CameraSourceModel, camera_id):
            print("Synthetic history already exists; left unchanged.")
            return
        recording_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(CameraSourceModel(id=camera_id, camera_code="DEMO_HISTORY",
            title="Synthetic history — not live traffic", road_name="Software demonstration",
            latitude=44.97, longitude=-93.26, current_status="replay_not_live",
            recording_time=recording_time, is_pilot=True))
        for minute in range(60):
            count = 10 + minute % 28
            db.add(TrafficMeasurementModel(camera_id=camera_id,
                timestamp=recording_time + timedelta(minutes=minute), vehicle_count=count,
                flow_per_minute=float(count), window_seconds=60, validity="valid",
                traffic_level="unable_to_determine", source_status="replay_not_live", is_replay=True))
        db.commit()
    print("Synthetic replay history created. No live source or alert rule was changed.")


if __name__ == "__main__":
    seed()
