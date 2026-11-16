"""C8351 provisional thresholds affect only the selected camera."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import Base
from app.models import CameraSourceModel
from app import camera_service
from app.health_monitor import calibration_valid, traffic_level_for
from apply_camera_calibration import CalibrationUpdateError, apply_camera_calibration


def test_new_camera_database_seeds_c8351_only(tmp_path, monkeypatch):
    features = []
    for code, uri in (("C8351", "camera/507269"), ("C631", "camera/other")):
        features.append({
            "uri": uri, "title": f"Road: {code}",
            "features": [{"geometry": {"coordinates": [-93.0, 45.0]}}],
            "views": [{"url": f"https://example.test/cameras/MN/{code}?x=1"}],
        })
    source = tmp_path / "sources.json"
    source.write_text(json.dumps({"data": {"mapFeaturesQuery": {"mapFeatures": features}}}), encoding="utf-8")
    monkeypatch.setattr(camera_service, "JSON_511MN_PATH", str(source))
    monkeypatch.setattr(camera_service, "PILOT_CAMERAS_PATH", str(ROOT / "pilot_cameras.json"))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            assert camera_service.initialize_camera_database(db) == 2
            selected = db.get(CameraSourceModel, "camera/507269")
            other = db.get(CameraSourceModel, "camera/other")
            assert (selected.calibration_t1, selected.calibration_t2) == (33, 142)
            assert "provisional" in selected.calibration_note.lower()
            assert calibration_valid(selected)
            assert [traffic_level_for(selected, count) for count in (28, 37, 247)] == ["clear", "normal", "congested"]
            assert other.calibration_t1 is None and other.calibration_t2 is None
    finally:
        engine.dispose()


def make_existing_db(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE camera_sources (id TEXT, camera_code TEXT, calibration_t1 REAL, calibration_t2 REAL, calibration_note TEXT)")
        connection.executemany("INSERT INTO camera_sources VALUES (?,?,?,?,?)", [
            ("camera/507269", "C8351", 20, 45, "previous calibration"),
            ("camera/other", "C631", 11, 25, "other road"),
        ])


def test_existing_database_updates_only_c8351(tmp_path):
    database = tmp_path / "traffic.db"
    make_existing_db(database)
    result = apply_camera_calibration(database, ROOT / "pilot_cameras.json", "C8351")
    assert result["camera_id"] == "camera/507269"
    assert (result["t1"], result["t2"]) == (33, 142)
    with sqlite3.connect(database) as connection:
        chosen, other = connection.execute("SELECT calibration_t1, calibration_t2, calibration_note FROM camera_sources ORDER BY id").fetchall()
    assert chosen[:2] == (33, 142) and "provisional" in chosen[2].lower()
    assert other == (11, 25, "other road")
    assert apply_camera_calibration(database, ROOT / "pilot_cameras.json", "C8351")["t1"] == 33


def test_missing_database_fails_without_creating_one(tmp_path):
    database = tmp_path / "missing.db"
    with pytest.raises(CalibrationUpdateError, match="database"):
        apply_camera_calibration(database, ROOT / "pilot_cameras.json", "C8351")
    assert not database.exists()
