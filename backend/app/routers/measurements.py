from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import TrafficMeasurementModel, CameraSourceModel
from app.schemas import MeasurementCreate, HistoryResponse, HistoricalPoint
from app.config import DEFAULT_T1, DEFAULT_T2
from app.routers.alerts import evaluate_threshold_crossing

router = APIRouter(prefix="/api", tags=["Measurements & History"])

def derive_traffic_level(flow: float) -> str:
    if flow < DEFAULT_T1:
        return "clear"
    elif flow < DEFAULT_T2:
        return "normal"
    return "congested"

@router.post("/measurements", status_code=status.HTTP_201_CREATED)
def submit_measurement(payload: MeasurementCreate, db: Session = Depends(get_db)):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == payload.camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Unknown camera source ID")

    ts = payload.timestamp
    if ts is None or ts.tzinfo is None or ts.utcoffset() is None:
        raise HTTPException(status_code=422, detail="An explicit timezone-aware source observation timestamp is required")
    ts = ts.astimezone(timezone.utc)
    replay = payload.is_replay or cam.current_status == "replay_not_live" or bool(cam.stream_url and "/uploads/" in cam.stream_url)
    if not replay:
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age < 0 or age > 60:
            raise HTTPException(status_code=422, detail="Live source observation must be no more than 60 seconds old and not in the future")
        if cam.last_measurement_time:
            last_time = cam.last_measurement_time
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            if ts <= last_time:
                raise HTTPException(status_code=409, detail="Observation timestamp must increase; duplicate or late observation rejected")
    traffic_level = payload.traffic_level or derive_traffic_level(payload.flow_per_minute)

    is_replay = payload.is_replay or (cam.stream_url and "/uploads" in cam.stream_url)
    source_status = "replay_not_live" if is_replay else payload.source_status
    if not is_replay and (source_status in ["live", "live_fresh"] or cam.current_status == "recovering"):
        source_status = "live"

    measurement = TrafficMeasurementModel(
        camera_id=payload.camera_id,
        timestamp=ts,
        vehicle_count=payload.vehicle_count,
        flow_per_minute=payload.flow_per_minute,
        traffic_level=traffic_level,
        source_status=source_status,
        is_replay=is_replay
    )
    db.add(measurement)

    cam.current_status = source_status
    cam.last_measurement_time = ts
    db.commit()

    alert_event = evaluate_threshold_crossing(
        db, payload.camera_id, payload.flow_per_minute, source_status, is_replay
    )

    return {
        "status": "success",
        "id": measurement.id,
        "traffic_level": traffic_level,
        "alert_triggered": bool(alert_event)
    }

@router.get("/history", response_model=HistoryResponse)
def get_history(
    camera_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    query = db.query(TrafficMeasurementModel).filter(TrafficMeasurementModel.camera_id == camera_id)
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
