import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import run_detector_worker as worker
from app.database import Base, get_db
from app.models import AlertEventModel, CameraSourceModel, TrafficMeasurementModel
from app.routers import alerts, cameras, measurements


def test_receipt_window_publishes_normalized_rate_every_five_seconds():
    window = worker.ReceiptTimeWindow()
    start = datetime.now(timezone.utc)
    assert window.add(start, [1, 2]) is None
    first = window.add(start + timedelta(seconds=5), [1, 2])
    assert first["window_seconds"] == 5
    assert first["vehicle_count"] == 2
    assert first["flow_per_minute"] == 24
    assert first["timestamp_basis"] == "system_received"
    assert window.add(start + timedelta(seconds=9), [1, 2, 3]) is None
    second = window.add(start + timedelta(seconds=10), [1, 2, 3])
    assert second["vehicle_count"] == 3
    assert second["flow_per_minute"] == 18


def test_receipt_window_expires_tracks_after_one_minute():
    window = worker.ReceiptTimeWindow()
    start = datetime.now(timezone.utc)
    window.add(start, [1])
    window.add(start + timedelta(seconds=5), [1])
    result = window.add(start + timedelta(seconds=65), [])
    assert result["vehicle_count"] == 0
    assert result["window_seconds"] == 60
    assert result["flow_per_minute"] == 0


def test_receipt_window_resets_on_nonincreasing_clock():
    window = worker.ReceiptTimeWindow()
    start = datetime.now(timezone.utc)
    window.add(start, [1])
    try:
        window.add(start, [2])
    except ValueError as error:
        assert str(error) == "receipt_time_not_increasing"
    else:
        raise AssertionError("nonincreasing receipt time must reset the rolling window")
    assert window.start is None
    assert window.tracks == {}


def test_receipt_measurements_keep_basis_and_trigger_page_alert():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(CameraSourceModel(id="test", title="Synthetic", latitude=0, longitude=0,
                                 current_status="initializing"))
        db.commit()

    app = FastAPI()
    app.include_router(alerts.router)
    app.include_router(measurements.router)
    app.include_router(cameras.router)

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        with client.websocket_connect("/api/alerts/session") as socket:
            session_id = socket.receive_json()["session_id"]
            rule = client.post("/api/alerts/rules", json={
                "camera_id": "test", "session_id": session_id, "threshold_vpm": 25,
            })
            assert rule.status_code == 201
            start = datetime.now(timezone.utc) - timedelta(seconds=10)

            def post(at, count, window_seconds, flow):
                return client.post("/api/measurements", json={
                    "camera_id": "test", "timestamp": at.isoformat(),
                    "vehicle_count": count, "flow_per_minute": flow,
                    "window_seconds": window_seconds,
                    "timestamp_basis": "system_received",
                })

            low = post(start, 1, 5, 12)
            high = post(start + timedelta(seconds=5), 3, 5, 36)
            assert low.status_code == 201 and not low.json()["alert_triggered"]
            assert high.status_code == 201 and high.json()["alert_triggered"]
            assert len(client.get("/api/alerts/events", params={
                "camera_id": "test", "session_id": session_id,
            }).json()) == 1
            status = client.get("/api/cameras/test/status").json()
            assert status["timestamp_basis"] == "system_received"
            assert status["flow_per_minute"] == 36
            assert status["measurement_window_seconds"] == 5
            history = client.get("/api/history", params={"camera_id": "test"}).json()
            assert [item["timestamp_basis"] for item in history["records"]] == [
                "system_received", "system_received",
            ]
            assert [item["window_seconds"] for item in history["records"]] == [5, 5]
            source_time = datetime.now(timezone.utc)
            switched = client.post("/api/measurements", json={
                "camera_id": "test", "timestamp": source_time.isoformat(),
                "vehicle_count": 30, "flow_per_minute": 30,
            })
            assert switched.status_code == 201 and not switched.json()["alert_triggered"]
            assert len(client.get("/api/alerts/events", params={
                "camera_id": "test", "session_id": session_id,
            }).json()) == 1
            with factory() as db:
                assert db.query(TrafficMeasurementModel).count() == 3
                assert db.query(AlertEventModel).count() == 1
    engine.dispose()


def test_receipt_rate_must_match_count_and_window():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(CameraSourceModel(id="test", title="Synthetic", latitude=0, longitude=0,
                                 current_status="initializing"))
        db.commit()
    app = FastAPI()
    app.include_router(measurements.router)

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        response = client.post("/api/measurements", json={
            "camera_id": "test", "timestamp": datetime.now(timezone.utc).isoformat(),
            "vehicle_count": 3, "flow_per_minute": 3, "window_seconds": 5,
            "timestamp_basis": "system_received",
        })
        assert response.status_code == 422
    engine.dispose()


def test_source_time_measurements_keep_original_full_minute_contract():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(CameraSourceModel(id="test", title="Synthetic", latitude=0, longitude=0,
                                 current_status="initializing"))
        db.commit()
    app = FastAPI()
    app.include_router(measurements.router)

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        response = client.post("/api/measurements", json={
            "camera_id": "test", "timestamp": datetime.now(timezone.utc).isoformat(),
            "vehicle_count": 3, "flow_per_minute": 36, "window_seconds": 5,
            "timestamp_basis": "source_observation",
        })
        assert response.status_code == 422
    engine.dispose()
