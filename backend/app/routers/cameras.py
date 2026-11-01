from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.config import SOURCE_ATTRIBUTION, DATA_RETENTION_POLICY
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
    query = db.query(CameraSourceModel)
    if pilot_only:
        query = query.filter(CameraSourceModel.is_pilot == True)
    return query.all()

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

@router.get("/{camera_id:path}/status", response_model=CurrentStatusResponse)
def get_camera_current_status(camera_id: str, db: Session = Depends(get_db)):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera location not found.")

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

    fallback_cams = []
    if cam.is_pilot:
        other_pilots = db.query(CameraSourceModel).filter(
            CameraSourceModel.is_pilot == True,
            CameraSourceModel.id != cam.id
        ).all()
        fallback_cams = [c.camera_code for c in other_pilots if c.camera_code]

    return CurrentStatusResponse(
        camera_id=cam.id,
        camera_code=cam.camera_code,
        camera_title=cam.title,
        is_pilot=cam.is_pilot,
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
