"""Regression tests for the product RF-DETR vehicle tracker."""

import numpy as np
from datetime import datetime, timedelta, timezone

from app.detector import DirectVehicleTracker
from run_detector_worker import SourceWindow


class DetectionBatch:
    """The tracker uses only boxes and its writable tracker-id output."""

    def __init__(self, boxes):
        self.xyxy = np.asarray(boxes, dtype=np.float32).reshape((-1, 4))
        self.tracker_id = None

    def __len__(self):
        return len(self.xyxy)


def test_discarded_small_detection_cannot_steal_existing_vehicle_id():
    tracker = DirectVehicleTracker()
    first = tracker.update_with_detections(DetectionBatch([[0, 0, 30, 30]]))
    assert first.tracker_id.tolist() == [1]

    # The small false detection overlaps the old box more than the moving car.
    second = tracker.update_with_detections(DetectionBatch([
        [5, 5, 25, 25],    # area 400: discarded by the tracker
        [20, 0, 50, 30],   # area 900: same car, displaced 20 px
    ]))
    assert second.tracker_id.tolist() == [-1, 1]


def test_adaptive_tracker_confirms_once_and_reconnects_across_windows():
    tracker = DirectVehicleTracker(0.25, 75, 60, adaptive=True)
    window = SourceWindow()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    results = []
    for second in range(121):
        boxes = [[400, 0, 430, 30]] if second == 1 else []
        if second == 58:
            boxes = [[0, 0, 30, 30]]
        elif second >= 60:
            boxes = [[80, 0, 110, 30], [81, 1, 101, 21]]
        ids = tracker.update_with_detections(DetectionBatch(boxes)).tracker_id
        if second in (1, 58):
            assert ids.tolist() == [-1]
        elif second >= 60:
            assert ids.tolist() == [2, -1]
        result = window.add(start + timedelta(seconds=second), ids)
        if result:
            results.append(result["vehicle_count"])
    assert results == [1, 1]
