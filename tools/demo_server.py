"""Local synthetic scenario server. Never use this entry point for live service."""
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
os.environ["DATABASE_PATH"] = str(ROOT / "demo-session.db")
os.environ["UPLOAD_DIR"] = str(ROOT / "demo-uploads")
sys.path.insert(0, str(ROOT / "backend"))
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from app.main import app
from app.database import initialize_schema, SessionLocal
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.schemas import MeasurementCreate
from app.routers.measurements import submit_measurement

CAMERA = "demo/software-test"
initialize_schema()
with SessionLocal() as db:
    if not db.get(CameraSourceModel, CAMERA):
        db.add(CameraSourceModel(id=CAMERA, camera_code="DEMO", title="SYNTHETIC TEST - not live traffic",
            road_name="Software scenario", latitude=44.97, longitude=-93.26,
            is_pilot=True, current_status="initializing", stream_url=None))
        db.commit()

# Put demo endpoints before the dashboard's catch-all static mount.
static_route = app.router.routes.pop()

@app.get("/demo", response_class=HTMLResponse)
def demo_controls():
    return '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PrivateTraffic scenario controls</title><style>body{font:18px system-ui;max-width:800px;margin:40px auto;padding:20px;line-height:1.6}button,a{font:inherit;margin:6px;padding:10px}pre{white-space:pre-wrap;background:#eee;padding:16px}</style>
    <h1>Software scenario controls</h1><p><strong>Synthetic inputs only - not live traffic.</strong> These controls exercise the same measurement and alert routes as the dashboard. They do not measure detection accuracy or live latency.</p>
    <p><a href="/" target="_blank">Open dashboard</a> and enable an alert at 25 vehicles/min. Then send low followed by high.</p>
    <button onclick="run('low')">Low: 20</button><button onclick="run('high')">High: 30</button>
    <button onclick="run('stale')">Stopped source</button><button onclick="run('offline')">Offline</button>
    <button onclick="run('replay')">Recorded mode</button><button onclick="run('low')">Restore fresh low</button>
    <p>Low then high should trigger once. Repeated high should not. Replay and stale must not trigger a live alert.</p><pre id="result">Ready</pre>
    <script>async function run(name){const r=await fetch('/demo/scenario/'+name,{method:'POST'});document.querySelector('#result').textContent=JSON.stringify(await r.json(),null,2)}</script></html>'''

@app.post("/demo/scenario/{name}")
def demo_scenario(name: str):
    if name not in {"low", "high", "stale", "offline", "replay"}:
        raise HTTPException(404, "Unknown scenario")
    with SessionLocal() as db:
        cam = db.get(CameraSourceModel, CAMERA)
        now = datetime.now(timezone.utc)
        if name in {"stale", "offline"}:
            cam.current_status = name
            db.commit()
            return {"synthetic": True, "scenario": name}
        replay = name == "replay"
        if replay:
            cam.current_status = "replay_not_live"
        elif cam.current_status == "replay_not_live":
            cam.current_status = "recovering"
        cam.recording_time = datetime(2026, 1, 1, tzinfo=timezone.utc) if replay else None
        cam.stream_url = None
        db.commit()
        count = 20 if name == "low" else 30
        stamp = now - timedelta(milliseconds=100)
        if replay:
            prior = db.query(TrafficMeasurementModel).filter_by(camera_id=CAMERA, is_replay=True).order_by(TrafficMeasurementModel.timestamp.desc()).first()
            stamp = (prior.timestamp.replace(tzinfo=timezone.utc) if prior and prior.timestamp else cam.recording_time.replace(tzinfo=timezone.utc)) + timedelta(seconds=60)
        payload = MeasurementCreate(camera_id=CAMERA, timestamp=stamp, vehicle_count=count,
            flow_per_minute=float(count), window_seconds=60, is_replay=replay,
            source_status="replay_not_live" if replay else "live")
        result = submit_measurement(payload, db)
        return {"synthetic": True, "scenario": name, **result}

app.router.routes.append(static_route)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8765")))
