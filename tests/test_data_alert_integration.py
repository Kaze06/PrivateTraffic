"""Run after integrating the independently-owned page-session alert router."""
from datetime import datetime, timezone, timedelta
import pytest
from test_data_time_gate import api
from app.routers import alerts, measurements
from app.models import CameraSourceModel


@pytest.mark.skipif(not hasattr(alerts, "_sessions"), reason="Requires page-session alert implementation")
def test_real_routes_crossing_failure_recovery_and_explicit_replay(api, monkeypatch):
    client, sessions, _ = api
    client.app.include_router(alerts.router)
    monkeypatch.setattr(measurements, "evaluate_threshold_crossing", alerts.evaluate_threshold_crossing)
    def post(count, **extra):
        item = dict(camera_id="test", timestamp=datetime.now(timezone.utc).isoformat(),
                    vehicle_count=count, flow_per_minute=count)
        item.update(extra)
        response = client.post("/api/measurements", json=item)
        assert response.status_code == 201, response.text
        return response.json()["alert_triggered"]
    with client.websocket_connect("/api/alerts/session") as socket:
        session_id = socket.receive_json()["session_id"]
        rule = client.post("/api/alerts/rules", json={"camera_id": "test", "session_id": session_id, "threshold_vpm": 25})
        assert rule.status_code == 201
        assert not post(20)
        assert post(30)
        assert not post(32)
        assert not post(20)
        assert not post(0, timestamp=None, validity="invalid", no_measurement_reason="inference_failed")
        assert not post(30)  # recovery establishes a new baseline
        assert not post(20)
        assert post(30)
        with sessions() as db:
            cam = db.get(CameraSourceModel, "test")
            cam.current_status = "replay_not_live"
            cam.stream_url = "https://example.test/archive.mp4"
            cam.recording_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
            db.commit()
        assert not post(20, timestamp="2020-01-01T00:01:00Z", is_replay=False)
        with sessions() as db:
            cam = db.get(CameraSourceModel, "test")
            cam.current_status = "recovering"
            cam.recording_time = None
            db.commit()
        assert not post(30)
        events = client.get("/api/alerts/events", params={"camera_id": "test", "session_id": session_id}).json()
        assert len(events) == 2
