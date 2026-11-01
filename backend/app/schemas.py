from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

class MeasurementCreate(BaseModel):
    camera_id: str = Field(..., example="camera/507320")
    timestamp: Optional[datetime] = None
    vehicle_count: int = Field(..., ge=0)
    flow_per_minute: float = Field(..., ge=0.0)
    traffic_level: Optional[str] = Field(None, example="normal")
    source_status: str = Field(default="live_fresh", example="live_fresh")
    is_replay: bool = Field(default=False)

class CurrentStatusResponse(BaseModel):
    camera_id: str
    camera_code: Optional[str] = None
    camera_title: str
    is_pilot: bool
    status: str
    vehicle_count: Optional[int] = None
    flow_per_minute: Optional[float] = None
    traffic_level: Optional[str] = None
    measurement_time: Optional[datetime] = None
    last_valid_time: Optional[datetime] = None
    stream_url: Optional[str] = None
    seconds_since_update: Optional[float] = None
    has_augmented_stream: bool = False
    failover_candidates: List[str] = []

class SourceComplianceResponse(BaseModel):
    source_name: str
    legal_basis: str
    permitted_use: str
    data_retention: str
    ptz_mitigation: str
    gps_policy: str

class CameraItem(BaseModel):
    id: str
    camera_code: Optional[str]
    title: str
    road_name: str
    latitude: float
    longitude: float
    stream_url: Optional[str]
    is_pilot: bool
    current_status: str

class HistoricalPoint(BaseModel):
    timestamp: datetime
    vehicle_count: int
    flow_per_minute: float
    traffic_level: str
    source_status: str

class HistoryResponse(BaseModel):
    camera_id: str
    has_data: bool
    records: List[HistoricalPoint]
    message: Optional[str] = None
