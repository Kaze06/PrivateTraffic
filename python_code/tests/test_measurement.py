import io
import tempfile
import unittest
from pathlib import Path

from measurement import Y4MReader, MeasurementWindow, LineCounter, MeasurementError, measure


def clip(times=None, frames=3):
    header = b"YUV4MPEG2 W4 H4 F1:1 Ip C420jpeg\n"
    body = b""
    for i in range(frames):
        tag = f" XPTS={times[i]}" if times is not None else ""
        body += f"FRAME{tag}\n".encode() + bytes([16 + i] * 16 + [128] * 8)
    return io.BytesIO(header + body)


class ReaderTests(unittest.TestCase):
    def test_reads_y4m_frames_and_source_times(self):
        frames = list(Y4MReader(clip(frames=2)))
        self.assertEqual([f.seconds for f in frames], [0.0, 1.0])
        self.assertEqual(len(frames[0].yuv420), 24)

    def test_rejects_nonincreasing_timestamp(self):
        with self.assertRaisesRegex(MeasurementError, "timestamp"):
            list(Y4MReader(clip(times=[0, 1, 1])))

    def test_rejects_truncated_frame(self):
        data = clip(frames=1).getvalue()[:-1]
        with self.assertRaisesRegex(MeasurementError, "frame size"):
            list(Y4MReader(io.BytesIO(data)))


class CountingTests(unittest.TestCase):
    def test_line_crossing_counts_each_track_once(self):
        counter = LineCounter(line_y=10, max_distance=20)
        for boxes in [[(0, 0, 4, 4)], [(0, 9, 4, 13)], [(0, 12, 4, 16)], [(0, 8, 4, 12)]]:
            counter.update(boxes)
        self.assertEqual(counter.total, 1)

    def test_full_window_and_short_tail(self):
        window = MeasurementWindow(camera_id="cam", recording_time="2026-11-01T00:00:00+00:00")
        for second in range(60):
            self.assertIsNone(window.add(second, 0))
        report = window.add(60, 2)
        self.assertEqual(report["vehicle_count"], 2)
        self.assertEqual(report["flow_per_minute"], 2.0)
        self.assertEqual(report["validity"], "valid")
        window.add(61, 2)
        self.assertEqual(window.finish()["no_measurement_reason"], "insufficient_duration")

    def test_gap_invalidates_window(self):
        window = MeasurementWindow("cam", "2026-11-01T00:00:00Z")
        window.add(0, 0)
        with self.assertRaisesRegex(MeasurementError, "timestamp"):
            window.add(7, 0)

    def test_single_frame_is_insufficient_duration(self):
        window = MeasurementWindow("cam", "2026-11-01T00:00:00Z")
        window.add(0, 0)
        self.assertEqual(window.finish()["no_measurement_reason"], "insufficient_duration")

    def test_exact_full_window_has_no_invalid_tail(self):
        window = MeasurementWindow("cam", "2026-11-01T00:00:00Z")
        for second in range(61):
            report = window.add(second, 0)
        self.assertEqual(report["validity"], "valid")
        self.assertIsNone(window.finish())

    def test_y4m_to_backend_json(self):
        class Detector:
            index = 0

            def boxes(self, frame):
                self.index += 1
                return [(0, 0, 2, 2)] if self.index == 1 else [(0, 2, 2, 4)]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.y4m"
            path.write_bytes(clip(frames=61).getvalue())
            reports = list(measure(path, "cam", "2026-11-01T00:00:00Z", line_y=2, detector=Detector()))
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["vehicle_count"], 1)
        self.assertEqual(reports[0]["timestamp"], "2026-11-01T00:01:00+00:00")
        self.assertTrue(reports[0]["is_replay"])


if __name__ == "__main__":
    unittest.main()
