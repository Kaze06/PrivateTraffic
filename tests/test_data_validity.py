import sys
import queue
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_data_time_gate import api, payload
import pytest
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.routers import cameras
from app import video_stream
from app.detector import predict_vehicles, InferenceUnavailable
import run_detector_worker as worker


def attach_cameras(api, monkeypatch, tmp_path):
    client, sessions, calls = api
    client.app.include_router(cameras.router)
    monkeypatch.setattr(cameras, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(video_stream, "SHM_DIR", tmp_path / "frames")
    monkeypatch.setattr(cameras, "load_pilot_codes", lambda: {})
    monkeypatch.setattr(cameras, "evaluate_threshold_crossing", lambda *a, **k: None)
    return client, sessions, calls


@pytest.mark.parametrize("change", [{"window_seconds": 30}, {"flow_per_minute": 21}, {"validity": "maybe"}])
def test_inconsistent_window_count_or_validity_rejected(api, change):
    client, sessions, calls = api
    item = payload(datetime.now(timezone.utc).isoformat())
    item.update(change)
    assert client.post("/api/measurements", json=item).status_code == 422
    assert not calls


def test_invalid_report_keeps_last_valid_without_fabricating_time(api, monkeypatch, tmp_path):
    client, sessions, calls = attach_cameras(api, monkeypatch, tmp_path)
    observed = datetime.now(timezone.utc) - timedelta(seconds=2)
    assert client.post("/api/measurements", json=payload(observed.isoformat())).status_code == 201
    report = payload(None)
    report.update(validity="invalid", no_measurement_reason="vehicle_inference_failed", vehicle_count=0, flow_per_minute=0)
    assert client.post("/api/measurements", json=report).status_code == 201
    with sessions() as db:
        latest = db.query(TrafficMeasurementModel).order_by(TrafficMeasurementModel.id.desc()).first()
        assert latest.timestamp is None
        assert db.get(CameraSourceModel, "test").last_measurement_time == observed.replace(tzinfo=None)
    status = client.get("/api/cameras/test/status").json()
    assert status["status"] == "recovering"
    assert status["vehicle_count"] is None
    assert status["traffic_level"] == "unable_to_determine"
    assert status["no_measurement_reason"] == "vehicle_inference_failed"
    assert calls[-1][1]["validity"] == "invalid"


def test_freshness_checked_on_read_without_monitor_or_overlay_mtime(api, monkeypatch, tmp_path):
    client, sessions, _ = attach_cameras(api, monkeypatch, tmp_path)
    old = datetime.now(timezone.utc) - timedelta(seconds=61)
    with sessions() as db:
        cam = db.get(CameraSourceModel, "test")
        cam.current_status, cam.last_measurement_time = "live", old
        cam.calibration_t1, cam.calibration_t2, cam.calibration_note = 20, 45, "Fixture evidence"
        db.add(TrafficMeasurementModel(camera_id="test", timestamp=old, vehicle_count=20, flow_per_minute=20,
                                      traffic_level="normal", source_status="live", validity="valid", is_replay=False))
        db.commit()
    frame = video_stream.get_shm_frame_path("test")
    frame.parent.mkdir()
    frame.write_bytes(b"new overlay")
    status = client.get("/api/cameras/test/status").json()
    assert status["status"] == "stale"
    assert status["traffic_level"] == "unable_to_determine"
    assert status["flow_per_minute"] is None
    assert not status["has_augmented_stream"]


def test_uncalibrated_despite_query_or_payload_override(api, monkeypatch, tmp_path):
    client, _, _ = attach_cameras(api, monkeypatch, tmp_path)
    item = payload(datetime.now(timezone.utc).isoformat())
    item["traffic_level"] = "congested"
    assert client.post("/api/measurements", json=item).json()["traffic_level"] == "unable_to_determine"
    status = client.get("/api/cameras/test/status?t1=1&t2=2").json()
    assert status["traffic_level"] == "unable_to_determine"
    assert status["no_measurement_reason"] == "calibration_unavailable"
    assert status["vehicle_count"] == 20


def test_old_replay_does_not_borrow_live_measurement_and_live_recovers(api, monkeypatch, tmp_path):
    client, sessions, calls = attach_cameras(api, monkeypatch, tmp_path)
    observed = datetime.now(timezone.utc) - timedelta(seconds=5)
    client.post("/api/measurements", json=payload(observed.isoformat()))
    recording = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with sessions() as db:
        cam = db.get(CameraSourceModel, "test")
        cam.current_status, cam.recording_time = "replay_not_live", recording
        db.commit()
    assert client.get("/api/cameras/test/status").json()["vehicle_count"] is None
    replay = payload((recording + timedelta(seconds=60)).isoformat())
    replay["is_replay"] = True
    assert client.post("/api/measurements", json=replay).status_code == 201
    after = client.get("/api/cameras/test/status").json()
    assert after["is_replay"] and after["vehicle_count"] == 20
    assert after["recording_time"].startswith("2020-01-01")
    assert calls[-1][0][4] is True
    with sessions() as db:
        cam = db.get(CameraSourceModel, "test")
        cam.current_status, cam.recording_time = "initializing", None
        db.commit()
    pending = client.get("/api/cameras/test/status").json()
    assert not pending["is_replay"] and pending["vehicle_count"] is None
    assert client.post("/api/measurements", json=payload(datetime.now(timezone.utc).isoformat())).status_code == 201
    assert client.get("/api/cameras/test/status").json()["status"] == "live"


@pytest.mark.parametrize("recording", [None, "2020-01-01T00:00:00", "2099-01-01T00:00:00Z"])
def test_upload_requires_real_aware_recording_time(api, monkeypatch, tmp_path, recording):
    client, sessions, _ = attach_cameras(api, monkeypatch, tmp_path)
    with sessions() as db:
        db.get(CameraSourceModel, "test").camera_code = "TEST"
        db.commit()
    data = {"camera_code": "TEST"}
    if recording:
        data["recording_time"] = recording
    response = client.post("/api/cameras/upload-replay", data=data, files={"video_file": ("video.mp4", b"fixture")})
    assert response.status_code == 422
    assert list(tmp_path.iterdir()) == []


def test_purge_only_replay_camera_and_its_file(api, monkeypatch, tmp_path):
    client, sessions, _ = attach_cameras(api, monkeypatch, tmp_path)
    keep = tmp_path / "unrelated.txt"
    keep.write_text("keep")
    replay = tmp_path / "replay.mp4"
    replay.write_bytes(b"fixture")
    with sessions() as db:
        cam = db.get(CameraSourceModel, "test")
        cam.stream_url, cam.current_status = "https://custom.example/live.m3u8", "live"
        db.add(CameraSourceModel(id="replay", title="Replay", latitude=0, longitude=0,
                                 stream_url="/uploads/replay.mp4", current_status="replay_not_live"))
        db.commit()
    result = client.post("/api/cameras/purge-replays").json()
    assert result["deleted_files"] == result["cameras_reset"] == 1
    assert keep.is_file() and not replay.exists()
    with sessions() as db:
        assert db.get(CameraSourceModel, "test").stream_url == "https://custom.example/live.m3u8"
        assert db.get(CameraSourceModel, "replay").current_status == "unavailable"


class Detections:
    def __init__(self, classes):
        self.class_id = classes
    def __getitem__(self, mask):
        return Detections([c for c, include in zip(self.class_id, mask) if include])


def test_rfdetr_1121_vehicle_categories_exclude_old_yolo_ids():
    engine = SimpleNamespace(predict=lambda *a, **kw: Detections(list(range(1, 10))))
    assert predict_vehicles(engine, object(), 0.3).class_id == [3, 4, 6, 8]


def test_inference_failure_and_missing_model_are_not_zero_vehicles():
    with pytest.raises(InferenceUnavailable, match="model_unavailable"):
        predict_vehicles(None, object(), 0.3)
    def fail(*a, **kw):
        raise RuntimeError("fixture inference failure")
    with pytest.raises(InferenceUnavailable, match="inference_failed"):
        predict_vehicles(SimpleNamespace(predict=fail), object(), 0.3)
    assert predict_vehicles(SimpleNamespace(predict=lambda *a, **kw: Detections([])), object(), 0.3).class_id == []


def test_empty_reader_never_reuses_last_frame():
    reader = worker.MonotonicPacedReader.__new__(worker.MonotonicPacedReader)
    reader.frame_queue = queue.Queue()
    reader.last_frame = object()
    assert reader.get_paced_frame() is None
    packet = worker.SourceFrame(object(), None, 1, False)
    reader.frame_queue.put(packet)
    assert reader.get_paced_frame() is packet
    assert reader.get_paced_frame() is None


def test_window_count_rate_timestamp_use_source_time():
    window = worker.SourceWindow()
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for second in range(60):
        assert window.add(start + timedelta(seconds=second), [1, 1, -1]) is None
    result = window.add(start + timedelta(seconds=60), [2])
    assert result == {"timestamp": (start + timedelta(seconds=60)).isoformat(), "vehicle_count": 2,
                      "flow_per_minute": 2.0, "window_seconds": 60, "validity": "valid"}


@pytest.mark.parametrize("delta", [0, -1, 6])
def test_window_rejects_repeated_backward_or_interrupted_observations(delta):
    window = worker.SourceWindow()
    start = datetime.now(timezone.utc)
    window.add(start, [1])
    with pytest.raises(ValueError):
        window.add(start + timedelta(seconds=delta), [2])
    assert window.start is None and not window.ids


def test_processor_reports_unavailable_timestamp_and_inference_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(video_stream, "SHM_DIR", tmp_path)
    sent = []
    def post(url, json, timeout):
        sent.append(json)
        return SimpleNamespace(raise_for_status=lambda: None)
    def fail(frame):
        raise InferenceUnavailable("vehicle_inference_failed")
    processor = worker.ObservationProcessor("test", SimpleNamespace(process_frame=fail, reset=lambda: None), SimpleNamespace(post=post))
    processor.process(worker.SourceFrame(object(), None, 1, False, "live_capture_timestamp_unavailable"))
    assert sent[0]["timestamp"] is None and sent[0]["validity"] == "invalid"
    assert sent[0]["no_measurement_reason"] == "live_capture_timestamp_unavailable"
    processor.process(worker.SourceFrame(object(), datetime.now(timezone.utc), 2, False))
    assert sent[1]["validity"] == "invalid"
    assert sent[1]["no_measurement_reason"] == "vehicle_inference_failed"


def test_overlay_rewrite_cannot_refresh_old_source_time(monkeypatch, tmp_path):
    monkeypatch.setattr(video_stream, "SHM_DIR", tmp_path)
    path = video_stream.get_shm_frame_path("test")
    path.write_bytes(b"new overlay")
    path.with_suffix(".json").write_text(json.dumps({
        "usable": True, "observed_at": (datetime.now(timezone.utc)-timedelta(seconds=61)).isoformat(),
        "written_at": time.time(), "is_replay": False, "sequence": "old"}))


    assert not video_stream.stream_manager.has_active_stream("test")
def test_frame_update_does_not_replace_files_open_by_dashboard(monkeypatch, tmp_path):
    import cv2

    monkeypatch.setattr(video_stream, "SHM_DIR", tmp_path)
    monkeypatch.setattr(cv2, "imencode", lambda *args, **kwargs: (True, SimpleNamespace(tobytes=lambda: b"jpeg")))
    monkeypatch.setattr(Path, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("file is open")))
    packet = worker.SourceFrame(object(), datetime.now(timezone.utc), 1, False)

    worker.write_shm_frame("test", object(), packet)

    frame = video_stream.get_shm_frame_path("test")
    assert frame.read_bytes() == b"jpeg"
    assert video_stream.frame_metadata("test")["sequence"].endswith(":1")




