"""Page-session alerts. Sessionless legacy rules never produce live events.

Connect /api/alerts/session, send 'ping' every 5s; disconnect or 20s silence
revokes liveness. Connection registry deliberately requires one API process.
"""
import asyncio
import math
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AlertRuleModel, AlertEventModel, CameraSourceModel, TrafficMeasurementModel
from app.schemas import AlertRuleCreate, AlertRuleResponse, AlertEventResponse

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])
SESSION_TIMEOUT_SECONDS = 20
# SourceWindow accepts frame gaps up to 5s; its next window ends on a frame.
SOURCE_WINDOW_JITTER_SECONDS = 5
_sessions = {}
_lock = threading.RLock()


def _utc(value):
    # SQLite stores UTC datetimes without the offset.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _active(session_id):
    seen = _sessions.get(session_id)
    return seen is not None and time.monotonic() - seen < SESSION_TIMEOUT_SECONDS


def _require_session(session_id):
    if session_id is not None and not _active(session_id):
        raise HTTPException(status_code=409, detail="Alert connection ended. Reconnect and enable alerts again.")


@router.websocket("/session")
async def alert_session(websocket: WebSocket):
    await websocket.accept()
    session_id = secrets.token_urlsafe(32)
    with _lock:
        _sessions[session_id] = time.monotonic()
    try:
        await websocket.send_json({"session_id": session_id, "heartbeat_seconds": 5,
                                   "timeout_seconds": SESSION_TIMEOUT_SECONDS})
        while True:
            message = await asyncio.wait_for(websocket.receive_text(), timeout=SESSION_TIMEOUT_SECONDS)
            if message != "ping":
                await websocket.close(code=1008)
                break
            with _lock:
                if not _active(session_id):
                    break
                _sessions[session_id] = time.monotonic()
            await websocket.send_text("pong")
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        with _lock:
            _sessions.pop(session_id, None)
        try:
            await websocket.close()
        except RuntimeError:
            pass


def evaluate_threshold_crossing(db: Session, camera_id: str, flow_vpm: float,
                                source_status: str, is_replay: bool, *,
                                measurement_id=None, measurement_time=None, validity="valid"):
    """Persist each rule's first baseline; emit only a subsequent upward crossing."""
    now = datetime.now(timezone.utc)
    with _lock:
        rules = db.query(AlertRuleModel).filter_by(camera_id=camera_id, enabled=True).all()
        measurement = db.get(TrafficMeasurementModel, measurement_id) if measurement_id is not None else None
        valid = (measurement is not None and not is_replay and validity == "valid"
                 and source_status in ("live", "live_fresh"))
        camera = db.get(CameraSourceModel, camera_id)
        valid = valid and camera is not None and camera.current_status in ("live", "live_fresh")
        if valid:
            age = (now - _utc(measurement.timestamp)).total_seconds() if measurement.timestamp else -1
            valid = (measurement.camera_id == camera_id and measurement.validity == "valid"
                     and not measurement.is_replay and measurement.source_status in ("live", "live_fresh")
                     and 0 <= age <= 60 and math.isfinite(flow_vpm)
                     and measurement.flow_per_minute == flow_vpm and measurement.window_seconds == 60
                     and measurement.vehicle_count == flow_vpm and flow_vpm >= 0)
            if measurement_time is not None:
                valid = valid and _utc(measurement_time) == _utc(measurement.timestamp)
            latest = db.query(TrafficMeasurementModel).filter_by(camera_id=camera_id).order_by(
                TrafficMeasurementModel.timestamp.desc(), TrafficMeasurementModel.id.desc()).first()
            valid = valid and latest.id == measurement.id
        previous = None
        if valid:
            previous = db.query(TrafficMeasurementModel).filter(
                TrafficMeasurementModel.camera_id == camera_id,
                TrafficMeasurementModel.id < measurement.id,
            ).order_by(TrafficMeasurementModel.id.desc()).first()
        continuous = bool(previous and previous.timestamp and previous.validity == "valid"
                          and not previous.is_replay and previous.source_status in ("live", "live_fresh"))
        if continuous:
            source_gap = (_utc(measurement.timestamp) - _utc(previous.timestamp)).total_seconds()
            continuous = 0 < source_gap <= measurement.window_seconds + SOURCE_WINDOW_JITTER_SECONDS
        events = []
        for rule in rules:
            if not _active(rule.session_id):
                continue
            if not valid:
                rule.was_above = None
                continue
            if not continuous:
                rule.was_above = None
            above = flow_vpm >= rule.threshold_vpm
            if rule.was_above is False and above:
                event = AlertEventModel(rule_id=rule.id, camera_id=camera_id,
                    measurement_id=measurement.id, trigger_time=now,
                    flow_at_trigger=flow_vpm, threshold_vpm=rule.threshold_vpm,
                    reason=f"Flow crossed {rule.threshold_vpm:g} veh/min (observed {flow_vpm:g} veh/min).")
                db.add(event)
                events.append(event)
            rule.was_above = above
            rule.last_seen_at = now
        db.commit()
        return events[0] if events else None


