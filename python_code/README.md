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

## Submit a Y4M replay to the existing backend

Start the PrivateTraffic API server and stop any other detector worker processing the same camera. Run the bridge from this folder (or use these parameters in a PyCharm run configuration):

```powershell
python measurement_bridge.py D:\path\to\approved_road_clip.y4m --camera-code C631 --recording-time 2026-11-01T12:00:00+08:00 --line-y 240
```

The bridge validates the Y4M source, generates a temporary H.264 MP4 preview for the browser, computes all measurement reports, then calls `GET /api/cameras`, `POST /api/cameras/upload-replay`, and `POST /api/measurements` in that order. It verifies the selected camera and replay mode. Source times and counts always come from the original Y4M file; the MP4 is only for display. An incomplete final window is saved as invalid. Replay measurements cannot trigger live alerts.

The API keeps the uploaded replay active after the command ends so its status and video remain inspectable. The existing application's replay removal control can restore the source; that control removes all configured backup replays, so check the other cameras first. This bridge is a replay integration tool, not a replacement for the repository's RF-DETR live worker. Its basic motion detector still needs real-road accuracy validation.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The two real-backend integration tests run automatically when this folder is inside the repository. When opening `D:\python_code` separately in PyCharm, set `PRIVATE_TRAFFIC_REPO` to a local PrivateTraffic checkout before running tests; otherwise those two tests are skipped. They use an in-memory database and temporary upload directory.

The detector uses background subtraction and centroid matching as an initial moving-target baseline. It has not been validated against real road footage; compare its output with manual counts before reporting detection accuracy. The line position, minimum target area, and camera viewpoint affect counts. Only downward crossings are counted.

## P0-2: prepare real-sample counting evidence

`count_evidence.py` prepares the sample index and aligns manual counting rows to valid 60-second automatic reports. It does **not** create human ground truth, grant video permission, or certify that a clip is real. Source-owner permission, retention terms, normal and peak recordings, and original recording times are still required. Keep raw clips, permission records, and completed results outside GitHub unless their terms explicitly allow sharing.

Create a JSON manifest after obtaining the recordings. Each `reports_path` is a JSONL file with one Measurement JSON object per line, exported from the RF-DETR replay worker or produced by `measurement.py`. Use the exact model version and Git commit for the run. Replace every example value below with verified sample information:

```json
{
  "samples": [
    {
      "sample_id": "normal_01",
      "scenario": "normal",
      "video_path": "D:/permitted_samples/normal_01.y4m",
      "reports_path": "D:/permitted_samples/normal_01_reports.jsonl",
      "camera_id": "camera/507320",
      "camera_code": "C631",
      "recording_start": "2026-11-01T12:00:00+08:00",
      "count_rule": "downward_line_crossing",
      "count_line_y": 240,
      "model_version": "model-name@exact-commit",
      "permission_reference": "source-owner authorization reference",
      "retention_terms": "allowed storage location and deletion date"
    }
  ]
}
```

The standalone Y4M module uses `downward_line_crossing` and requires `count_line_y`. The repository RF-DETR worker counts `unique_tracks_present` within each 60-second window; for that rule set `count_line_y` to `null`. A human annotator must follow the selected rule and the same visible road area. Do not compare the two rules as if they meant the same count.

Run `prepare` to hash the clip and report file and produce `sample_index.csv` plus `manual_counts_template.csv`. The template gives each video's exact start/end offsets and UTC times for the corresponding 60-second report. A human annotator watches those windows and fills `manual_count` and `annotator` in a copy of the template, saved as `manual_counts.csv`.

```powershell
python count_evidence.py prepare --manifest D:\permitted_samples\samples.json --output-dir D:\python_code\evidence\run_01\prepare
python count_evidence.py evaluate --manifest D:\permitted_samples\samples.json --prepared-index D:\python_code\evidence\run_01\prepare\sample_index.csv --manual-counts D:\permitted_samples\manual_counts.csv --output-dir D:\python_code\evidence\run_01\evaluate
```

`evaluate` first checks the prepared index to ensure the clip, automatic reports, and metadata have not changed since the manual sheet was created. It then writes `sample_index.csv`, `count_comparison.csv`, and `summary.json` to a new output directory. For a nonzero manual count it uses `abs(automatic_count - manual_count) / manual_count` and marks a window against the normal 10% or peak 15% target. For a zero manual count it leaves percentage error blank and records the number of false positive counts. Invalid automatic reports such as an incomplete tail are excluded; overlapping valid windows, missing permissions, mismatched cameras, missing or extra manual rows, and duplicate windows are rejected. The script does not overwrite existing evidence files. It hashes files and checks metadata/report alignment; a person must still verify that the recording actually covers the indexed time window and that the permission reference is genuine.

The tests use synthetic fixtures only; they are not real-road accuracy evidence. A local video file by itself does not establish permission, the authoritative recording start, normal/peak classification, or manual ground truth. Keep those fields pending until the source owner and annotator confirm them.