def test_cli_replay_setup_persists_time_then_real_worker_submission_succeeds(api, monkeypatch, tmp_path):
    client, sessions, calls = attach_cameras(api, monkeypatch, tmp_path)
    with sessions() as db:
        camera = db.get(CameraSourceModel, "test")
        camera.camera_code = "TEST"
        camera.stream_url = "https://custom.example/live.m3u8"
        db.commit()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"isolated upload fixture")
    source = worker.configure_cli_replay(str(clip), "2020-01-01T00:00:00+08:00", {"camera_code": "TEST"}, client)
    recording = datetime(2019, 12, 31, 16, tzinfo=timezone.utc)
    with sessions() as db:
        camera = db.get(CameraSourceModel, "test")
        assert camera.recording_time == recording.replace(tzinfo=None)
        assert camera.current_status == "replay_not_live"
        assert camera.stream_url == source
        assert camera.original_stream_url == "https://custom.example/live.m3u8"
    monkeypatch.setattr(worker, "write_shm_frame", lambda *args: None)
    detector = SimpleNamespace(process_frame=lambda image: (image, 1, SimpleNamespace(tracker_id=[1])), reset=lambda: None)
    processor = worker.ObservationProcessor("test", detector, client)
    for second in range(61):
        processor.process(worker.SourceFrame(object(), recording + timedelta(seconds=second), second, True))
    status = client.get("/api/cameras/test/status").json()
    assert status["is_replay"] and status["vehicle_count"] == 1
    assert status["measurement_time"].startswith("2019-12-31T16:01:00")
    with sessions() as db:
        assert db.query(TrafficMeasurementModel).count() == 1
        measurement = db.query(TrafficMeasurementModel).one()
        assert measurement.validity == "valid" and measurement.is_replay
    assert client.post("/api/cameras/purge-replays").json()["cameras_reset"] == 1
    with sessions() as db:
        assert db.get(CameraSourceModel, "test").stream_url == "https://custom.example/live.m3u8"
    assert clip.exists()  # purge removes the registered copy, not the caller's file


