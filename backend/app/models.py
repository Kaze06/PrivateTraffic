from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index
from app.database import Base

class CameraSourceModel(Base):
    __tablename__ = "camera_sources"

    id = Column(String, primary_key=True, index=True)
    camera_code = Column(String, index=True, nullable=True)
    title = Column(String, nullable=False)
    road_name = Column(String, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    stream_url = Column(String, nullable=True)
    is_pilot = Column(Boolean, default=False, index=True)
    current_status = Column(String, default="initializing")
    last_measurement_time = Column(DateTime, nullable=True)

class TrafficMeasurementModel(Base):
    __tablename__ = "traffic_measurements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    vehicle_count = Column(Integer, nullable=False)
    flow_per_minute = Column(Float, nullable=False)
    traffic_level = Column(String, nullable=False)
    source_status = Column(String, nullable=False)
    is_replay = Column(Boolean, default=False)

Index("ix_camera_timestamp", TrafficMeasurementModel.camera_id, TrafficMeasurementModel.timestamp)
