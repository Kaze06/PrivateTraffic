from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List

from app.database import get_db
from app.models import TrafficMeasurementModel, CameraSourceModel
from app.schemas import MeasurementCreate, HistoryResponse, HistoricalPoint
from app.config import FLOW_BAND_CLEAR_MAX, FLOW_BAND_NORMAL_MAX

router = APIRouter(prefix="/api", tags=["Measurements & History"])

def derive_traffic_level(flow: float) -> str:
    """Domain rule DOM-1.2: derive level from documented count bands."""
    if flow <= FLOW_BAND_CLEAR_MAX:
        return "clear"
    elif flow <= FLOW_BAND_NORMAL_MAX:
        return "normal"
    return "congested"

@router.post("/measurements", status_code=status.HTTP_201_CREATED)
def submit_measurement(payload: MeasurementCreate, db: Session = Depends(get_db)):
    """
    Data contract endpoint (CV Model Pipeline):
    Ingests minute-by-minute vehicle counts without saving raw video (SEC-1.2).
    """
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == payload.camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Unknown camera source ID")

    ts = payload.timestamp or datetime.now(timezone.utc)
    traffic_level = payload.traffic_level or derive_traffic_level(payload.flow_per_minute)

    # REQ-1.3 & AVA-1.2: Visibly separate replay data from live data
    source_status = "replay" if payload.is_replay else payload.source_status

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

    # Update camera live health
    cam.current_status = source_status
    cam.last_measurement_time = ts
    db.commit()

    return {"status": "success", "id": measurement.id, "traffic_level": traffic_level}

@router.get("/history", response_model=HistoryResponse)
def get_history(
    camera_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    """
    Historical data query endpoint for (Charts & Trend comparison - REQ-1.4).
    Distinguishes empty periods as 'no data' rather than zero traffic (REQ-1.4 Acceptance).
    """
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
