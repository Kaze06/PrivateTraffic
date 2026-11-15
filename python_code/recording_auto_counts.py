"""Run the repository RF-DETR worker's count semantics on local MP4 windows.

Frames are decoded and processed in memory; the output is aggregate JSONL only.
The result is automatic measurement, not independent manual ground truth.
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


class RecordingCountError(ValueError):
    """A recording could not yield a valid, source-timed count window."""


def _recording_start(value):
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RecordingCountError("recording_time must include a timezone") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise RecordingCountError("recording_time must include a timezone")
    return observed.astimezone(timezone.utc)


def count_recording(video_path, camera_id, recording_time, repo_path, reports_path,
                    *, detector=None, start_seconds=0.0, max_windows=1):
    """Write source-time replay measurements using the product SourceWindow."""
    started_at = _recording_start(recording_time)
    if not isinstance(camera_id, str) or not camera_id.strip():
        raise RecordingCountError("camera_id is required")
    if not isinstance(start_seconds, (int, float)) or not math.isfinite(start_seconds) or start_seconds < 0:
        raise RecordingCountError("start_seconds must be nonnegative and finite")
    if isinstance(max_windows, bool) or not isinstance(max_windows, int) or max_windows < 1:
        raise RecordingCountError("max_windows must be a positive integer")
    video_path = Path(video_path)
    if not video_path.is_file():
        raise RecordingCountError(f"video file not found: {video_path}")
    repo_path = Path(repo_path).resolve()
    if not (repo_path / "run_detector_worker.py").is_file():
        raise RecordingCountError(f"PrivateTraffic checkout not found: {repo_path}")
    reports_path = Path(reports_path)
    if reports_path.exists():
        raise RecordingCountError(f"refusing to overwrite reports: {reports_path}")

    # The worker inserts its backend path and imports the same SourceWindow and
    # VehicleDetector used by the existing product pipeline.
    sys.path.insert(0, str(repo_path))
    from run_detector_worker import SourceWindow
    from app.detector import InferenceUnavailable, VehicleDetector
    import av

    detector = detector if detector is not None else VehicleDetector()
    if detector is None or (isinstance(detector, VehicleDetector) and detector.model is None):
        raise RecordingCountError("RF-DETR model unavailable")
    window = SourceWindow()
    reports = []
    previous_seconds = None
    processed_frames = 0
    with av.open(str(video_path)) as container:
        for frame in container.decode(video=0):
            seconds = frame.time
            if seconds is None or not math.isfinite(seconds) or seconds < 0:
                raise RecordingCountError("media presentation timestamp unavailable")
            if previous_seconds is not None and seconds <= previous_seconds:
                raise RecordingCountError("media presentation time did not increase")
            previous_seconds = seconds
            if seconds < start_seconds:
                continue
            processed_frames += 1
            if processed_frames % 300 == 0:
                print(f"Processed {processed_frames} frames through media {seconds:.1f}s",
                      file=sys.stderr, flush=True)
            observed_at = started_at + timedelta(seconds=seconds)
            try:
                _, _, tracked = detector.process_frame(frame.to_ndarray(format="bgr24"))
                ids = tracked.tracker_id if tracked.tracker_id is not None else []
                result = window.add(observed_at, ids)
            except (InferenceUnavailable, ValueError) as exc:
                raise RecordingCountError(f"no valid count after source/inference failure: {exc}") from exc
            if result is not None:
                reports.append({
                    "camera_id": camera_id,
                    "timestamp": result["timestamp"],
                    "vehicle_count": result["vehicle_count"],
                    "flow_per_minute": result["flow_per_minute"],
                    "window_seconds": 60,
                    "validity": "valid",
                    "no_measurement_reason": None,
                    "source_status": "replay_not_live",
                    "is_replay": True,
                })
                if len(reports) >= max_windows:
                    break
    if not reports:
        raise RecordingCountError("no complete 60-second window in selected recording interval")
    reports_path.parent.mkdir(parents=True, exist_ok=True)
    with reports_path.open("x", encoding="utf-8", newline="\n") as stream:
        for report in reports:
            stream.write(json.dumps(report, ensure_ascii=False) + "\n")
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--recording-time", required=True, help="Original start with timezone")
    parser.add_argument("--repo", required=True, type=Path, help="Local PrivateTraffic checkout")
    parser.add_argument("--reports-out", required=True, type=Path)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--max-windows", type=int, default=1)
    args = parser.parse_args()
    try:
        reports = count_recording(args.video, args.camera_id, args.recording_time,
                                  args.repo, args.reports_out,
                                  start_seconds=args.start_seconds, max_windows=args.max_windows)
    except (RecordingCountError, OSError) as exc:
        parser.exit(1, f"Recording count error: {exc}\n")
    print(json.dumps({"reports": len(reports), "output": str(args.reports_out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
