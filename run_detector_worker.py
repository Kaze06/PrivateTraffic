#!/usr/bin/env python3
"""Source-time vehicle windows. OpenCV alone cannot certify live capture time.

For a permitted live feed, --timestamp-adapter module:function supplies an aware
source datetime for (stream_url, frame_index, media_position_ms), or None when
unavailable. The adapter must use authoritative per-frame source metadata, never
wall-clock receipt time. Replay uses recording_time + original media position.
"""
import sys
import os
import time
import argparse
import importlib
import threading
import queue
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "backend"))
from app.detector import VehicleDetector, InferenceUnavailable
from app.video_stream import get_shm_frame_path, clear_frame
from app.health_monitor import MAX_SOURCE_GAP_SECONDS

logger = logging.getLogger("RFDETR-Worker")
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
FRAME_INTERVAL = 1 / 30


def parse_source_time(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Source time must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass
class SourceFrame:
    image: object
    observed_at: object
    sequence: object
    is_replay: bool
    reason: str = None


class MonotonicPacedReader:
    def __init__(self, stream_url, is_local_file=False, *, is_replay=False,
                 recording_time=None, timestamp_adapter=None):
        self.stream_url = stream_url
        self.is_local_file = is_local_file
        self.is_replay = is_replay or is_local_file
        self.recording_time = parse_source_time(recording_time) if recording_time else None
        self.timestamp_adapter = timestamp_adapter
        self.running = True
        self.ended = False
        self.frame_queue = queue.Queue(maxsize=180 if self.is_replay else 2)
        self.thread = threading.Thread(target=self._capture_thread, daemon=True)
        self.thread.start()

    def _capture_thread(self):
        import cv2
        cap = cv2.VideoCapture(self.stream_url)
        sequence = 0
        pace_start = time.monotonic()
        previous_position = None
        try:
            while self.running:
                try:
                    ret, frame = cap.read()
                except Exception:
                    ret, frame = False, None
                if not ret or frame is None:
                    if self.is_replay:
                        self.ended = True
                        break
                    cap.release()
                    time.sleep(0.5)
                    cap = cv2.VideoCapture(self.stream_url)
                    continue
                sequence += 1
                position = cap.get(cv2.CAP_PROP_POS_MSEC)
                observed = None
                reason = None
                try:
                    if self.is_replay:
                        if self.recording_time is None:
                            reason = "replay_recording_time_unavailable"
                        elif position < 0 or (previous_position is not None and position <= previous_position):
                            reason = "replay_media_timestamp_not_increasing"
                        else:
                            observed = self.recording_time + timedelta(milliseconds=position)
                        previous_position = position
                        delay = pace_start + position / 1000 - time.monotonic()
                        if 0 < delay < 5:
                            time.sleep(delay)
                    elif self.timestamp_adapter:
                        observed = parse_source_time(self.timestamp_adapter(self.stream_url, sequence, position))
                    else:
                        reason = "live_capture_timestamp_unavailable"
                except Exception:
                    reason = "source_timestamp_unavailable"
                packet = SourceFrame(frame, observed, sequence, self.is_replay, reason)
                while self.running:
                    try:
                        self.frame_queue.put(packet, timeout=0.1)
                        break
                    except queue.Full:
                        if not self.is_replay:
                            try:
                                self.frame_queue.get_nowait()
                            except queue.Empty:
                                pass
        finally:
            cap.release()

    def get_paced_frame(self):
        try:
            return self.frame_queue.get(timeout=FRAME_INTERVAL)
        except queue.Empty:
            return None

    def stop(self):
        self.running = False
        self.thread.join(timeout=2)


class HLSSegmentReader:
    """Decode an anchored media segment directly; never use OpenCV's playlist cursor."""
    def __init__(self, stream_url, *, trust_pdt=False):
        from app.hls_timestamp import SegmentClock
        self.clock = SegmentClock(stream_url, trust_pdt=trust_pdt)
        self.is_replay = self.ended = False
        self.running = True
        self.frame_queue = queue.Queue(maxsize=180)
        self.thread = threading.Thread(target=self._capture_thread, daemon=True)
        self.thread.start()

    def _capture_thread(self):
        sequence = 0
        while self.running:
            for kind, value in self.clock.poll():
                sequence += 1
                if kind == "frame":
                    image, observed, identity = value
                    packet = SourceFrame(image, observed, identity, False)
                else:
                    packet = SourceFrame(None, None, ("invalid", sequence), False, value)
                while self.running:
                    try:
                        self.frame_queue.put(packet, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if not self.running:
                    break
            time.sleep(0.5)

    def get_paced_frame(self):
        try:
            return self.frame_queue.get(timeout=FRAME_INTERVAL)
        except queue.Empty:
            return None

    def stop(self):
        self.running = False
        self.thread.join(timeout=2)


def create_source_reader(source, local, replay, recording, adapter, hls_pdt=False):
    if hls_pdt and not replay:
        return HLSSegmentReader(source, trust_pdt=True)
    return MonotonicPacedReader(source, local, is_replay=replay,
                                recording_time=recording, timestamp_adapter=adapter)


class SourceWindow:
    """Count distinct tracks observed in the preceding 60 source seconds."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.start = None
        self.last_time = None
        self.ids = {}

    def add(self, observed_at, tracker_ids):
        if self.last_time is not None:
            gap = (observed_at - self.last_time).total_seconds()
            if gap <= 0:
                self.reset()
                raise ValueError("source_timestamp_not_increasing")
            if gap > MAX_SOURCE_GAP_SECONDS:
                self.reset()
                raise ValueError("source_window_interrupted")
        result = None
        if self.start is None:
            self.start = observed_at
        self.last_time = observed_at
        for tid in tracker_ids:
            if int(tid) > 0:
                self.ids[int(tid)] = observed_at
        if (observed_at - self.start).total_seconds() >= 60:
            cutoff = observed_at - timedelta(seconds=60)
            self.ids = {tid: seen for tid, seen in self.ids.items() if seen > cutoff}
            result = {"timestamp": observed_at.isoformat(), "vehicle_count": len(self.ids),
                      "flow_per_minute": float(len(self.ids)), "window_seconds": 60, "validity": "valid"}
            self.start = observed_at
            self.ids.clear()
        return result



def write_shm_frame(camera_id, image, packet):
    import cv2
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        return
    path = get_shm_frame_path(camera_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(encoded.tobytes())
    tmp.replace(path)
    meta = path.with_suffix(".json")
    tmp = meta.with_suffix(".tmp.json")
    tmp.write_text(json.dumps({"usable": True, "observed_at": packet.observed_at.isoformat(),
                              "is_replay": packet.is_replay, "written_at": time.time(),
                              "sequence": f"{packet.observed_at.isoformat()}:{packet.sequence}"}))
    tmp.replace(meta)


def get_camera_status(camera_id, session):
    import requests
    try:
        response = session.get(f"{API_BASE_URL}/api/cameras/{camera_id}/status", timeout=2)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException:
        return None


def get_active_focus_camera_id(session):
    import requests
    try:
        response = session.get(f"{API_BASE_URL}/api/cameras/active-focus", timeout=2)
        return response.json().get("active_camera_id") if response.status_code == 200 else None
    except requests.RequestException:
        return None


def get_default_camera(session):
    response = session.get(f"{API_BASE_URL}/api/cameras?pilot_only=true", timeout=3)
    response.raise_for_status()
    cameras = response.json()
    if not cameras:
        raise RuntimeError("No configured camera source")
    return next((c for c in cameras if c.get("is_default")), cameras[0])


def resolve_stream_url(stream_url):
    if not stream_url:
        raise ValueError("source_url_unavailable")
    if stream_url.startswith("/uploads/"):
        root = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads"))).resolve()
        path = (root / stream_url[len("/uploads/"):]).resolve()
        if path.parent != root:
            raise ValueError("invalid_replay_path")
        return (str(path), True) if path.is_file() else (f"{API_BASE_URL}{stream_url}", False)
    return stream_url, Path(stream_url).is_file() if "://" not in stream_url else False


def configure_cli_replay(source, recording_time, camera, session):
    """Register replay mode and original time before submitting any observations."""
    recording = parse_source_time(recording_time)
    if not camera.get("camera_code"):
        raise ValueError("The selected camera needs a camera_code for replay setup")
    resolved, local = resolve_stream_url(source)
    data = {"camera_code": camera["camera_code"], "recording_time": recording.isoformat()}
    endpoint = f"{API_BASE_URL}/api/cameras/upload-replay"
    if local:
        with open(resolved, "rb") as video:
            response = session.post(endpoint, data=data, files={"video_file": (Path(resolved).name, video, "video/mp4")}, timeout=60)
    else:
        data["stream_url"] = resolved
        response = session.post(endpoint, data=data, timeout=10)
    response.raise_for_status()
    return response.json()["stream_url"]


class ObservationProcessor:
    def __init__(self, camera_id, detector, session):
        self.camera_id, self.detector, self.session = camera_id, detector, session
        self.window = SourceWindow()
        self.last_sequence = None
        self.last_invalid = None
        self.last_invalid_at = 0

    def submit(self, payload, replay):
        payload.update(camera_id=self.camera_id, is_replay=replay,
                       source_status="replay_not_live" if replay else "live")
        response = self.session.post(f"{API_BASE_URL}/api/measurements", json=payload, timeout=5)
        response.raise_for_status()

    def invalid(self, reason, replay, observed_at=None):
        self.window.reset()
        self.detector.reset()
        clear_frame(self.camera_id)
        now = time.monotonic()
        if reason == self.last_invalid and now - self.last_invalid_at < 10:
            return
        self.submit({"timestamp": observed_at.isoformat() if observed_at else None,
                     "vehicle_count": 0, "flow_per_minute": 0, "window_seconds": 60,
                     "validity": "invalid", "no_measurement_reason": reason}, replay)
        self.last_invalid, self.last_invalid_at = reason, now

    def process(self, packet):
        if packet.sequence == self.last_sequence:
            self.invalid("source_frame_reused", packet.is_replay, packet.observed_at)
            return
        self.last_sequence = packet.sequence
        if packet.observed_at is None:
            self.invalid(packet.reason or "source_timestamp_unavailable", packet.is_replay)
            return
        try:
            observed = parse_source_time(packet.observed_at)
        except ValueError:
            self.invalid("source_timestamp_unavailable", packet.is_replay)
            return
        packet.observed_at = observed
        if not packet.is_replay and not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 60:
            self.invalid("source_observation_not_fresh", False)
            return
        try:
            annotated, _, detections = self.detector.process_frame(packet.image)
            ids = detections.tracker_id if detections.tracker_id is not None else []
            result = self.window.add(observed, ids)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, (InferenceUnavailable, ValueError)) else "vehicle_inference_failed"
            self.invalid(reason, packet.is_replay, observed)
            return
        write_shm_frame(self.camera_id, annotated, packet)
        self.last_invalid = None
        if result:
            self.submit(result, packet.is_replay)


def main():
    import requests
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Fixed source; local files are always replay")
    parser.add_argument("--replay", action="store_true", help="Explicitly mark a remote source as replay")
    parser.add_argument("--recording-time", help="Original timezone-aware recording start, required for replay")
    parser.add_argument("--timestamp-adapter", help="Trusted source metadata adapter module:function")
    parser.add_argument("--hls-pdt", action="store_true", help="Decode video-only MPEG-TS media segments with explicit per-segment PDT")
    parser.add_argument("--trust-hls-source-clock", action="store_true", help="Attest PDT denotes the first displayed video sample's UTC observation time")
    args = parser.parse_args()
    if args.hls_pdt and (not args.trust_hls_source_clock or args.timestamp_adapter or args.replay):
        parser.error("--hls-pdt requires --trust-hls-source-clock and cannot be combined with replay or a custom adapter")
    logging.basicConfig(level=logging.INFO)
    adapter = None
    if args.timestamp_adapter:
        module, function = args.timestamp_adapter.split(":", 1)
        adapter = getattr(importlib.import_module(module), function)
    session = requests.Session()
    camera = get_default_camera(session)
    camera_id = camera["id"]
    registered_replay = None
    if args.source:
        _, local = resolve_stream_url(args.source)
        if local or args.replay or args.source.startswith("/uploads/"):
            if not args.recording_time:
                parser.error("Replay --source requires --recording-time with its original timezone-aware recording start")
            args.source = configure_cli_replay(args.source, args.recording_time, camera, session)
            registered_replay = (args.source, parse_source_time(args.recording_time))
    detector = VehicleDetector()
    reader = None
    signature = None
    processor = None
    next_focus_check = 0
    last_packet_at = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if now >= next_focus_check:
                next_focus_check = now + 2
                active_id = (get_active_focus_camera_id(session) or camera_id) if not args.source else camera_id
                info = get_camera_status(active_id, session)
                if info:
                    if registered_replay and (
                        not info.get("is_replay")
                        or info.get("stream_url") != registered_replay[0]
                        or not info.get("recording_time")
                        or parse_source_time(info["recording_time"]) != registered_replay[1]
                    ):
                        logger.info("Registered replay was retired or replaced; stopping fixed replay worker")
                        break
                    source, local = resolve_stream_url(args.source or info.get("stream_url"))
                    replay = bool(args.replay or local or info.get("is_replay") or (args.source or info.get("stream_url", "")).startswith("/uploads/"))
                    recording = args.recording_time or info.get("recording_time")
                    new_signature = (active_id, source, replay, recording)
                    if new_signature != signature:
                        if reader:
                            reader.stop()
                            clear_frame(camera_id)
                        camera_id = active_id
                        reader = create_source_reader(source, local, replay, recording, adapter, args.hls_pdt)
                        detector.reset()
                        processor = ObservationProcessor(camera_id, detector, session)
                        signature = new_signature
                        last_packet_at = now
            if reader is None:
                time.sleep(0.1)
                continue
            packet = reader.get_paced_frame()
            try:
                if packet is not None:
                    last_packet_at = time.monotonic()
                    processor.process(packet)
                elif reader.ended or time.monotonic() - last_packet_at > (35 if isinstance(reader, HLSSegmentReader) else 2):
                    processor.invalid("replay_ended" if reader.ended else "source_frames_unavailable", reader.is_replay)
            except requests.RequestException:
                logger.exception("Measurement submission failed")
                processor.window.reset()
    except KeyboardInterrupt:
        pass
    finally:
        if reader:
            reader.stop()
        clear_frame(camera_id)
        session.close()


if __name__ == "__main__":
    main()
