"""Run after integrating the independently-owned page-session alert router."""
from datetime import datetime, timezone, timedelta
import pytest
from test_data_time_gate import api
from app.routers import alerts, measurements
from app.models import CameraSourceModel
from app.health_monitor import source_state


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


@pytest.mark.skipif(not hasattr(alerts, "_sessions"), reason="Requires page-session alert implementation")
@pytest.mark.parametrize("monitor_stale", [False, True])
@pytest.mark.parametrize("source_gap, expected_alert", [(60, True), (65, True), (65.01, False)])
def test_source_window_continuity_survives_receipt_delay_and_monitor_stale(api, monkeypatch, monitor_stale, source_gap, expected_alert):
    client, sessions, _ = api
    client.app.include_router(alerts.router)
    monkeypatch.setattr(measurements, "evaluate_threshold_crossing", alerts.evaluate_threshold_crossing)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    class Clock(datetime):
        current = start + timedelta(seconds=0.1)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(measurements, "datetime", Clock)
    monkeypatch.setattr(alerts, "datetime", Clock)
    with client.websocket_connect("/api/alerts/session") as socket:
        session_id = socket.receive_json()["session_id"]
        assert client.post("/api/alerts/rules", json={"camera_id": "test", "session_id": session_id, "threshold_vpm": 25}).status_code == 201
        low = client.post("/api/measurements", json={"camera_id": "test", "timestamp": start.isoformat(), "vehicle_count": 20, "flow_per_minute": 20})
        assert low.status_code == 201 and not low.json()["alert_triggered"]
        next_time = start + timedelta(seconds=source_gap)
        Clock.current = next_time + timedelta(seconds=0.2)
        if monitor_stale:
            with sessions() as db:
                camera = db.get(CameraSourceModel, "test")
                camera.current_status = source_state(camera, Clock.current)
                assert camera.current_status == "stale"
                db.commit()
        high = client.post("/api/measurements", json={"camera_id": "test", "timestamp": next_time.isoformat(), "vehicle_count": 30, "flow_per_minute": 30})
        assert high.status_code == 201, high.text
        assert high.json()["alert_triggered"] is expected_alert
