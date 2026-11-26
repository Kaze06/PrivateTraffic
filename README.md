# PrivateTraffic

PrivateTraffic is Group 13's Software Engineering course project. The first release follows Task2/Task3: one permitted camera, rolling counts over up to 60 seconds, a labelled time basis, explicit source state, calibrated road levels, and threshold alerts while the page is open.

Current milestone: integrated development baseline, including Y4M replay and count-evaluation tools. See [the acceptance record](docs/ACCEPTANCE.md) for prior replay evidence and remaining real-source checks.

## Run the dashboard

Use Python 3.10 or newer. From the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The API serves the dashboard; the detector is a separate process. The database is updated additively on startup. Existing records are not deleted.

The backend can be tested without downloading model weights. RF-DETR is pinned to 1.12.1; inference additionally needs the installed Torch/OpenCV dependencies and model weights. Source access, actual model inference and hardware throughput must be checked on the selected pilot.

## Run the detector

Start the API first. For a permitted recording with a known original start time:

```powershell
python run_detector_worker.py --source clip.mp4 --recording-time "2020-01-01T00:00:00+08:00"
```

The worker registers replay metadata with the selected pilot camera before processing, and uploads a copy of a local clip. For a permitted remote HTTP(S) recording, also pass `--replay`. Purging replay files restores the original source URL. Set `API_BASE_URL` if the API is not at http://127.0.0.1:8000.

In source-observation mode, use `--timestamp-adapter module:function`. The callable receives `(stream_url, frame_index, media_position_ms)` and returns the timezone-aware source observation time, or `None` when unavailable. The four configured MnDOT playlists were checked on 2026-11-15 and contain no absolute PDT anchor. The receipt-time mode below supports these feeds with a separately labelled local frame-receipt time. RF-DETR inference and real source throughput remain to be measured on the selected pilot.

For a feed without frame capture timestamps, start a separate worker in the system-received-time mode:

```powershell
$env:API_BASE_URL = "http://127.0.0.1:8000"
python run_detector_worker.py --receipt-time
```

The worker uses the selected frame's local receipt time, publishes a rolling 60-second distinct-track count every 5 seconds, and enables the existing page-session alert rules. During the first minute it normalizes the available partial window to vehicles per minute. The dashboard labels these measurements with their time basis. This mode is for live operational use when source capture time is unavailable; its video delivery delay is unknown. Run one worker process, keep the dashboard open for the selected camera, and ensure RF-DETR weights and runtime dependencies are installed.

For an allowed **media playlist** whose provider confirms that each segment's PROGRAM-DATE-TIME is the first displayed video's observation time, use:

```powershell
python run_detector_worker.py --source "https://provider.example/media.m3u8" --hls-pdt --trust-hls-source-clock
```

This opt-in path decodes each exact segment with PyAV and derives frame UTC from its presentation timestamps. It supports unencrypted, independent, video-only MPEG-TS segments, each with an explicit timezone-aware PDT. Master playlists, audio tracks, encryption, byte ranges and initialization segments are unsupported and yield unavailable measurements. The trust flag records the operator's assertion; obtain the source timing evidence before enabling it. See [source timing and probe evidence](docs/SOURCE_TIMING.md).

## Inspect software behavior without a live feed

```powershell
.venv/Scripts/python tools/demo_server.py
```

Open http://127.0.0.1:8765/demo for controls and http://127.0.0.1:8765 for the dashboard. This separate local entry point uses `demo-session.db` and a camera named **SYNTHETIC TEST - not live traffic**. Enable a threshold of 25 on the dashboard, send low (20), then high (30). Repeating high should not produce another event; low re-arms it. The controls also inject stale/offline and replay states while preserving stored observation timestamps. Automatic aging is checked separately by the API tests. This is software validation, not a source permission, model-accuracy or live-latency result.

Stop a foreground demo with Ctrl+C. For the background demonstration on Windows, run `powershell -File tools/stop-demo.ps1`; it checks the process on port 8765 before stopping it.

The older `python seed_mock_data.py` command now creates only labelled non-live history in `demo-history.db` by default. It does not relabel real cameras as live or enable rules.

## Data and acceptance rules

- The RF-DETR worker counts distinct vehicle tracks seen within each 60-second source-time window, not line crossings. The separate [Y4M bridge](python_code/README.md) can generate line-crossing measurements for non-live replay; do not compare the two as though they used the same counting rule.
- The dashboard retains the measurement branch's default tracker. To compare the candidate adaptive tracker on the same permitted recording and window, run `python python_code/recording_auto_counts.py` with the same inputs twice, adding `--adaptive-tracking` on the second run and using a separate `--reports-out` path. This flag does not change live or dashboard behavior.
- Source-observation mode uses source observation time. `--receipt-time` is the explicit alternative for sources without capture timestamps and stores worker frame-receipt time as a separate basis.
- Failed inference is invalid data, not zero traffic. Replay has an explicit recording time and cannot generate live alerts.
- A road remains `unable_to_determine` until calibration evidence and T1/T2 have been recorded. User alert thresholds are separate from road classification.
- C8351 starts with provisional T1=33 and T2=142 in newly seeded databases. Existing values are unchanged on startup; back up the database before explicitly updating it with `python_code/apply_camera_calibration.py`.
- A first valid alert observation sets the baseline. Only a below-to-at/above crossing creates an event; sustained high is suppressed.
- Source permission and retention evidence must be supplied before a live pilot. Uploaded permitted replay files are retained separately and can be purged. GPS and identity analysis are excluded.
- Prior evaluation targets are <=10% normal / <=15% peak count error, live freshness <=60 seconds and visible eligible alert <=30 seconds. Keep prior recording results tied to the tracker and windows that produced them; assess live performance separately.

See [shared contract](docs/CONTRACT.md) and [task record](docs/TASKS.md). Historical charts, notifications after page close and simultaneous camera monitoring are deferred.

## Tests

```powershell
.venv/Scripts/python -m pytest -q
```

The default run covers both `tests` and `python_code/tests`, including MP4 transcoding for Y4M replay. Install the root `requirements.txt` first; tests do not require RF-DETR weights.

With Playwright and its Chromium browser installed, run `node tools/check-browser.cjs` while the demo is running. `PLAYWRIGHT_MODULE` can point to an existing Playwright installation.

Behavior checks use isolated SQLite databases. Browser screenshots and test evidence are stored in `artifacts/`; synthetic scenarios are labelled.

## Team

- Zian Song 1240008286 (leader): integration and acceptance coordination
- Jiajun Chen 1240000551: source and failure-state acceptance
- Ziyan Yao 1230020566: measurement and calibration acceptance
