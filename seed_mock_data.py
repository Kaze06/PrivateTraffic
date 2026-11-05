"""
seed_mock_data.py
Populates SQLite database with simulated measurements for the pilot cameras.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add 'backend' folder to Python path so 'app' can be imported from root
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.database import SessionLocal, Base, engine
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.camera_service import initialize_camera_database

def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Ensure cameras are initialized from 511MN.json
    initialize_camera_database(db)

    pilot_cams = db.query(CameraSourceModel).filter(CameraSourceModel.is_pilot == True).all()
    if not pilot_cams:
        print("No pilot cameras found. Make sure 511MN.json and pilot_cameras.json exist in root.")
        db.close()
        return

    now = datetime.now(timezone.utc)
    print(f"Seeding historical traffic data for {len(pilot_cams)} pilot cameras...")

    for cam in pilot_cams:
        # Generate 60 minutes of past data (1-minute intervals)
        for i in range(60, 0, -1):
            ts = now - timedelta(minutes=i)
            # Simulate a realistic traffic wave
            count = 12 + (i % 20)
            flow = float(count)
            level = "clear" if flow < 15 else ("normal" if flow < 30 else "congested")

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

        # Update latest status on the camera
        cam.current_status = "live"
        cam.last_measurement_time = now

    db.commit()
    db.close()
    print("Seeding complete! Database 'traffic.db' populated with 60 minutes of mock history.")

if __name__ == "__main__":
    seed()
