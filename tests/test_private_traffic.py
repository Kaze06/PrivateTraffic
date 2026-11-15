import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CameraSourceModel, TrafficMeasurementModel, AlertRuleModel, AlertEventModel

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
def setup_test_db(monkeypatch):
    from app.routers import measurements
    monkeypatch.setattr(measurements, "evaluate_threshold_crossing", lambda *args, **kwargs: None)
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
        last_measurement_time=None,
        calibration_t1=20, calibration_t2=45, calibration_note="Test fixture calibration"
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
    payload = {
        "camera_id": "camera/test1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vehicle_count": 20,
        "flow_per_minute": 20.0,
        "source_status": "live",
        "is_replay": False
    }
    res = client.post("/api/measurements", json=payload)
    assert res.status_code == 201
    assert res.json()["traffic_level"] == "normal"

    db = TestingSessionLocal()
    cam = db.query(CameraSourceModel).filter_by(id="camera/test1").first()
    cam.current_status = "stale"
    db.commit()
    db.close()

    res_stale = client.get("/api/cameras/camera/test1/status")
    assert res_stale.status_code == 200
    data = res_stale.json()
    assert data["status"] == "stale"
    assert data["traffic_level"] == "unable_to_determine"
    assert data["last_valid_time"] is not None

def test_query_thresholds_cannot_override_recorded_calibration():
    payload = {
        "camera_id": "camera/test1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vehicle_count": 22,
        "flow_per_minute": 22.0,
        "source_status": "live",
        "is_replay": False
    }
    res = client.post("/api/measurements", json=payload)
    assert res.status_code == 201

    res_clear = client.get("/api/cameras/camera/test1/status?t1=30&t2=50")
    assert res_clear.status_code == 200
    assert res_clear.json()["traffic_level"] == "normal"

    res_congested = client.get("/api/cameras/camera/test1/status?t1=10&t2=20")
    assert res_congested.status_code == 200
    assert res_congested.json()["traffic_level"] == "normal"

def test_recovering_and_offline_transitions():
    db = TestingSessionLocal()
    cam = db.query(CameraSourceModel).filter_by(id="camera/test1").first()
    cam.current_status = "recovering"
    db.commit()
    db.close()

    res_rec = client.get("/api/cameras/camera/test1/status")
    assert res_rec.status_code == 200
    assert res_rec.json()["status"] == "recovering"
    assert res_rec.json()["traffic_level"] == "unable_to_determine"

    payload = {
        "camera_id": "camera/test1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vehicle_count": 10,
        "flow_per_minute": 10.0,
        "source_status": "live",
        "is_replay": False
    }
    res = client.post("/api/measurements", json=payload)
    assert res.status_code == 201

    res_after = client.get("/api/cameras/camera/test1/status")
    assert res_after.json()["status"] == "live"
    assert res_after.json()["traffic_level"] == "clear"

def test_measurement_hands_persisted_source_time_to_alert_evaluator(monkeypatch):
    from app.routers import measurements
    calls = []
    monkeypatch.setattr(measurements, "evaluate_threshold_crossing", lambda *args, **kwargs: calls.append((args, kwargs)))
    observed = datetime.now(timezone.utc) - timedelta(seconds=2)
    response = client.post("/api/measurements", json={
        "camera_id": "camera/test1", "timestamp": observed.isoformat(),
        "vehicle_count": 30, "flow_per_minute": 30, "source_status": "live"
    })
    assert response.status_code == 201
    assert len(calls) == 1
    assert calls[0][1] == {"measurement_id": response.json()["id"], "measurement_time": observed, "validity": "valid"}
