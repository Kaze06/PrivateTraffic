"""Send a Y4M replay through PrivateTraffic's existing replay and measurement APIs.

The Y4M file remains the measurement source. A temporary MP4 is uploaded only
for browser preview. Stop any other detector worker for this camera first.
"""

import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path

from measurement import MeasurementError, MeasurementWindow, Y4MReader, measure


def transcode_y4m_to_mp4(source, destination):
    """Create a browser-playable preview while rejecting unusable source timing."""
    import imageio_ffmpeg

    count = 0
    with open(source, "rb") as stream:
        reader = Y4MReader(stream)
        fps = 1.0 / reader.frame_period
        if not math.isfinite(fps) or not 0 < fps <= 120:
            raise MeasurementError("unsupported Y4M frame rate")
        previous = None
        for frame in reader:
            if previous is not None and frame.seconds - previous > 5:
                raise MeasurementError("source timestamp interrupted")
            # Constant-rate MP4 cannot represent materially variable Y4M frame times.
            expected = count * reader.frame_period
            if abs(frame.seconds - expected) > reader.frame_period / 2:
                raise MeasurementError("Y4M timestamps cannot align with MP4 preview")
            count += 1
            previous = frame.seconds
    if count == 0:
        raise MeasurementError("empty Y4M replay")
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
               "-y", "-f", "yuv4mpegpipe", "-i", str(source), "-an", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-tag:v", "avc1", str(destination)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise MeasurementError(f"H.264 preview encoding failed: {result.stderr.strip()[-500:]}")
    if not Path(destination).is_file() or Path(destination).stat().st_size == 0:
        raise MeasurementError("MP4 preview was not created")


def run_replay(y4m_path, camera_code, recording_time, api_base_url="http://127.0.0.1:8000",
               *, session=None, detector=None, converter=None, line_y=None, min_area=400):
    """Validate, register, then submit the replay; return submitted reports."""
    import requests

    y4m_path = Path(y4m_path)
    if not y4m_path.is_file():
        raise MeasurementError(f"Y4M file not found: {y4m_path}")
    if not camera_code or not camera_code.strip():
        raise MeasurementError("camera code is required")
    # Fail before any API mutation when the original recording time is invalid.
    try:
        MeasurementWindow("preflight", recording_time)
    except ValueError as exc:
        raise MeasurementError("recording time must be ISO 8601 with a timezone") from exc

    owned_session = session is None
    session = session or requests.Session()
    base = api_base_url.rstrip("/")
    try:
        with tempfile.TemporaryDirectory(prefix="privatetraffic_y4m_") as directory:
            preview = Path(directory) / "preview.mp4"
            (converter or transcode_y4m_to_mp4)(y4m_path, preview)
            if not preview.is_file() or preview.stat().st_size == 0:
                raise MeasurementError("MP4 preview was not created")

            response = session.get(f"{base}/api/cameras", timeout=10)
            response.raise_for_status()
            matches = [camera for camera in response.json()
                       if camera.get("camera_code") == camera_code]
            if len(matches) != 1:
                raise MeasurementError("camera code must select exactly one camera")
            camera_id = matches[0]["id"]

            # Finish analysis before registering replay, so a bad Y4M source
            # cannot leave the camera in replay mode without measurement data.
            reports = list(measure(y4m_path, camera_id, recording_time,
                                   line_y=line_y, min_area=min_area, detector=detector))
            if not reports:
                raise MeasurementError("no measurement report was produced")

            with preview.open("rb") as video:
                response = session.post(
                    f"{base}/api/cameras/upload-replay",
                    data={"camera_code": camera_code, "recording_time": recording_time},
                    files={"video_file": ("preview.mp4", video, "video/mp4")}, timeout=60)
            response.raise_for_status()
            registration = response.json()
            if registration.get("camera_id") != camera_id or registration.get("mode") != "replay_not_live":
                raise MeasurementError("replay registration returned a different camera or mode")

            for report in reports:
                response = session.post(f"{base}/api/measurements", json=report, timeout=10)
                response.raise_for_status()
                if response.json().get("alert_triggered"):
                    raise MeasurementError("replay unexpectedly triggered a live alert")
            return reports
    finally:
        if owned_session:
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Original YUV420 Y4M file")
    parser.add_argument("--camera-code", required=True, help="Existing backend camera code")
    parser.add_argument("--recording-time", required=True, help="Original ISO 8601 start with timezone")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--line-y", type=int)
    parser.add_argument("--min-area", type=int, default=400)
    args = parser.parse_args()
    try:
        reports = run_replay(args.video, args.camera_code, args.recording_time,
                             args.api_base_url, line_y=args.line_y, min_area=args.min_area)
    except (MeasurementError, OSError) as exc:
        parser.exit(1, f"Measurement integration error: {exc}\n")
    for report in reports:
        print(json.dumps(report, ensure_ascii=False))
    print(f"Submitted {len(reports)} replay report(s); browser mode is replay_not_live.")


if __name__ == "__main__":
    main()
