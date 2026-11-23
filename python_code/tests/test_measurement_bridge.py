"""Replay bridge tests."""

import tempfile
import unittest
from pathlib import Path

from measurement import MeasurementError
from measurement_bridge import run_replay, transcode_y4m_to_mp4


def write_clip(path, frames=61, width=64, height=64):
    header = f"YUV4MPEG2 W{width} H{height} F1:1 Ip C420jpeg\n".encode()
    payload = bytes([20] * (width * height) + [128] * (width * height // 2))
    with open(path, "wb") as stream:
        stream.write(header)
        for _ in range(frames):
            stream.write(b"FRAME\n" + payload)


class CrossingDetector:
    def __init__(self):
        self.frames = 0

    def boxes(self, frame):
        self.frames += 1
        return [(0, 0, 12, 12)] if self.frames == 1 else [(0, 30, 12, 42)]


class Response:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, registered_camera="cam/test"):
        self.calls = []
        self.registered_camera = registered_camera

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, None))
        return Response([{"id": "cam/test", "camera_code": "TEST"}])

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/upload-replay"):
            return Response({"camera_id": self.registered_camera, "mode": "replay_not_live"})
        return Response({"status": "success", "alert_triggered": False}, 201)


class BridgeTests(unittest.TestCase):
    def test_registers_replay_before_sending_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.y4m"
            write_clip(clip)
            session = FakeSession()

            def converter(source, destination):
                Path(destination).write_bytes(b"mp4 preview")

            reports = run_replay(clip, "TEST", "2020-01-01T00:00:00Z",
                                 session=session, detector=CrossingDetector(),
                                 converter=converter, line_y=22)
        calls = session.calls
        self.assertEqual([call[0] for call in calls], ["GET", "POST", "POST"])
        self.assertTrue(calls[1][1].endswith("/upload-replay"))
        self.assertTrue(calls[2][1].endswith("/measurements"))
        self.assertEqual(reports[0]["vehicle_count"], 1)
        self.assertEqual(calls[2][2]["json"]["timestamp"], "2020-01-01T00:01:00+00:00")
        self.assertTrue(calls[2][2]["json"]["is_replay"])

    def test_bad_y4m_never_changes_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "bad.y4m"
            clip.write_bytes(b"not a Y4M clip")
            session = FakeSession()
            with self.assertRaises(MeasurementError):
                run_replay(clip, "TEST", "2020-01-01T00:00:00Z", session=session)
            self.assertEqual(session.calls, [])

    def test_registration_must_match_selected_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.y4m"
            write_clip(clip, frames=1)
            session = FakeSession(registered_camera="other")

            def converter(source, destination):
                Path(destination).write_bytes(b"mp4 preview")

            with self.assertRaisesRegex(MeasurementError, "camera"):
                run_replay(clip, "TEST", "2020-01-01T00:00:00Z",
                           session=session, converter=converter)
            self.assertEqual(len(session.calls), 2)

    def test_transcoded_preview_is_readable_mp4(self):
        import cv2
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.y4m"
            preview = Path(directory) / "preview.mp4"
            write_clip(clip, frames=3)
            transcode_y4m_to_mp4(clip, preview)
            capture = cv2.VideoCapture(str(preview))
            try:
                self.assertTrue(capture.isOpened())
                self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), 3)
                fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
                codec = "".join(chr((fourcc >> (8 * shift)) & 255) for shift in range(4)).lower()
                self.assertIn(codec, {"avc1", "h264"})
                ok, frame = capture.read()
                self.assertTrue(ok)
                self.assertEqual(frame.shape[:2], (64, 64))
            finally:
                capture.release()


if __name__ == "__main__":
    unittest.main()