def test_cli_remote_replay_setup_marks_mode_and_recording_time(api, monkeypatch, tmp_path):
    client, sessions, _ = attach_cameras(api, monkeypatch, tmp_path)
    with sessions() as db:
        db.get(CameraSourceModel, "test").camera_code = "TEST"
        db.commit()
    url = "https://example.test/clip.mp4"
    assert worker.configure_cli_replay(url, "2020-01-01T00:00:00Z", {"camera_code": "TEST"}, client) == url
    status = client.get("/api/cameras/test/status").json()
    assert status["is_replay"] and status["recording_time"].startswith("2020-01-01")


@pytest.mark.parametrize("validity", ["valid", "invalid"])
def test_purge_rejects_late_replay_reports_without_restoring_replay_mode(api, monkeypatch, tmp_path, validity):
    client, sessions, calls = attach_cameras(api, monkeypatch, tmp_path)
    with sessions() as db:
        camera = db.get(CameraSourceModel, "test")
        camera.camera_code = "TEST"
        camera.stream_url = "https://example.test/live.m3u8"
        db.commit()
    worker.configure_cli_replay("https://example.test/clip.mp4", "2020-01-01T00:00:00Z", {"camera_code": "TEST"}, client)
    assert client.post("/api/cameras/purge-replays").json()["cameras_reset"] == 1
    report = dict(camera_id="test", timestamp="2020-01-01T00:01:00Z" if validity == "valid" else None,
                  vehicle_count=0, flow_per_minute=0, is_replay=True, validity=validity,
                  no_measurement_reason="replay_ended" if validity == "invalid" else None)
    assert client.post("/api/measurements", json=report).status_code == 409
    with sessions() as db:
        camera = db.get(CameraSourceModel, "test")
        assert camera.current_status == "initializing"
        assert camera.stream_url == "https://example.test/live.m3u8"
        assert camera.recording_time is None
        assert db.query(TrafficMeasurementModel).count() == 0
    assert calls == []


