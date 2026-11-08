import sys
from pathlib import Path

# Ensure backend directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CameraSourceModel, TrafficMeasurementModel, AlertRuleModel, AlertEventModel

# StaticPool ensures all connections and worker threads share the single in-memory SQLite instance
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    cam = CameraSourceModel(
        id="camera/test1",
        camera_code="C_TEST",
        title="Test Camera Route 1",
        road_name="MN 100",
        latitude=44.97,
        longitude=-93.26,
        stream_url="https://video.dot.state.mn.us/test.m3u8",
        is_pilot=True,
        current_status="live",
        last_measurement_time=datetime.now(timezone.utc)
    )
    db.add(cam)
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)

def test_source_compliance_endpoint():
    res = client.get("/api/cameras/compliance")
    assert res.status_code == 200
    data = res.json()
    assert "MnDOT" in data["source_name"]
    assert "GPS-free" in data["gps_policy"]
    assert "Aggregate" in data["data_retention"]

def test_measurement_flow_and_level_calculation():
    # Submit measurement with 20 vehicles over 60s window
    payload = {
        "camera_id": "camera/test1",
        "vehicle_count": 20,
        "flow_per_minute": 20.0,
        "source_status": "live",
        "is_replay": False
    }
    res = client.post("/api/measurements", json=payload)
    assert res.status_code == 201
    assert res.json()["traffic_level"] == "normal"

    # Verify status endpoint reflects state updates
    db = TestingSessionLocal()
    cam = db.query(CameraSourceModel).filter_by(id="camera/test1").first()
    cam.current_status = "stale"
    db.commit()
    db.close()

    res_stale = client.get("/api/cameras/camera/test1/status")
    assert res_stale.status_code == 200
    assert res_stale.json()["status"] == "stale"

def test_alert_rule_crossing_and_rearm():
    # 1. Set threshold of 25.0 vpm
    res_rule = client.post("/api/alerts/rules", json={
        "camera_id": "camera/test1",
        "threshold_vpm": 25.0
    })
    assert res_rule.status_code == 201

    # 2. Measurement below threshold (20 vpm) -> No alert
    res_m1 = client.post("/api/measurements", json={
        "camera_id": "camera/test1",
        "vehicle_count": 20,
        "flow_per_minute": 20.0,
        "source_status": "live",
        "is_replay": False
    })
    assert res_m1.status_code == 201
    assert res_m1.json()["alert_triggered"] is False

    # 3. Measurement crosses threshold (30 vpm) -> Triggers alert
    res_m2 = client.post("/api/measurements", json={
        "camera_id": "camera/test1",
        "vehicle_count": 30,
        "flow_per_minute": 30.0,
        "source_status": "live",
        "is_replay": False
    })
    assert res_m2.status_code == 201
    assert res_m2.json()["alert_triggered"] is True

    # 4. Successive measurement still above (32 vpm) -> Suppressed, no repeat
    res_m3 = client.post("/api/measurements", json={
        "camera_id": "camera/test1",
        "vehicle_count": 32,
        "flow_per_minute": 32.0,
        "source_status": "live",
        "is_replay": False
    })
    assert res_m3.status_code == 201
    assert res_m3.json()["alert_triggered"] is False

    # 5. Flow drops below threshold (15 vpm) -> Re-arms
    res_m4 = client.post("/api/measurements", json={
        "camera_id": "camera/test1",
        "vehicle_count": 15,
        "flow_per_minute": 15.0,
        "source_status": "live",
        "is_replay": False
    })
    assert res_m4.status_code == 201

    # 6. Replay clips must never trigger live alerts
    res_replay = client.post("/api/measurements", json={
        "camera_id": "camera/test1",
        "vehicle_count": 50,
        "flow_per_minute": 50.0,
        "source_status": "replay_not_live",
        "is_replay": True
    })
    assert res_replay.json()["alert_triggered"] is False
