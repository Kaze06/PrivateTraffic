import os
import time
import signal
import asyncio
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import shutil
import uuid

from app.database import get_db
from app.config import SOURCE_ATTRIBUTION, DATA_RETENTION_POLICY, UPLOAD_DIR, DEFAULT_T1, DEFAULT_T2
from app.camera_service import load_pilot_codes
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.schemas import (
    CameraItem, 
    CurrentStatusResponse, 
    SourceComplianceResponse
)
from app.video_stream import stream_manager, get_shm_frame_path, frame_metadata, clear_frame
from app.health_monitor import utc_time, source_state, is_replay_source, calibration_valid, traffic_level_for
from app.routers.alerts import evaluate_threshold_crossing

router = APIRouter(prefix="/api/cameras", tags=["Cameras & Status"])

ACTIVE_FOCUS_CAMERA_ID: Optional[str] = None
SHM_DIR = Path("/dev/shm") if Path("/dev/shm").is_dir() else Path(os.environ.get("TEMP", "/tmp"))

SHUTDOWN_REQUESTED = False

try:
    orig_sigint = signal.getsignal(signal.SIGINT)
    def _sigint_handler(sig, frame):
        global SHUTDOWN_REQUESTED
        SHUTDOWN_REQUESTED = True
        if callable(orig_sigint):
            orig_sigint(sig, frame)
    signal.signal(signal.SIGINT, _sigint_handler)

    orig_sigterm = signal.getsignal(signal.SIGTERM)
    def _sigterm_handler(sig, frame):
        global SHUTDOWN_REQUESTED
        SHUTDOWN_REQUESTED = True
        if callable(orig_sigterm):
            orig_sigterm(sig, frame)
    signal.signal(signal.SIGTERM, _sigterm_handler)
except Exception:
    pass

@router.get("/active-focus")
def get_active_focus():
    return {"active_camera_id": ACTIVE_FOCUS_CAMERA_ID}

@router.post("/active-focus")
def set_active_focus(payload: dict):
    global ACTIVE_FOCUS_CAMERA_ID
    cam_id = payload.get("camera_id")
    if cam_id:
        ACTIVE_FOCUS_CAMERA_ID = urllib.parse.unquote(cam_id)
    return {"status": "ok", "active_camera_id": ACTIVE_FOCUS_CAMERA_ID}

@router.websocket("/{camera_id:path}/ws")
async def websocket_video_stream(websocket: WebSocket, camera_id: str):
    await websocket.accept()
    cam_id = urllib.parse.unquote(camera_id)
    frame_path = get_shm_frame_path(cam_id)
    last_mtime = 0.0

    async def client_listener():
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
        except (asyncio.CancelledError, Exception):
            pass

    listener_task = asyncio.create_task(client_listener())

    try:
        while not listener_task.done() and not SHUTDOWN_REQUESTED:
            if frame_metadata(cam_id):
                try:
                    mtime = frame_path.stat().st_mtime
                    if mtime > last_mtime:
                        last_mtime = mtime
                        with open(frame_path, "rb") as f:
                            data = f.read()
                        await websocket.send_bytes(data)
                except Exception:
                    break
            await asyncio.sleep(0.015)
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        listener_task.cancel()
        try:
            await listener_task
        except (asyncio.CancelledError, Exception):
            pass

@router.get("", response_model=List[CameraItem])
def list_cameras(pilot_only: bool = False, db: Session = Depends(get_db)):
    pilot_map = load_pilot_codes()
    cams = db.query(CameraSourceModel).all()
    
    results = []
    for c in cams:
        is_pilot = False
        is_default = False
        stream_url = c.stream_url
        
        if c.camera_code and c.camera_code in pilot_map:
            is_pilot = True
            is_default = bool(pilot_map[c.camera_code].get("is_default", False))
            if not (stream_url and stream_url.startswith("/uploads")):
                stream_url = pilot_map[c.camera_code].get("stream_url", stream_url)
                
        if pilot_only and not is_pilot:
            continue
            
        results.append(CameraItem(
            id=c.id,
            camera_code=c.camera_code,
            title=c.title,
            road_name=c.road_name,
            latitude=c.latitude,
            longitude=c.longitude,
            stream_url=stream_url,
            is_pilot=is_pilot,
            is_default=is_default,
            current_status=c.current_status
        ))
        
    return results

