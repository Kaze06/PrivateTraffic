"""Y4M replay measurements for the PrivateTraffic Python backend.

Run ``python measurement.py --help``. A replay is never labelled live.
"""

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MeasurementError(ValueError):
    """Invalid source media or observation sequence."""


@dataclass(frozen=True)
class Frame:
    seconds: float
    width: int
    height: int
    yuv420: bytes


class Y4MReader:
    """Read progressive, 8-bit, planar 4:2:0 Y4M without guessing frame times."""

    def __init__(self, stream):
        self.stream = stream
        header = stream.readline()
        if not header.startswith(b"YUV4MPEG2 "):
            raise MeasurementError("not a Y4M file")
        fields = header.rstrip(b"\n").split()[1:]
        values = {field[:1]: field[1:] for field in fields}
        try:
            self.width = int(values[b"W"])
            self.height = int(values[b"H"])
            num, den = map(int, values[b"F"].split(b":"))
            chroma = values.get(b"C", b"420jpeg")
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            raise MeasurementError("invalid Y4M header") from exc
        if (self.width <= 0 or self.height <= 0 or self.width % 2 or self.height % 2
                or num <= 0 or den <= 0 or not chroma.startswith(b"420")):
            raise MeasurementError("unsupported frame size, rate or chroma")
        if values.get(b"I", b"p") != b"p":
            raise MeasurementError("interlaced Y4M is unsupported")
        self.frame_size = self.width * self.height * 3 // 2
        self.frame_period = den / num

    def __iter__(self):
        index = 0
        previous = None
        while True:
            marker = self.stream.readline()
            if marker == b"":
                return
            if not marker.startswith(b"FRAME") or marker[5:6] not in (b"\n", b" "):
                raise MeasurementError("invalid Y4M frame marker")
            seconds = index * self.frame_period
            for tag in marker.rstrip(b"\n").split()[1:]:
                if tag.startswith(b"XPTS="):
                    try:
                        seconds = float(tag[5:])
                    except ValueError as exc:
                        raise MeasurementError("invalid frame timestamp") from exc
            if not math.isfinite(seconds) or seconds < 0 or (previous is not None and seconds <= previous):
                raise MeasurementError("frame timestamp must increase")
            payload = self.stream.read(self.frame_size)
            if len(payload) != self.frame_size:
                raise MeasurementError("incorrect frame size or truncated frame")
            yield Frame(seconds, self.width, self.height, payload)
            previous = seconds
            index += 1


class LineCounter:
    """Associate centroid tracks and count a downward line crossing once per ID."""

    def __init__(self, line_y, max_distance=45, max_missed=8):
        self.line_y = line_y
        self.max_distance = max_distance
        self.max_missed = max_missed
        self.tracks = {}
        self.next_id = 1
        self.total = 0

    def update(self, boxes):
        points = [((x1 + x2) / 2, (y1 + y2) / 2) for x1, y1, x2, y2 in boxes]
        available = set(self.tracks)
        for x, y in points:
            candidates = [(math.hypot(x - self.tracks[tid][0], y - self.tracks[tid][1]), tid)
                          for tid in available]
            distance, tid = min(candidates, default=(math.inf, None))
            if distance > self.max_distance:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = (x, y, 0, False)
            else:
                old_x, old_y, _, counted = self.tracks[tid]
                if old_y < self.line_y <= y and not counted:
                    self.total += 1
                    counted = True
                self.tracks[tid] = (x, y, 0, counted)
                available.remove(tid)
        for tid in available:
            x, y, missed, counted = self.tracks[tid]
            if missed + 1 > self.max_missed:
                del self.tracks[tid]
            else:
                self.tracks[tid] = (x, y, missed + 1, counted)
        return self.total


class MeasurementWindow:
    """Emit a report only after 60 contiguous seconds of source time."""

    def __init__(self, camera_id, recording_time):
        self.camera_id = camera_id
        self.recording_time = datetime.fromisoformat(recording_time.replace("Z", "+00:00"))
        if self.recording_time.tzinfo is None or self.recording_time.utcoffset() is None:
            raise MeasurementError("recording time requires a timezone")
        self.start = self.previous = None
        self.start_count = 0
        self.pending = False

    def _report(self, seconds, count, validity, reason=None):
        observed = self.recording_time + timedelta(seconds=seconds)
        return {"camera_id": self.camera_id, "timestamp": observed.astimezone(timezone.utc).isoformat(),
                "vehicle_count": count, "flow_per_minute": float(count), "window_seconds": 60,
                "validity": validity, "no_measurement_reason": reason,
                "source_status": "replay_not_live", "is_replay": True}

    def add(self, seconds, cumulative_count):
        if not math.isfinite(seconds) or seconds < 0 or (self.previous is not None and
                (seconds <= self.previous or seconds - self.previous > 5)):
            self.start = self.previous = None
            self.pending = False
            raise MeasurementError("source timestamp repeated, reversed or interrupted")
        if self.start is None:
            self.start = seconds
            self.start_count = cumulative_count
        self.previous = seconds
        self.pending = True
        if seconds - self.start < 60:
            return None
        count = cumulative_count - self.start_count
        if count < 0:
            raise MeasurementError("vehicle count decreased")
        result = self._report(seconds, count, "valid")
        self.start, self.start_count = seconds, cumulative_count
        self.pending = False
        return result

    def finish(self):
        if not self.pending:
            return None
        return self._report(self.previous, 0, "invalid", "insufficient_duration")


class MotionDetector:
    """Simple moving-object baseline; real-road accuracy needs manual validation."""

    def __init__(self, min_area=400):
        import cv2
        self.cv2 = cv2
        self.model = cv2.createBackgroundSubtractorMOG2(history=120, varThreshold=25, detectShadows=False)
        self.min_area = min_area

    def boxes(self, frame):
        import numpy as np
        pixels = np.frombuffer(frame.yuv420, dtype=np.uint8).reshape((frame.height * 3 // 2, frame.width))
        bgr = self.cv2.cvtColor(pixels, self.cv2.COLOR_YUV2BGR_I420)
        mask = self.model.apply(bgr)
        mask = self.cv2.morphologyEx(mask, self.cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = self.cv2.findContours(mask, self.cv2.RETR_EXTERNAL, self.cv2.CHAIN_APPROX_SIMPLE)
        result = []
        for contour in contours:
            if self.cv2.contourArea(contour) >= self.min_area:
                x, y, w, h = self.cv2.boundingRect(contour)
                result.append((x, y, x + w, y + h))
        return result


def measure(path, camera_id, recording_time, line_y=None, min_area=400, detector=None):
    with open(path, "rb") as stream:
        reader = Y4MReader(stream)
        counter = LineCounter(reader.height / 2 if line_y is None else line_y)
        window = MeasurementWindow(camera_id, recording_time)
        detector = detector or MotionDetector(min_area)
        for frame in reader:
            count = counter.update(detector.boxes(frame))
            result = window.add(frame.seconds, count)
            if result:
                yield result
        tail = window.finish()
        if tail:
            yield tail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="YUV4MPEG2 .y4m replay file")
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--recording-time", required=True, help="Original ISO 8601 time with timezone")
    parser.add_argument("--line-y", type=int, help="Counting line Y pixel; default: image centre")
    parser.add_argument("--min-area", type=int, default=400)
    args = parser.parse_args()
    try:
        for report in measure(args.video, args.camera_id, args.recording_time, args.line_y, args.min_area):
            print(json.dumps(report, ensure_ascii=False))
    except (OSError, MeasurementError) as exc:
        parser.exit(1, f"Measurement error: {exc}\n")


if __name__ == "__main__":
    main()
