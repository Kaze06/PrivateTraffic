# PrivateTraffic Measurement Python

This PyCharm-ready project implements the weekly report's Measurement module for Y4M replay. It reads progressive YUV420 frames, derives source time from the Y4M frame rate (or `XPTS=<seconds>` frame tags), tracks moving regions, counts a downward crossing once per track, and emits one JSON record per complete 60-second window. A short final window is marked invalid. Replay is always labelled `replay_not_live`.

## Open in PyCharm

1. Open this `python_code` folder as a project.
2. Configure a Python 3.10+ interpreter and install `requirements.txt`.
3. Create a Python run configuration: script `measurement.py`; parameters as below; working directory this folder.

```powershell
python -m pip install -r requirements.txt
python measurement.py D:\path\to\approved_road_clip.y4m --camera-id camera/507320 --recording-time 2026-11-01T12:00:00+08:00 --line-y 240
```

If the source is MP4, convert it first with FFmpeg: `ffmpeg -i input.mp4 -pix_fmt yuv420p output.y4m`. Use the original recording start as `--recording-time`; do not use the conversion time. Standard Y4M files have a frame rate but no absolute recording time, so this argument is required.

Each output line is a JSON object using the existing backend field names: `camera_id`, `timestamp`, `vehicle_count`, `flow_per_minute`, `window_seconds`, `validity`, `no_measurement_reason`, `source_status`, `is_replay`. These records are ready for review or later submission to `POST /api/measurements`. This project does not change the backend's MP4 upload and replay registration flow.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The detector uses background subtraction and centroid matching as an initial moving-target baseline. It has not been validated against real road footage; compare its output with manual counts before reporting detection accuracy. The line position, minimum target area, and camera viewpoint affect counts. Only downward crossings are counted.
