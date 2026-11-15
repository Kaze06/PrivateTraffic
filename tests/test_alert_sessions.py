"""Alert regression tests use a private in-memory database and the real router."""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import CameraSourceModel, TrafficMeasurementModel, AlertEventModel, AlertRuleModel
from app.routers import alerts


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(alerts.router)
    def db_override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = db_override
    with factory() as db:
        db.add(CameraSourceModel(id="test", title="Synthetic", latitude=0, longitude=0, current_status="live"))
        db.commit()
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def rule(client, session=None, threshold=25):
    result = client.post("/api/alerts/rules", json={"camera_id": "test", "session_id": session, "threshold_vpm": threshold})
    assert result.status_code == 201, result.text
    return result.json()


def observe(factory, flow, *, changes=None, referenced=True, argument_changes=None):
    values = dict(camera_id="test", timestamp=datetime.now(timezone.utc), vehicle_count=flow,
                  flow_per_minute=flow, traffic_level="unable_to_determine", source_status="live",
                  is_replay=False, window_seconds=60, validity="valid")
    values.update(changes or {})
    with factory() as db:
        m = TrafficMeasurementModel(**values)
        db.add(m)
        db.commit()
        kwargs = dict(measurement_id=m.id if referenced else None, measurement_time=m.timestamp, validity=m.validity)
        kwargs.update(argument_changes or {})
        return bool(alerts.evaluate_threshold_crossing(db, "test", flow, m.source_status, m.is_replay, **kwargs))


