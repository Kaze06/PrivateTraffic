from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index
from app.database import Base

class CameraSourceModel(Base):
    """
    Cache table for camera metadata loaded from 511MN.json and pilot_cameras.json
    """
    __tablename__ = "camera_sources"

    id = Column(String, primary_key=True, index=True) # e.g. "camera/507320"
    camera_code = Column(String, index=True, nullable=True) # e.g. "C631"
    title = Column(String, nullable=False)
    road_name = Column(String, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    stream_url = Column(String, nullable=True)
    is_pilot = Column(Boolean, default=False, index=True)
    current_status = Column(String, default="unavailable") # live, stale, unavailable, replay
    last_measurement_time = Column(DateTime, nullable=True)

class TrafficMeasurementModel(Base):
    """
    Aggregate traffic records (SEC-1.2: aggregates stored, no raw video retained)
    """
    __tablename__ = "traffic_measurements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    vehicle_count = Column(Integer, nullable=False)
    flow_per_minute = Column(Float, nullable=False)
    traffic_level = Column(String, nullable=False) # "clear", "normal", "congested"
    source_status = Column(String, nullable=False)  # "live", "replay", "stale"
    is_replay = Column(Boolean, default=False)

# Compound index for efficient historical queries (REQ-1.4)
Index("ix_camera_timestamp", TrafficMeasurementModel.camera_id, TrafficMeasurementModel.timestamp)
