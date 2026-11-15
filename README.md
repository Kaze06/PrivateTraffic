# PrivateTraffic

PrivateTraffic is Group 13's Software Engineering course project. The first release follows Task2/Task3: one permitted camera, 60-second vehicle counts and flow, observation time, explicit source state, calibrated road levels, and threshold alerts while the page is open.

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

For live input, use `--timestamp-adapter module:function`. The callable receives `(stream_url, frame_index, media_position_ms)` and returns the authoritative timezone-aware source observation time, or `None` when unavailable. An adapter for the selected live feed is still required. OpenCV receipt time alone does not establish capture time; without that evidence the worker reports unavailable measurements. RF-DETR inference and real source throughput remain unverified in the current local acceptance environment.

## Inspect software behavior without a live feed

```powershell
.venv/Scripts/python tools/demo_server.py
```

Open http://127.0.0.1:8765/demo for controls and http://127.0.0.1:8765 for the dashboard. This separate local entry point uses `demo-session.db` and a camera named **SYNTHETIC TEST - not live traffic**. Enable a threshold of 25 on the dashboard, send low (20), then high (30). Repeating high should not produce another event; low re-arms it. The controls also inject stale/offline and replay states while preserving stored observation timestamps. Automatic aging is checked separately by the API tests. This is software validation, not a source permission, model-accuracy or live-latency result.

Stop a foreground demo with Ctrl+C. For the background demonstration on Windows, run `powershell -File tools/stop-demo.ps1`; it checks the process on port 8765 before stopping it.

The older `python seed_mock_data.py` command now creates only labelled non-live history in `demo-history.db` by default. It does not relabel real cameras as live or enable rules.

## Data and acceptance rules

- Use source observation time, not upload or processing time. Missing/old/non-increasing live timestamps cannot become current measurements.
- Failed inference is invalid data, not zero traffic. Replay has an explicit recording time and cannot generate live alerts.
- A road remains `unable_to_determine` until calibration evidence and T1/T2 have been recorded. User alert thresholds are separate from road classification.
- A first valid alert observation sets the baseline. Only a below-to-at/above crossing creates an event; sustained high is suppressed.
- Source permission and retention evidence must be supplied before a live pilot. Uploaded permitted replay files are retained separately and can be purged. GPS and identity analysis are excluded.
- Counting targets are <=10% normal / <=15% peak error, live freshness <=60 seconds, visible eligible alert <=30 seconds. Real samples and timing records are still required.

See [shared contract](docs/CONTRACT.md) and [task record](docs/TASKS.md). Historical charts, notifications after page close and simultaneous camera monitoring are deferred.

## Tests

```powershell
.venv/Scripts/python -m pytest -q
```

With Playwright and its Chromium browser installed, run `node tools/check-browser.cjs` while the demo is running. `PLAYWRIGHT_MODULE` can point to an existing Playwright installation.

Behavior checks use isolated SQLite databases. Browser screenshots and test evidence are stored in `artifacts/`; synthetic scenarios are labelled.

## Team

- Zian Song 1240008286 (leader): integration and acceptance coordination
- Jiajun Chen 1240000551: source and failure-state acceptance
- Ziyan Yao 1230020566: measurement and calibration acceptance
