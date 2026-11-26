import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.detector_manager import DetectorManager
from app.models import CameraSourceModel
from app.routers import detection


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_manager_starts_once_reuses_worker_and_stops(monkeypatch):
    manager = DetectorManager()
    process = FakeProcess()
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr("app.detector_manager.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.detector_manager.frame_metadata", lambda camera_id: None)

    first = manager.start("camera-a", "http://127.0.0.1:8002/")
    second = manager.start("camera-b", "http://127.0.0.1:8002/")

    assert first == {"state": "starting", "camera_id": "camera-a"}
    assert second == {"state": "starting", "camera_id": "camera-b"}
    assert len(calls) == 1
    assert calls[0][0][-1] == "--receipt-time"
    assert calls[0][1]["env"]["API_BASE_URL"] == "http://127.0.0.1:8002"
    monkeypatch.setattr("app.detector_manager.frame_metadata", lambda camera_id: {"usable": True})
    assert manager.status("camera-b")["state"] == "running"
    assert manager.stop() == {"state": "idle", "camera_id": None}
    assert process.terminated


def test_manager_reports_worker_exit(monkeypatch):
    manager = DetectorManager()
    process = FakeProcess()
    calls = []
    monkeypatch.setattr("app.detector_manager.subprocess.Popen",
                        lambda *args, **kwargs: calls.append(kwargs) or process)
    manager.start("camera-a", "http://127.0.0.1:8000")
    calls[0]["stdout"].write("Traceback (most recent call last)\nModuleNotFoundError: No module named 'torch'\n")
    calls[0]["stdout"].flush()
    process.returncode = 2

    assert manager.status() == {
        "state": "failed", "camera_id": "camera-a", "exit_code": 2,
        "detail": "ModuleNotFoundError: No module named 'torch'",
    }


def test_detection_start_selects_live_camera_and_rejects_replay(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([
            CameraSourceModel(id="live-camera", title="Live", latitude=0, longitude=0,
                              stream_url="https://example.test/live.m3u8", current_status="initializing"),
            CameraSourceModel(id="replay-camera", title="Replay", latitude=0, longitude=0,
                              stream_url="/uploads/clip.mp4", current_status="replay_not_live"),
        ])
        db.commit()

    app = FastAPI()
    app.include_router(detection.router)

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    selected = []
    starts = []
    monkeypatch.setattr(detection, "set_active_focus", lambda payload: selected.append(payload["camera_id"]))
    monkeypatch.setattr(detection, "missing_detector_dependencies", lambda: [])
    monkeypatch.setattr(detection.detector_manager, "start",
                        lambda camera_id, api_base_url: starts.append((camera_id, api_base_url))
                        or {"state": "starting", "camera_id": camera_id})

    with TestClient(app) as client:
        response = client.post("/api/detection/start", json={"camera_id": "live-camera"})
        replay = client.post("/api/detection/start", json={"camera_id": "replay-camera"})

    assert response.status_code == 200
    assert response.json() == {"state": "starting", "camera_id": "live-camera"}
    assert selected == ["live-camera"]
    assert starts == [("live-camera", "http://testserver")]
    assert replay.status_code == 409
    engine.dispose()


def test_detection_start_explains_missing_detector_dependencies(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(CameraSourceModel(id="live-camera", title="Live", latitude=0, longitude=0,
                                 stream_url="https://example.test/live.m3u8", current_status="initializing"))
        db.commit()

    app = FastAPI()
    app.include_router(detection.router)

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    monkeypatch.setattr(detection, "missing_detector_dependencies", lambda: ["torch", "rfdetr"])
    monkeypatch.setattr(detection.detector_manager, "start",
                        lambda *args: (_ for _ in ()).throw(AssertionError("worker must not start")))

    with TestClient(app) as client:
        response = client.post("/api/detection/start", json={"camera_id": "live-camera"})

    assert response.status_code == 503
    assert "torch, rfdetr" in response.json()["detail"]
    assert "Install the project requirements" in response.json()["detail"]
    engine.dispose()
