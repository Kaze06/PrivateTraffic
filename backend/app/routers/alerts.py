from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models import AlertRuleModel, AlertEventModel, CameraSourceModel
from app.schemas import AlertRuleCreate, AlertRuleResponse, AlertEventResponse

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])

# In-memory tracking of active state crossing to avoid repeat spam
# Format: {camera_id: bool (is_above_threshold)}
_ARMED_STATE = {}

def evaluate_threshold_crossing(db: Session, camera_id: str, flow_vpm: float, source_status: str, is_replay: bool):
    """
    Evaluates alerts upon valid live measurement updates:
    - Fires once when flow reaches or exceeds threshold
    - Suppresses repeats while flow remains above threshold
    - Re-arms once flow drops below threshold
    - Disallows alerts for non-live states (replay, stale, offline)
    """
    if is_replay or source_status not in ["live", "live_fresh"]:
        return None

    rule = db.query(AlertRuleModel).filter(
        AlertRuleModel.camera_id == camera_id,
        AlertRuleModel.enabled == True
    ).order_by(AlertRuleModel.id.desc()).first()

    if not rule:
        return None

    was_above = _ARMED_STATE.get(camera_id, False)
    is_above = flow_vpm >= rule.threshold_vpm

    if is_above and not was_above:
        _ARMED_STATE[camera_id] = True
        event = AlertEventModel(
            rule_id=rule.id,
            camera_id=camera_id,
            trigger_time=datetime.now(timezone.utc),
            flow_at_trigger=flow_vpm,
            threshold_vpm=rule.threshold_vpm,
            reason=f"Flow reached {flow_vpm:.1f} vpm, crossing threshold of {rule.threshold_vpm:.1f} vpm"
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    elif not is_above:
        _ARMED_STATE[camera_id] = False

    return None

@router.post("/rules", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
def set_alert_rule(payload: AlertRuleCreate, db: Session = Depends(get_db)):
    cam = db.query(CameraSourceModel).filter(CameraSourceModel.id == payload.camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera location not found.")

    # Deactivate older rules for this session/source
    db.query(AlertRuleModel).filter(AlertRuleModel.camera_id == payload.camera_id).update({"enabled": False})
    
    rule = AlertRuleModel(
        camera_id=payload.camera_id,
        threshold_vpm=payload.threshold_vpm,
        enabled=True
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    _ARMED_STATE[payload.camera_id] = False

    return AlertRuleResponse(
        id=rule.id,
        camera_id=rule.camera_id,
        threshold_vpm=rule.threshold_vpm,
        enabled=rule.enabled
    )

@router.get("/rules", response_model=Optional[AlertRuleResponse])
def get_active_rule(camera_id: str, db: Session = Depends(get_db)):
    rule = db.query(AlertRuleModel).filter(
        AlertRuleModel.camera_id == camera_id,
        AlertRuleModel.enabled == True
    ).order_by(AlertRuleModel.id.desc()).first()
    if not rule:
        return None
    return AlertRuleResponse(
        id=rule.id,
        camera_id=rule.camera_id,
        threshold_vpm=rule.threshold_vpm,
        enabled=rule.enabled
    )

@router.delete("/rules")
def clear_alert_rule(camera_id: str, db: Session = Depends(get_db)):
    db.query(AlertRuleModel).filter(AlertRuleModel.camera_id == camera_id).update({"enabled": False})
    _ARMED_STATE.pop(camera_id, None)
    db.commit()
    return {"status": "cleared"}

@router.get("/events", response_model=List[AlertEventResponse])
def get_alert_events(camera_id: str, db: Session = Depends(get_db)):
    events = db.query(AlertEventModel).filter(
        AlertEventModel.camera_id == camera_id
    ).order_by(AlertEventModel.trigger_time.desc()).limit(10).all()
    return [
        AlertEventResponse(
            id=e.id,
            camera_id=e.camera_id,
            trigger_time=e.trigger_time,
            flow_at_trigger=e.flow_at_trigger,
            threshold_vpm=e.threshold_vpm,
            reason=e.reason
        )
        for e in events
    ]
