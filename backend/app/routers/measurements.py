from datetime import datetime, timezone
from sqlalchemy import null
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import TrafficMeasurementModel, CameraSourceModel
from app.schemas import MeasurementCreate, HistoryResponse, HistoricalPoint
from app.health_monitor import utc_time, traffic_level_for, is_replay_source, source_state
from app.routers.alerts import evaluate_threshold_crossing

router = APIRouter(prefix="/api", tags=["Measurements & History"])

@router.post("/measurements", status_code=status.HTTP_201_CREATED)
def submit_measurement(payload: MeasurementCreate, db: Session = Depends(get_db)):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == payload.camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Unknown camera source ID")
    if payload.validity not in {"valid", "invalid"}:
        raise HTTPException(status_code=422, detail="validity must be valid or invalid")
    if payload.window_seconds != 60 or payload.flow_per_minute != payload.vehicle_count:
        raise HTTPException(status_code=422, detail="First release requires a 60-second window with flow equal to its vehicle count")

    replay = bool(payload.is_replay or is_replay_source(cam))
    valid = payload.validity == "valid"
    ts = payload.timestamp
    if ts is not None:
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Source observation timestamp must include its timezone")
        ts = ts.astimezone(timezone.utc)
    if not replay and ts is not None:
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age < 0 or age > 60:
            raise HTTPException(status_code=422, detail="Live observation must be at most 60 seconds old and not in the future")
        if cam.last_measurement_time and ts <= utc_time(cam.last_measurement_time):
            raise HTTPException(status_code=409, detail="Duplicate or late observation rejected")
    if valid:
        if ts is None:
            raise HTTPException(status_code=422, detail="An explicit timezone-aware source observation timestamp is required")
        if replay:
            if not cam.recording_time:
                raise HTTPException(status_code=422, detail="Replay requires its original recording_time")
            if ts < utc_time(cam.recording_time):
                raise HTTPException(status_code=422, detail="Replay observation cannot precede recording_time")
        else:
            if payload.source_status not in {"live", "live_fresh"}:
                raise HTTPException(status_code=422, detail="Valid live observations require a live source status")
        latest = db.query(TrafficMeasurementModel).filter(
            TrafficMeasurementModel.camera_id == cam.id,
            TrafficMeasurementModel.validity == "valid",
            TrafficMeasurementModel.is_replay == replay,
        ).order_by(TrafficMeasurementModel.timestamp.desc()).first()
        if latest and ts <= utc_time(latest.timestamp):
            raise HTTPException(status_code=409, detail="Duplicate or late observation rejected")
    elif not payload.no_measurement_reason or not payload.no_measurement_reason.strip():
        raise HTTPException(status_code=422, detail="Invalid observations require no_measurement_reason")

    if valid and not replay and cam.last_measurement_time and source_state(cam) not in {"live", "live_fresh"}:
        evaluate_threshold_crossing(db, cam.id, 0, "recovering", False, validity="invalid")
    source_status = "replay_not_live" if replay else ("live" if valid else "recovering")
    level = traffic_level_for(cam, payload.flow_per_minute) if valid else "unable_to_determine"
    measurement = TrafficMeasurementModel(
        camera_id=cam.id, timestamp=ts if ts is not None else null(),
        vehicle_count=payload.vehicle_count, flow_per_minute=payload.flow_per_minute,
        window_seconds=60, validity=payload.validity,
        no_measurement_reason=payload.no_measurement_reason if not valid else None,
        traffic_level=level, source_status=source_status, is_replay=replay,
    )
    db.add(measurement)
    cam.current_status = source_status
    if valid and not replay:
        cam.last_measurement_time = ts
    db.commit()
    db.refresh(measurement)
    event = evaluate_threshold_crossing(
        db, cam.id, payload.flow_per_minute, source_status, replay,
        measurement_id=measurement.id, measurement_time=ts, validity=payload.validity,
    )
    return {"status": "success", "id": measurement.id, "traffic_level": level,
            "alert_triggered": bool(event), "validity": payload.validity}

@router.get("/history", response_model=HistoryResponse)
def get_history(
    camera_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    query = db.query(TrafficMeasurementModel).filter(TrafficMeasurementModel.camera_id == camera_id, TrafficMeasurementModel.validity == "valid")
    if start_time:
        query = query.filter(TrafficMeasurementModel.timestamp >= start_time)
    if end_time:
        query = query.filter(TrafficMeasurementModel.timestamp <= end_time)

    records = query.order_by(TrafficMeasurementModel.timestamp.asc()).all()

    if not records:
        return HistoryResponse(
            camera_id=camera_id,
            has_data=False,
            records=[],
            message="No data recorded for this time window."
        )

    return HistoryResponse(
        camera_id=camera_id,
        has_data=True,
        records=[
            HistoricalPoint(
                timestamp=r.timestamp,
                vehicle_count=r.vehicle_count,
                flow_per_minute=r.flow_per_minute,
                traffic_level=r.traffic_level,
                source_status=r.source_status
            )
            for r in records
        ]
    )
