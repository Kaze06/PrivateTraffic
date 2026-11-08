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

    ts = payload.timestamp or datetime.now(timezone.utc)
    traffic_level = payload.traffic_level or derive_traffic_level(payload.flow_per_minute)

    source_status = "replay_not_live" if payload.is_replay else payload.source_status

    measurement = TrafficMeasurementModel(
        camera_id=payload.camera_id,
        timestamp=ts,
        vehicle_count=payload.vehicle_count,
        flow_per_minute=payload.flow_per_minute,
        traffic_level=traffic_level,
        source_status=source_status,
        is_replay=payload.is_replay
    )
    db.add(measurement)

    cam.current_status = source_status
    cam.last_measurement_time = ts
    db.commit()

    # Fixed threshold alert evaluation
    alert_event = evaluate_threshold_crossing(
        db, payload.camera_id, payload.flow_per_minute, source_status, payload.is_replay
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
