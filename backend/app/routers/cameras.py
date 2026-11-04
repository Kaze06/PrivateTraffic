import os
import shutil
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.config import SOURCE_ATTRIBUTION, DATA_RETENTION_POLICY, UPLOAD_DIR
from app.camera_service import load_pilot_codes
from app.models import CameraSourceModel, TrafficMeasurementModel
from app.schemas import (
    CameraItem, 
    CurrentStatusResponse, 
    SourceComplianceResponse
)
from app.video_stream import stream_manager

router = APIRouter(prefix="/api/cameras", tags=["Cameras & Status"])

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
    camera_code: str = Form(..., example="C8351"),
    video_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.camera_code == camera_code).first()
    if not cam:
        raise HTTPException(status_code=404, detail=f"No camera found with code: {camera_code}")

    clean_filename = f"{camera_code}_backup_{int(datetime.now().timestamp())}.mp4"
    disk_path = os.path.join(UPLOAD_DIR, clean_filename)
    
    with open(disk_path, "wb") as buffer:
        shutil.copyfileobj(video_file.file, buffer)

    web_accessible_url = f"/uploads/{clean_filename}"
    cam.stream_url = web_accessible_url
    cam.current_status = "replay_not_live"
    cam.last_measurement_time = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "success",
        "camera_id": cam.id,
        "camera_code": cam.camera_code,
        "mode": "replay_not_live",
        "stream_url": web_accessible_url,
        "message": f"Backup video configured for {camera_code}. Source set to 'replay_not_live'."
    }

@router.post("/purge-replays")
def purge_all_replays(db: Session = Depends(get_db)):
    deleted_files = 0
    if os.path.exists(UPLOAD_DIR):
        for fname in os.listdir(UPLOAD_DIR):
            fpath = os.path.join(UPLOAD_DIR, fname)
            try:
                if os.path.isfile(fpath) or os.path.islink(fpath):
                    os.unlink(fpath)
                    deleted_files += 1
            except Exception:
                pass

    cams = db.query(CameraSourceModel).all()
    reset_count = 0
    for cam in cams:
        if cam.camera_code:
            cam.stream_url = f"https://video.dot.state.mn.us/public/{cam.camera_code}.stream/playlist.m3u8"
            if cam.current_status == "replay_not_live":
                cam.current_status = "initializing"
                reset_count += 1

    db.commit()
    return {
        "status": "success",
        "deleted_files": deleted_files,
        "cameras_reset": reset_count,
        "message": f"Purged {deleted_files} files. Restored {reset_count} cameras back to native live stream."
    }

@router.get("/{camera_id:path}/status", response_model=CurrentStatusResponse)
def get_camera_current_status(camera_id: str, db: Session = Depends(get_db)):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera location not found.")

    pilot_map = load_pilot_codes()
    is_pilot = bool(cam.camera_code and cam.camera_code in pilot_map)
    is_default = bool(is_pilot and pilot_map[cam.camera_code].get("is_default", False))

    latest_meas = (
        db.query(TrafficMeasurementModel)
        .filter(TrafficMeasurementModel.camera_id == camera_id)
        .order_by(TrafficMeasurementModel.timestamp.desc())
        .first()
    )

    now = datetime.now(timezone.utc)
    seconds_since = None
    if latest_meas and latest_meas.timestamp:
        seconds_since = (now - latest_meas.timestamp.replace(tzinfo=timezone.utc)).total_seconds()

    fallback_cams = [code for code in pilot_map.keys() if code != cam.camera_code] if is_pilot else []

    return CurrentStatusResponse(
        camera_id=cam.id,
        camera_code=cam.camera_code,
        camera_title=cam.title,
        is_pilot=is_pilot,
        is_default=is_default,
        status=cam.current_status,
        vehicle_count=latest_meas.vehicle_count if latest_meas else None,
        flow_per_minute=latest_meas.flow_per_minute if latest_meas else None,
        traffic_level=latest_meas.traffic_level if latest_meas else "unable_to_determine",
        measurement_time=latest_meas.timestamp if latest_meas else None,
        last_valid_time=cam.last_measurement_time,
        stream_url=cam.stream_url,
        seconds_since_update=seconds_since,
        has_augmented_stream=stream_manager.has_active_stream(cam.id),
        failover_candidates=fallback_cams
    )

@router.get("/{camera_id:path}/stream")
async def get_augmented_video_stream(camera_id: str):
    return StreamingResponse(
        stream_manager.get_stream(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
