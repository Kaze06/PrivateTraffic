"""P1-6 API handoff checks. Synthetic inputs; no footage or accuracy claim."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("PRIVATE_TRAFFIC_REPO", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

from test_data_time_gate import api
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.routers import alerts, cameras, measurements

RECORDING = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def handoff(api, monkeypatch):
    client, sessions, _ = api
    client.app.include_router(cameras.router)
    client.app.include_router(alerts.router)
    monkeypatch.setattr(measurements, "evaluate_threshold_crossing", alerts.evaluate_threshold_crossing)
    settings = json.loads((ROOT / "pilot_cameras.json").read_text(encoding="utf-8"))
    selected = next(item for item in settings if item["camera_code"] == "C8351")
    with sessions() as db:
        camera = db.get(CameraSourceModel, "test")
        camera.camera_code = "C8351"
        camera.current_status = "replay_not_live"
        camera.recording_time = RECORDING
        camera.calibration_t1 = selected["calibration_t1"]
        camera.calibration_t2 = selected["calibration_t2"]
        camera.calibration_note = selected["calibration_note"]
        db.commit()
    return client, sessions


def report(count, seconds=60):
    return dict(camera_id="test", timestamp=(RECORDING + timedelta(seconds=seconds)).isoformat(),
                vehicle_count=count, flow_per_minute=count, window_seconds=60,
                is_replay=True, source_status="replay_not_live")


@pytest.mark.parametrize("count, level", [(32, "clear"), (33, "normal"),
                                         (141, "normal"), (142, "congested")])
def test_configured_boundaries_persist_and_reach_status_and_history(handoff, count, level):
    client, sessions = handoff
    response = client.post("/api/measurements", json=report(count))
    assert response.status_code == 201, response.text
    assert response.json()["traffic_level"] == level
    status = client.get("/api/cameras/test/status")
    assert status.status_code == 200, status.text
    data = status.json()
    assert data["status"] == "replay_not_live" and data["is_replay"]
    assert data["calibrated"] and data["traffic_level"] == level
    assert data["flow_per_minute"] == data["vehicle_count"] == count
    assert datetime.fromisoformat(data["measurement_time"].replace("Z", "+00:00")) == RECORDING + timedelta(seconds=60)
    assert datetime.fromisoformat(data["recording_time"].replace("Z", "+00:00")) == RECORDING
    assert data["last_valid_time"] is None
    history = client.get("/api/history", params={"camera_id": "test"})
    assert history.status_code == 200, history.text
    records = history.json()["records"]
    assert len(records) == 1
    assert records[0]["vehicle_count"] == count and records[0]["traffic_level"] == level
    assert records[0]["source_status"] == "replay_not_live"
    with sessions() as db:
        saved = db.query(TrafficMeasurementModel).one()
        assert saved.vehicle_count == count and saved.is_replay
        assert db.get(CameraSourceModel, "test").last_measurement_time is None


def test_replay_crossing_never_alerts_and_duplicate_never_adds_history(handoff):
    client, sessions = handoff
    with client.websocket_connect("/api/alerts/session") as socket:
        session_id = socket.receive_json()["session_id"]
        rule = client.post("/api/alerts/rules", json={
            "camera_id": "test", "session_id": session_id, "threshold_vpm": 142})
        assert rule.status_code == 201, rule.text
        for seconds, count in [(60, 141), (120, 142), (180, 143)]:
            response = client.post("/api/measurements", json=report(count, seconds))
            assert response.status_code == 201, response.text
            assert response.json()["alert_triggered"] is False
        duplicate = client.post("/api/measurements", json=report(143, 180))
        assert duplicate.status_code == 409
        events = client.get("/api/alerts/events", params={"camera_id": "test", "session_id": session_id})
        assert events.status_code == 200 and events.json() == []
    with sessions() as db:
        assert db.query(TrafficMeasurementModel).count() == 3
        assert db.get(CameraSourceModel, "test").last_measurement_time is None


def test_invalid_replay_keeps_valid_history_but_hides_current_count(handoff):
    client, _ = handoff
    assert client.post("/api/measurements", json=report(33)).status_code == 201
    invalid = report(0, 120)
    invalid.update(timestamp=None, validity="invalid", no_measurement_reason="inference_failed")
    response = client.post("/api/measurements", json=invalid)
    assert response.status_code == 201, response.text
    assert response.json()["alert_triggered"] is False
    status = client.get("/api/cameras/test/status").json()
    assert status["status"] == "replay_not_live"
    assert status["vehicle_count"] is None and status["flow_per_minute"] is None
    assert status["traffic_level"] == "unable_to_determine"
    assert status["no_measurement_reason"] == "inference_failed"
    records = client.get("/api/history", params={"camera_id": "test"}).json()["records"]
    assert len(records) == 1 and records[0]["vehicle_count"] == 33