def _rule_response(rule):
    return AlertRuleResponse(id=rule.id, camera_id=rule.camera_id, session_id=rule.session_id,
                             threshold_vpm=rule.threshold_vpm, enabled=rule.enabled)


@router.post("/rules", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
def set_alert_rule(payload: AlertRuleCreate, db: Session = Depends(get_db)):
    with _lock:
        _require_session(payload.session_id)
        if not db.get(CameraSourceModel, payload.camera_id):
            raise HTTPException(status_code=404, detail="Camera location not found.")
        db.query(AlertRuleModel).filter_by(camera_id=payload.camera_id, session_id=payload.session_id).update({"enabled": False})
        rule = AlertRuleModel(camera_id=payload.camera_id, session_id=payload.session_id,
                             threshold_vpm=payload.threshold_vpm, enabled=True, was_above=None,
                             last_seen_at=datetime.now(timezone.utc) if payload.session_id else None)
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return _rule_response(rule)


@router.get("/rules", response_model=Optional[AlertRuleResponse])
def get_active_rule(camera_id: str, session_id: Optional[str] = None, db: Session = Depends(get_db)):
    with _lock:
        _require_session(session_id)
        rule = db.query(AlertRuleModel).filter_by(camera_id=camera_id, session_id=session_id,
                                                enabled=True).order_by(AlertRuleModel.id.desc()).first()
        return _rule_response(rule) if rule else None


@router.delete("/rules")
def clear_alert_rule(camera_id: str, session_id: Optional[str] = None, db: Session = Depends(get_db)):
    with _lock:
        _require_session(session_id)
        db.query(AlertRuleModel).filter_by(camera_id=camera_id, session_id=session_id).update({"enabled": False, "was_above": None})
        db.commit()
        return {"status": "cleared"}


@router.get("/events", response_model=List[AlertEventResponse])
def get_alert_events(camera_id: str, session_id: Optional[str] = None, db: Session = Depends(get_db)):
    with _lock:
        _require_session(session_id)
        events = db.query(AlertEventModel).join(AlertRuleModel, AlertRuleModel.id == AlertEventModel.rule_id).filter(
            AlertEventModel.camera_id == camera_id, AlertRuleModel.session_id == session_id
        ).order_by(AlertEventModel.trigger_time.desc(), AlertEventModel.id.desc()).limit(10).all()
        return [AlertEventResponse(id=e.id, rule_id=e.rule_id, measurement_id=e.measurement_id,
                                  camera_id=e.camera_id, trigger_time=e.trigger_time,
                                  flow_at_trigger=e.flow_at_trigger, threshold_vpm=e.threshold_vpm,
                                  reason=e.reason) for e in events]
