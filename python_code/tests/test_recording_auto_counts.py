"""Offline RF-DETR report generation against the repository's real window logic."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from recording_auto_counts import RecordingCountError, count_recording
from tests.test_measurement_bridge import write_clip
from measurement_bridge import transcode_y4m_to_mp4


REPO = Path(os.environ.get("PRIVATE_TRAFFIC_REPO", Path(__file__).resolve().parents[2]))


class StableDetector:
    def process_frame(self, image):
        return image, 1, SimpleNamespace(tracker_id=[7])


@unittest.skipUnless((REPO / "run_detector_worker.py").is_file(),
                     "Set PRIVATE_TRAFFIC_REPO to the cloned repository")
class RecordingAutoCountTests(unittest.TestCase):
    def test_real_mp4_decoder_and_source_window_write_one_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.y4m"
            video = Path(directory) / "source.mp4"
            reports_path = Path(directory) / "reports.jsonl"
            write_clip(source, frames=61)
            transcode_y4m_to_mp4(source, video)
            reports = count_recording(video, "cam/test", "2020-01-01T00:00:00Z",
                                      REPO, reports_path, detector=StableDetector())
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["timestamp"], "2020-01-01T00:01:00+00:00")
            self.assertEqual(reports[0]["vehicle_count"], 1)
            self.assertEqual(reports[0]["source_status"], "replay_not_live")
            self.assertTrue(reports[0]["is_replay"])
            self.assertEqual(json.loads(reports_path.read_text(encoding="utf-8").strip()), reports[0])

    def test_rejects_naive_recording_time_before_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RecordingCountError, "recording_time"):
                count_recording(Path(directory) / "unused.mp4", "cam/test", "2020-01-01T00:00:00",
                                REPO, Path(directory) / "reports.jsonl", detector=StableDetector())

    def test_short_recording_never_writes_a_valid_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "short.y4m"
            video = Path(directory) / "short.mp4"
            reports_path = Path(directory) / "reports.jsonl"
            write_clip(source, frames=3)
            transcode_y4m_to_mp4(source, video)
            with self.assertRaisesRegex(RecordingCountError, "no complete"):
                count_recording(video, "cam/test", "2020-01-01T00:00:00Z",
                                REPO, reports_path, detector=StableDetector())
            self.assertFalse(reports_path.exists())


if __name__ == "__main__":
    unittest.main()
