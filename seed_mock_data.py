"""
seed_mock_data.py
Populates SQLite database with simulated measurements and a sample alert rule
for pilot cameras for testing and evaluation without requiring live CV inference.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add 'backend' folder to Python path so 'app' can be imported from root
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.database import SessionLocal, Base, engine
from app.models import CameraSourceModel, TrafficMeasurementModel, AlertRuleModel
from app.camera_service import initialize_camera_database
from app.config import DEFAULT_T1, DEFAULT_T2

def seed():
    # Recreate all tables including alert_rules and alert_events
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Populate camera catalog from 511MN.json and pilot_cameras.json
    initialize_camera_database(db)

    pilot_cams = db.query(CameraSourceModel).filter(CameraSourceModel.is_pilot == True).all()
    if not pilot_cams:
        print("No pilot cameras found. Ensure 511MN.json and pilot_cameras.json exist in root.")
        db.close()
        return

    now = datetime.now(timezone.utc)
    print(f"Seeding historical traffic data for {len(pilot_cams)} pilot cameras...")

    for cam in pilot_cams:
        # Generate 60 minutes of past data (1-minute intervals)
        for i in range(60, 0, -1):
            ts = now - timedelta(minutes=i)
            # Realistic wave pattern varying between clear, normal, and peak
            count = 10 + (i % 28)
            flow = float(count)

            if flow < DEFAULT_T1:
                level = "clear"
            elif flow < DEFAULT_T2:
                level = "normal"
            else:
                level = "congested"

            meas = TrafficMeasurementModel(
                camera_id=cam.id,
                timestamp=ts,
                vehicle_count=count,
                flow_per_minute=flow,
                traffic_level=level,
                source_status="live",
                is_replay=False
            )
            db.add(meas)

        # Update latest camera state
        cam.current_status = "live"
        cam.last_measurement_time = now

        # Add a default sample alert rule (threshold: 30.0 vpm)
        existing_rule = db.query(AlertRuleModel).filter(AlertRuleModel.camera_id == cam.id).first()
        if not existing_rule:
            db.add(AlertRuleModel(
                camera_id=cam.id,
                threshold_vpm=30.0,
                enabled=True
            ))

    db.commit()
    db.close()
    print("Seeding complete! Database 'traffic.db' populated with fresh schema, history, and alert rules.")

if __name__ == "__main__":
    seed()
