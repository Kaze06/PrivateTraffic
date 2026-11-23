"""Exercise the Y4M bridge against PrivateTraffic's real API routes."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from measurement_bridge import run_replay
from tests.test_measurement_bridge import CrossingDetector, write_clip


repo = Path(os.environ.get("PRIVATE_TRAFFIC_REPO", Path(__file__).resolve().parents[2]))
backend = repo / "backend"
if backend.is_dir():
    sys.path.insert(0, str(backend))


@unittest.skipUnless(backend.is_dir(), "Set PRIVATE_TRAFFIC_REPO to the cloned repository")
class BackendIntegrationTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.database import Base, get_db
        from app.main import app
        from app.models import CameraSourceModel
        from app.routers import cameras

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.addCleanup(lambda: Base.metadata.drop_all(self.engine))
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(CameraSourceModel(id="cam/test", camera_code="TEST", title="Test Camera",
                                     road_name="Test Road", latitude=0, longitude=0,
                                     stream_url="https://example.test/live.m3u8", current_status="live"))
            db.commit()

        def override_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        self.addCleanup(app.dependency_overrides.clear)
        upload_patch = patch.object(cameras, "UPLOAD_DIR", self.temp.name)
        upload_patch.start()
        self.addCleanup(upload_patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_valid_y4m_window_is_saved_as_replay_without_alert(self):
        from app.models import TrafficMeasurementModel, AlertEventModel

        clip = Path(self.temp.name) / "source.y4m"
        write_clip(clip)
        reports = run_replay(clip, "TEST", "2020-01-01T00:00:00Z",
                             session=self.client, detector=CrossingDetector(), line_y=22)
        self.assertEqual(len(reports), 1)
        with self.sessions() as db:
            rows = db.query(TrafficMeasurementModel).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].vehicle_count, 1)
            self.assertEqual(rows[0].validity, "valid")
            self.assertTrue(rows[0].is_replay)
            self.assertEqual(db.query(AlertEventModel).count(), 0)
        status = self.client.get("/api/cameras/cam/test/status").json()
        self.assertEqual(status["status"], "replay_not_live")
        self.assertTrue(status["is_replay"])
        self.assertEqual(status["vehicle_count"], 1)

    def test_short_clip_is_invalid_and_not_current_traffic(self):
        from app.models import TrafficMeasurementModel, AlertEventModel

        clip = Path(self.temp.name) / "short.y4m"
        write_clip(clip, frames=2)
        reports = run_replay(clip, "TEST", "2020-01-01T00:00:00Z",
                             session=self.client, detector=CrossingDetector(), line_y=22)
        self.assertEqual(reports[0]["no_measurement_reason"], "insufficient_duration")
        with self.sessions() as db:
            row = db.query(TrafficMeasurementModel).one()
            self.assertEqual(row.validity, "invalid")
            self.assertEqual(db.query(AlertEventModel).count(), 0)
        status = self.client.get("/api/cameras/cam/test/status").json()
        self.assertEqual(status["status"], "replay_not_live")
        self.assertEqual(status["traffic_level"], "unable_to_determine")


if __name__ == "__main__":
    unittest.main()
