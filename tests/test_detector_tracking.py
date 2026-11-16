"""Regression tests for the product RF-DETR vehicle tracker."""

import numpy as np

from app.detector import DirectVehicleTracker


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
