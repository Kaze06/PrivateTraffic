import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.routers import measurements


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(CameraSourceModel(id="test", title="Test", latitude=0, longitude=0, current_status="initializing"))
        db.commit()
    app = FastAPI()
    app.include_router(measurements.router)
    def get_test_db():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = get_test_db
    calls = []
    monkeypatch.setattr(measurements, "evaluate_threshold_crossing", lambda *a, **k: calls.append((a, k)))
    with TestClient(app) as client:
        yield client, sessions, calls
    engine.dispose()


def payload(timestamp):
    return dict(camera_id="test", timestamp=timestamp, vehicle_count=20, flow_per_minute=20)


@pytest.mark.parametrize("timestamp", [None, "2026-01-01T12:00:00", "2000-01-01T12:00:00Z", "2099-01-01T12:00:00Z"])
def test_missing_naive_stale_future_never_advance_or_evaluate(api, timestamp):
    client, sessions, calls = api
    assert client.post("/api/measurements", json=payload(timestamp)).status_code == 422
    with sessions() as db:
        assert db.get(CameraSourceModel, "test").last_measurement_time is None
        assert db.query(TrafficMeasurementModel).count() == 0
    assert calls == []


def test_duplicate_and_out_of_order_never_replace_valid_result(api):
    client, sessions, calls = api
    now = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert client.post("/api/measurements", json=payload(now.isoformat())).status_code == 201
    for timestamp in [now, now - timedelta(seconds=1)]:
        assert client.post("/api/measurements", json=payload(timestamp.isoformat())).status_code == 409
    with sessions() as db:
        assert db.query(TrafficMeasurementModel).count() == 1
        assert db.get(CameraSourceModel, "test").last_measurement_time == now.replace(tzinfo=None)
    assert len(calls) == 1