@router.get("/compliance", response_model=SourceComplianceResponse)
def get_source_compliance():
    return SourceComplianceResponse(
        source_name=SOURCE_ATTRIBUTION,
        legal_basis="Public traveler information, provided on an 'as-is' and 'as-available' basis without uptime SLA.",
        permitted_use="Non-commercial, academic research and course testing.",
        data_retention=DATA_RETENTION_POLICY,
        ptz_mitigation="Pre-vetted multi-camera failover pool configured in pilot_cameras.json",
        gps_policy="Strictly GPS-free; user locations are never requested or stored."
    )

@router.post("/upload-replay")
async def upload_backup_replay(
    camera_code: str = Form(..., examples=["C8351"]),
    video_file: Optional[UploadFile] = File(None),
    recording_time: datetime = Form(...),
    stream_url: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.camera_code == camera_code).first()
    if not cam:
        raise HTTPException(status_code=404, detail=f"No camera found with code: {camera_code}")

    if recording_time.tzinfo is None or recording_time.utcoffset() is None:
        raise HTTPException(status_code=422, detail="recording_time must be timezone-aware")
    recording_time = recording_time.astimezone(timezone.utc)
    if recording_time > datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="recording_time cannot be in the future")
    if (video_file is None) == (stream_url is None):
        raise HTTPException(status_code=422, detail="Supply exactly one video_file or replay stream_url")
    if stream_url is not None:
        parsed = urllib.parse.urlparse(stream_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=422, detail="Replay stream_url must be an HTTP(S) URL")
        web_accessible_url = stream_url
    else:
        clean_filename = f"replay_{uuid.uuid4().hex}.mp4"
        disk_path = os.path.join(UPLOAD_DIR, clean_filename)
        with open(disk_path, "wb") as buffer:
            shutil.copyfileobj(video_file.file, buffer)
        web_accessible_url = f"/uploads/{clean_filename}"
    if not is_replay_source(cam):
        cam.original_stream_url = cam.stream_url
    cam.stream_url = web_accessible_url
    cam.current_status = "replay_not_live"
    cam.recording_time = recording_time
    clear_frame(cam.id)
    db.commit()
    evaluate_threshold_crossing(db, cam.id, 0, "replay_not_live", True, validity="invalid")

    return {
        "status": "success",
        "camera_id": cam.id,
        "camera_code": cam.camera_code,
        "mode": "replay_not_live",
        "stream_url": web_accessible_url,
        "recording_time": recording_time,
        "message": f"Backup video configured for {camera_code}. Source set to 'replay_not_live'."
    }

@router.post("/purge-replays")
def purge_all_replays(db: Session = Depends(get_db)):
    root = Path(UPLOAD_DIR).resolve()
    deleted_files = 0
    reset_count = 0
    failures = []
    pilot_map = load_pilot_codes()
    for cam in db.query(CameraSourceModel).all():
        if not is_replay_source(cam):
            continue
        # Only delete the configured replay, never unrelated uploads or external paths.
        url = cam.stream_url or ""
        if url.startswith("/uploads/"):
            name = urllib.parse.unquote(url[len("/uploads/"):])
            path = root / name
            if Path(name).name != name or path.is_symlink() or path.resolve().parent != root:
                failures.append(cam.id)
                continue
            try:
                if path.is_file():
                    path.unlink()
                    deleted_files += 1
            except OSError:
                failures.append(cam.id)
                continue
        original = getattr(cam, "original_stream_url", None)
        cam.stream_url = original or pilot_map.get(cam.camera_code, {}).get("stream_url")
        cam.recording_time = None
        cam.last_measurement_time = None
        cam.current_status = "initializing" if cam.stream_url else "unavailable"
        clear_frame(cam.id)
        evaluate_threshold_crossing(db, cam.id, 0, cam.current_status, False, validity="invalid")
        reset_count += 1
    db.commit()
    return {"status": "partial" if failures else "success", "deleted_files": deleted_files,
            "cameras_reset": reset_count, "failed_camera_ids": failures}

