"""P0-4: source-clock failures must not preserve a live counting window."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import run_detector_worker as worker


class Detector:
    def __init__(self):
        self.resets = 0
        self.calls = 0

    def process_frame(self, image):
        self.calls += 1
        return image, None, SimpleNamespace(tracker_id=[1])

    def reset(self):
        self.resets += 1


class Session:
    def __init__(self):
        self.sent = []

    def post(self, url, json, timeout):
        self.sent.append(json.copy())
        return SimpleNamespace(raise_for_status=lambda: None)


@pytest.mark.parametrize("bad_time", [datetime(2026, 1, 1), "not-a-timestamp", 123])
def test_untrusted_time_invalidates_window_and_allows_new_start(monkeypatch, bad_time):
    cleared = []
    monkeypatch.setattr(worker, "clear_frame", cleared.append)
    monkeypatch.setattr(worker, "write_shm_frame", lambda *args: None)
    detector, session = Detector(), Session()
    processor = worker.ObservationProcessor("test", detector, session)
    now = datetime.now(timezone.utc)
    first = now - timedelta(seconds=10)
    processor.process(worker.SourceFrame(object(), first, 1, False))
    assert processor.window.start == first
    processor.process(worker.SourceFrame(object(), bad_time, 2, False))
    assert len(session.sent) == 1
    assert session.sent[0]["timestamp"] is None
    assert session.sent[0]["validity"] == "invalid"
    assert session.sent[0]["no_measurement_reason"] == "source_timestamp_unavailable"
    assert processor.window.start is None and not processor.window.ids
    assert detector.resets == 1 and cleared == ["test"]
    next_time = now - timedelta(seconds=5)
    processor.process(worker.SourceFrame(object(), next_time, 3, False))
    assert processor.window.start == next_time
    assert len(session.sent) == 1


@pytest.mark.parametrize("offset", [-61, 61])
def test_stale_or_future_live_time_is_invalid_before_inference(monkeypatch, offset):
    monkeypatch.setattr(worker, "clear_frame", lambda camera_id: None)
    detector, session = Detector(), Session()
    processor = worker.ObservationProcessor("test", detector, session)
    processor.process(worker.SourceFrame(object(), datetime.now(timezone.utc) + timedelta(seconds=offset), 1, False))
    assert detector.calls == 0
    assert session.sent[0]["validity"] == "invalid"
    assert session.sent[0]["no_measurement_reason"] == "source_observation_not_fresh"


def test_aware_iso_time_is_normalized_before_frame_metadata(monkeypatch):
    observed = []
    monkeypatch.setattr(worker, "write_shm_frame", lambda camera_id, image, packet: observed.append(packet.observed_at))
    detector, session = Detector(), Session()
    processor = worker.ObservationProcessor("test", detector, session)
    processor.process(worker.SourceFrame(object(), datetime.now(timezone.utc).isoformat(), 1, True))
    assert len(observed) == 1
    assert isinstance(observed[0], datetime) and observed[0].tzinfo == timezone.utc
    assert session.sent == []
