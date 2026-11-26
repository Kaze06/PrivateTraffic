from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.detector_manager import detector_manager, missing_detector_dependencies
from app.health_monitor import is_replay_source
from app.models import CameraSourceModel
from app.routers.cameras import set_active_focus


router = APIRouter(prefix="/api/detection", tags=["Detection"])


class DetectionStartRequest(BaseModel):
    camera_id: str


@router.post("/start")
def start_detection(payload: DetectionStartRequest, request: Request, db: Session = Depends(get_db)):
    camera = db.query(CameraSourceModel).filter_by(id=payload.camera_id).first()
    if camera is None:
        raise HTTPException(status_code=404, detail="Unknown camera source ID")
    if not camera.stream_url or is_replay_source(camera):
        raise HTTPException(status_code=409, detail="Detection view requires a live camera source")
    missing = missing_detector_dependencies()
    if missing:
        packages = ", ".join(missing)
        raise HTTPException(
            status_code=503,
            detail=f"Vehicle detection needs {packages}. Install the project requirements in the Python environment running this dashboard, then retry.",
        )

    set_active_focus({"camera_id": camera.id})
    api_base_url = str(request.base_url).rstrip("/")
    return detector_manager.start(camera.id, api_base_url)


@router.get("/status")
def get_detection_status(camera_id: str):
    return detector_manager.status(camera_id)


@router.post("/stop")
def stop_detection():
    return detector_manager.stop()