@router.get("/{camera_id:path}/status", response_model=CurrentStatusResponse)
def get_camera_current_status(
    camera_id: str,
    t1: Optional[float] = None,
    t2: Optional[float] = None,
    db: Session = Depends(get_db)
):
    cam_id = urllib.parse.unquote(camera_id)
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == cam_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera location not found.")

    pilot_map = load_pilot_codes()
    is_pilot = bool(cam.camera_code and cam.camera_code in pilot_map)
    is_default = bool(is_pilot and pilot_map[cam.camera_code].get("is_default", False))

    latest_meas = (
        db.query(TrafficMeasurementModel)
        .filter(TrafficMeasurementModel.camera_id == cam_id)
        .order_by(TrafficMeasurementModel.id.desc())
        .first()
    )

    now = datetime.now(timezone.utc)
    seconds_since = (now - utc_time(latest_meas.timestamp)).total_seconds() if latest_meas and latest_meas.timestamp else None
    replay = is_replay_source(cam)
    same_mode = bool(latest_meas and latest_meas.is_replay == replay)
    if not same_mode:
        seconds_since = None
    current_status = source_state(cam, now)
    meta = frame_metadata(cam_id)
    has_active = bool(meta and meta["is_replay"] == replay)
    usable = bool(latest_meas and latest_meas.validity == "valid" and latest_meas.is_replay == replay)
    if not replay:
        usable = usable and current_status in {"live", "live_fresh"} and seconds_since is not None and 0 <= seconds_since <= 60
        if current_status in {"live", "live_fresh"} and not usable:
            current_status = "stale"
    else:
        usable = usable and bool(cam.recording_time) and utc_time(latest_meas.timestamp) >= utc_time(cam.recording_time)
    level = traffic_level_for(cam, latest_meas.flow_per_minute) if usable else "unable_to_determine"
    reason = None
    if not usable:
        reason = (latest_meas.no_measurement_reason if latest_meas and latest_meas.validity != "valid" else None) or (
            "replay_recording_time_unavailable" if replay and not cam.recording_time else
            "source_observation_unavailable" if not latest_meas else "source_observation_not_fresh")
    elif not calibration_valid(cam):
        reason = "calibration_unavailable"

    fallback_cams = [code for code in pilot_map.keys() if code != cam.camera_code] if is_pilot else []

    return CurrentStatusResponse(
        camera_id=cam.id,
        camera_code=cam.camera_code,
        camera_title=cam.title,
        is_pilot=is_pilot,
        is_default=is_default,
        status=current_status,
        vehicle_count=latest_meas.vehicle_count if usable else None,
        flow_per_minute=latest_meas.flow_per_minute if usable else None,
        traffic_level=level,
        measurement_time=utc_time(latest_meas.timestamp) if same_mode and latest_meas.timestamp else None,
        timestamp_basis=latest_meas.timestamp_basis if same_mode and latest_meas else "source_observation",
        measurement_window_seconds=latest_meas.window_seconds if usable else None,
        last_valid_time=utc_time(cam.last_measurement_time) if cam.last_measurement_time else None,
        stream_url=cam.stream_url,
        seconds_since_update=seconds_since,
        has_augmented_stream=has_active,
        is_replay=replay, recording_time=utc_time(cam.recording_time) if cam.recording_time else None, calibrated=calibration_valid(cam),
        no_measurement_reason=reason,
        failover_candidates=fallback_cams
    )

@router.get("/{camera_id:path}/stream")
async def get_augmented_video_stream(camera_id: str):
    cam_id = urllib.parse.unquote(camera_id)
    return StreamingResponse(
        stream_manager.get_stream(cam_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
