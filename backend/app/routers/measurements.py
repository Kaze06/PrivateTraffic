import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import TrafficMeasurementModel, CameraSourceModel
from app.schemas import MeasurementCreate, HistoryResponse, HistoricalPoint
from app.config import FLOW_BAND_CLEAR_MAX, FLOW_BAND_NORMAL_MAX

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Measurements & History"])

def derive_traffic_level(flow: float) -> str:
    if flow <= FLOW_BAND_CLEAR_MAX:
        return "clear"
    elif flow <= FLOW_BAND_NORMAL_MAX:
        return "normal"
    return "congested"

@router.post("/measurements", status_code=status.HTTP_201_CREATED)
def submit_measurement(payload: MeasurementCreate, db: Session = Depends(get_db)):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == payload.camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Unknown camera source ID")

    ts = payload.timestamp or datetime.now(timezone.utc)
    traffic_level = payload.traffic_level or derive_traffic_level(payload.flow_per_minute)

    if payload.is_replay:
        source_status = "replay_not_live"
    else:
        if cam.current_status in ["stale", "offline", "recovering"]:
            source_status = "live_fresh"
            logger.info(f"Camera {cam.id} successfully recovered to live_fresh.")
        else:
            source_status = "live_fresh"

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

    return {"status": "success", "id": measurement.id, "traffic_level": traffic_level, "source_status": source_status}

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