def test_cli_worker_stops_when_replay_is_purged_during_active_read(api, monkeypatch, tmp_path):
    import itertools
    import requests
    client, sessions, _ = attach_cameras(api, monkeypatch, tmp_path)
    with sessions() as db:
        camera = db.get(CameraSourceModel, "test")
        camera.camera_code = "TEST"
        camera.stream_url = "https://example.test/live.m3u8"
        db.commit()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"isolated CLI fixture")
    measurement_codes = []
    class Session:
        def get(self, *args, **kwargs):
            return client.get(*args, **kwargs)
        def post(self, url, **kwargs):
            response = client.post(url, **kwargs)
            if url.endswith("/api/measurements"):
                measurement_codes.append(response.status_code)
                # Use the worker's HTTP exception type, independent of TestClient's backend.
                worker_response = requests.Response()
                worker_response.status_code = response.status_code
                worker_response.reason = response.reason_phrase
                worker_response.url = url
                return worker_response
            return response
        def close(self):
            pass
    readers = []
    class Reader:
        def __init__(self, *args, **kwargs):
            self.is_replay, self.ended, self.stopped = True, True, False
            readers.append(self)
        def get_paced_frame(self):
            assert client.post("/api/cameras/purge-replays").json()["cameras_reset"] == 1
            return None
        def stop(self):
            self.stopped = True
    monkeypatch.setattr(requests, "Session", Session)
    monkeypatch.setattr(sys, "argv", ["worker", "--source", str(clip), "--recording-time", "2020-01-01T00:00:00Z"])
    monkeypatch.setattr(worker, "get_default_camera", lambda session: {"id": "test", "camera_code": "TEST"})
    monkeypatch.setattr(worker, "VehicleDetector", lambda: SimpleNamespace(reset=lambda: None))
    monkeypatch.setattr(worker, "MonotonicPacedReader", Reader)
    ticks = itertools.count(0, 3)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    worker.main()
    assert len(readers) == 1 and readers[0].stopped
    assert measurement_codes == [409]
    status = client.get("/api/cameras/test/status").json()
    assert not status["is_replay"] and status["recording_time"] is None
    assert status["stream_url"] == "https://example.test/live.m3u8"
