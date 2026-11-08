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
    current_status = Column(String, default="initializing")  # initializing, live, replay, stale, offline, recovering
    last_measurement_time = Column(DateTime, nullable=True)

class TrafficMeasurementModel(Base):
    __tablename__ = "traffic_measurements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    vehicle_count = Column(Integer, nullable=False)
    flow_per_minute = Column(Float, nullable=False)
    traffic_level = Column(String, nullable=False)  # clear, normal, congested, unable_to_determine
    source_status = Column(String, nullable=False)   # live, replay, stale, offline
    is_replay = Column(Boolean, default=False)

Index("ix_camera_timestamp", TrafficMeasurementModel.camera_id, TrafficMeasurementModel.timestamp)

class AlertRuleModel(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(String, index=True, nullable=False)
    threshold_vpm = Column(Float, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class AlertEventModel(Base):
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, nullable=False)
    camera_id = Column(String, index=True, nullable=False)
    trigger_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    flow_at_trigger = Column(Float, nullable=False)
    threshold_vpm = Column(Float, nullable=False)
    reason = Column(String, nullable=False)
    cleared = Column(Boolean, default=False)