def events(client, session=None):
    params = {"camera_id": "test"}
    if session:
        params["session_id"] = session
    response = client.get("/api/alerts/events", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_initial_high_baseline_crossing_sustained_and_rearm(setup):
    client, factory = setup
    with client.websocket_connect("/api/alerts/session") as ws:
        session = ws.receive_json()["session_id"]
        created = rule(client, session)
        assert not observe(factory, 30)
        assert not observe(factory, 35)
        assert not observe(factory, 20)
        assert observe(factory, 25)
        assert not observe(factory, 40)
        assert not observe(factory, 10)
        assert observe(factory, 26)
        result = events(client, session)
        assert len(result) == 2
        assert all(e["measurement_id"] and e["rule_id"] == created["id"] for e in result)
        with factory() as db:
            assert db.get(AlertRuleModel, created["id"]).was_above is True


def test_scopes_and_disable_do_not_change_other_page(setup):
    client, factory = setup
    with client.websocket_connect("/api/alerts/session") as a, client.websocket_connect("/api/alerts/session") as b:
        sa, sb = a.receive_json()["session_id"], b.receive_json()["session_id"]
        rule(client, sa, 25)
        rule(client, sb, 35)
        rule(client)  # legacy request must not disable either page
        assert not observe(factory, 20)
        assert observe(factory, 30)
        assert len(events(client, sa)) == 1
        assert events(client, sb) == events(client) == []
        assert client.delete("/api/alerts/rules", params={"camera_id":"test", "session_id":sa}).status_code == 200
        assert observe(factory, 40)
        assert len(events(client, sb)) == 1
        assert len(events(client, sa)) == 1


def test_legacy_rule_never_emits_and_can_be_read_deleted(setup):
    client, factory = setup
    created = rule(client)
    assert client.get("/api/alerts/rules", params={"camera_id":"test"}).json()["id"] == created["id"]
    assert not observe(factory, 10)
    assert not observe(factory, 40)
    assert events(client) == []
    assert client.delete("/api/alerts/rules", params={"camera_id":"test"}).status_code == 200
    assert client.get("/api/alerts/rules", params={"camera_id":"test"}).json() is None


@pytest.mark.parametrize("changes, referenced, arguments", [
    ({}, False, {}),
    ({}, True, {"measurement_id":99999}),
    ({"timestamp":datetime.now(timezone.utc)-timedelta(seconds=61)}, True, {}),
    ({"timestamp":datetime.now(timezone.utc)+timedelta(minutes=5)}, True, {}),
    ({"is_replay":True}, True, {}),
    ({"source_status":"stale"}, True, {}),
    ({"source_status":"offline"}, True, {}),
    ({"source_status":"recovering"}, True, {}),
    ({"validity":"invalid"}, True, {}),
    ({"camera_id":"other"}, True, {}),
    ({"vehicle_count":1}, True, {}),
    ({"window_seconds":30}, True, {}),
    ({}, True, {"measurement_time":datetime.now(timezone.utc)-timedelta(days=1)}),
])
def test_invalid_reference_or_nonlive_cannot_emit(setup, changes, referenced, arguments):
    client, factory = setup
    with client.websocket_connect("/api/alerts/session") as ws:
        session=ws.receive_json()["session_id"]
        rule(client,session)
        assert not observe(factory,10)
        assert not observe(factory,40,changes=changes, referenced=referenced, argument_changes=arguments)
        assert events(client,session)==[]


def test_disconnect_revokes_and_new_connection_needs_new_rule(setup):
    client, factory=setup
    with client.websocket_connect("/api/alerts/session") as ws:
        session=ws.receive_json()["session_id"]
        rule(client,session)
        assert not observe(factory,10)
        ws.send_text("ping")
        assert ws.receive_text()=="pong"
    assert not observe(factory,40)
    assert client.get("/api/alerts/events", params={"camera_id":"test","session_id":session}).status_code==409
    with client.websocket_connect("/api/alerts/session") as ws:
        other=ws.receive_json()["session_id"]
        assert other != session
        assert not observe(factory,10)
        assert not observe(factory,40)
        assert events(client,other)==[]


def test_expired_heartbeat_blocks_evaluator_without_waiting_for_socket_cleanup(setup, monkeypatch):
    client,factory=setup
    with client.websocket_connect("/api/alerts/session") as ws:
        session=ws.receive_json()["session_id"]
        rule(client,session)
        assert not observe(factory,10)
        monkeypatch.setitem(alerts._sessions,session,alerts.time.monotonic()-21)
        assert not observe(factory,40)
        assert client.post("/api/alerts/rules",json={"camera_id":"test","session_id":session,"threshold_vpm":25}).status_code==409
        with factory() as db:
            assert db.query(AlertEventModel).count()==0


def test_timeout_closes_connection(setup, monkeypatch):
    client,_=setup
    monkeypatch.setattr(alerts,"SESSION_TIMEOUT_SECONDS",0.1)
    with client.websocket_connect("/api/alerts/session") as ws:
        session=ws.receive_json()["session_id"]
        assert ws.receive()["type"]=="websocket.close"
    assert session not in alerts._sessions


def test_replacing_threshold_establishes_new_baseline(setup):
    client,factory=setup
    with client.websocket_connect("/api/alerts/session") as ws:
        session=ws.receive_json()["session_id"]
        rule(client,session)
        assert not observe(factory,10)
        rule(client,session,20)
        assert not observe(factory,30)
        assert not observe(factory,10)
        assert observe(factory,20)
        assert len(events(client,session))==1

@pytest.mark.parametrize("source_state", ["stale", "offline", "recovering", "replay_not_live"])
def test_camera_state_overrides_live_measurement_claim(setup, source_state):
    client,factory=setup
    with client.websocket_connect("/api/alerts/session") as ws:
        session=ws.receive_json()["session_id"]
        rule(client,session)
        assert not observe(factory,10)
        with factory() as db:
            db.get(CameraSourceModel,"test").current_status=source_state
            db.commit()
        assert not observe(factory,40)
        assert events(client,session)==[]


@pytest.mark.parametrize("source_gap, expected", [(60, True), (60.1, True), (65, True), (65.001, False), (120, False)])
def test_source_window_continuity_with_receipt_jitter(setup, monkeypatch, source_gap, expected):
    client, factory = setup
    start = datetime.now(timezone.utc)
    class Clock(datetime):
        current = start + timedelta(milliseconds=100)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(alerts, "datetime", Clock)
    with client.websocket_connect("/api/alerts/session") as ws:
        session = ws.receive_json()["session_id"]
        rule(client, session)
        assert not observe(factory, 10, changes={"timestamp":start})
        # Arrival includes processing jitter; source time determines continuity.
        Clock.current = start + timedelta(seconds=source_gap, milliseconds=200)
        assert observe(factory, 40, changes={"timestamp":start + timedelta(seconds=source_gap)}) is expected
        assert len(events(client, session)) == int(expected)


def test_invalid_window_requires_new_baseline_after_recovery(setup):
    client, factory = setup
    with client.websocket_connect("/api/alerts/session") as ws:
        session = ws.receive_json()["session_id"]
        rule(client, session)
        assert not observe(factory, 10)
        assert not observe(factory, 0, changes={"validity":"invalid", "source_status":"recovering"})
        assert not observe(factory, 40)
        assert not observe(factory, 10)
        assert observe(factory, 40)
