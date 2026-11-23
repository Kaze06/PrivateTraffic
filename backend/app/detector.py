from __future__ import annotations
import time
import logging
import warnings
from typing import Tuple, List, Optional

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*loss_type=None.*")

for logger_name in ["rfdetr", "rfdetr.models", "rf_detr"]:
    l = logging.getLogger(logger_name)
    l.setLevel(logging.ERROR)
    l.propagate = False

logger = logging.getLogger(__name__)

# RF-DETR 1.12.1 preserves COCO category IDs (not contiguous YOLO indices).
COCO_VEHICLE_CLASS_IDS = {3, 4, 6, 8}


class InferenceUnavailable(RuntimeError):
    """No valid vehicle observation was produced."""


def predict_vehicles(engine, frame_rgb, threshold):
    if engine is None:
        raise InferenceUnavailable("vehicle_model_unavailable")
    try:
        detections = engine.predict(frame_rgb, threshold=threshold)
        if detections.class_id is None:
            raise ValueError("Missing category IDs")
        return detections[[int(cid) in COCO_VEHICLE_CLASS_IDS for cid in detections.class_id]]
    except Exception as exc:
        raise InferenceUnavailable("vehicle_inference_failed") from exc


class DirectVehicleTracker:
    def __init__(self, iou_threshold: float = 0.20, max_dist: float = 50.0, max_age: int = 45,
                 *, adaptive: bool = False):
        self.iou_threshold = iou_threshold
        self.max_dist = max_dist
        self.max_age = max_age
        self.adaptive = adaptive
        self.next_id = 1
        self.tracks = {}

    def reset(self):
        self.tracks.clear()
        self.next_id = 1

    def update_with_detections(self, detections: sv.Detections) -> sv.Detections:
        import numpy as np
        for tid in self.tracks:
            self.tracks[tid]["lost_count"] += 1

        if len(detections) == 0:
            self.tracks = {tid: tr for tid, tr in self.tracks.items() if tr["lost_count"] <= self.max_age}
            detections.tracker_id = np.array([], dtype=int)
            return detections

        boxes = detections.xyxy
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        eligible = areas >= 500
        centroids = np.column_stack([
            (boxes[:, 0] + boxes[:, 2]) / 2.0,
            (boxes[:, 1] + boxes[:, 3]) / 2.0
        ])

        assigned_ids = [-1] * len(boxes)
        matched_prev = set()

        if self.tracks:
            prev_ids = list(self.tracks.keys())
            prev_boxes = np.array([self.tracks[tid]["box"] for tid in prev_ids], dtype=np.float32)
            prev_centroids = np.array([self.tracks[tid]["centroid"] for tid in prev_ids], dtype=np.float32)

            ious = self._compute_iou_batch(boxes, prev_boxes)
            ious[~eligible, :] = -1
            for _ in range(min(len(boxes), len(prev_ids))):
                max_idx = np.unravel_index(np.argmax(ious), ious.shape)
                d_idx, p_idx = max_idx
                if ious[d_idx, p_idx] < self.iou_threshold:
                    break
                tid = prev_ids[p_idx]
                if tid not in matched_prev and assigned_ids[d_idx] == -1:
                    assigned_ids[d_idx] = tid
                    matched_prev.add(tid)
                ious[d_idx, :] = -1
                ious[:, p_idx] = -1

            for d_idx in range(len(boxes)):
                if not eligible[d_idx] or assigned_ids[d_idx] != -1:
                    continue
                c = centroids[d_idx]
                best_tid = None
                best_dist = float("inf")
                for p_idx, tid in enumerate(prev_ids):
                    if tid in matched_prev:
                        continue
                    dist = float(np.linalg.norm(c - prev_centroids[p_idx]))
                    limit = self.max_dist
                    if self.adaptive:
                        limit += 3.0 * min(self.tracks[tid]["lost_count"], 10)
                    if dist < limit and dist < best_dist:
                        best_dist = dist
                        best_tid = tid

                if best_tid is not None:
                    assigned_ids[d_idx] = best_tid
                    matched_prev.add(best_tid)

        confirmed_ids = [-1] * len(boxes)
        for i in range(len(boxes)):
            if not eligible[i]:
                assigned_ids[i] = -1
                continue

            if assigned_ids[i] == -1:
                tid = self.next_id
                self.next_id += 1
                assigned_ids[i] = tid
            else:
                tid = assigned_ids[i]

            previous = self.tracks.get(tid)
            hits = previous["hits"] + 1 if previous is not None else 1
            box = boxes[i]
            if self.adaptive and previous is not None:
                box = 0.80 * box + 0.20 * previous["box"]
            self.tracks[tid] = {
                "box": box,
                "centroid": centroids[i],
                "lost_count": 0,
                "hits": hits,
            }
            # An adaptive track may confirm after a short detection gap.
            if not self.adaptive or hits >= 2:
                confirmed_ids[i] = tid

        self.tracks = {tid: tr for tid, tr in self.tracks.items() if tr["lost_count"] <= self.max_age}
        detections.tracker_id = np.array(confirmed_ids, dtype=int)
        return detections

    @staticmethod
    def _compute_iou_batch(boxes1: np.ndarray, boxes2: np.ndarray) -> np.ndarray:
        import numpy as np
        x1 = np.maximum(boxes1[:, None, 0], boxes2[None, :, 0])
        y1 = np.maximum(boxes1[:, None, 1], boxes2[None, :, 1])
        x2 = np.minimum(boxes1[:, None, 2], boxes2[None, :, 2])
        y2 = np.minimum(boxes1[:, None, 3], boxes2[None, :, 3])
        inter_area = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        area1 = (boxes1[:, 2] - boxes1[:, 0]) * (boxes1[:, 3] - boxes1[:, 1])
        area2 = (boxes2[:, 2] - boxes2[:, 0]) * (boxes2[:, 3] - boxes2[:, 1])
        union_area = area1[:, None] + area2[None, :] - inter_area
        return inter_area / np.maximum(union_area, 1e-6)

class VehicleDetector:
    def __init__(self, confidence_threshold: float = 0.30, device: Optional[str] = None,
                 *, adaptive_tracking: bool = False):
        import torch
        self.confidence_threshold = confidence_threshold
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.model = None
        self.predictor = None
        self.tracker = (DirectVehicleTracker(0.25, 75.0, 60, adaptive=True)
                        if adaptive_tracking else DirectVehicleTracker())

        self._load_rfdetr()

    def _load_rfdetr(self):
        try:
            from rfdetr import RFDETRNano
            logger.info("Initializing RF-DETR Nano model...")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.model = RFDETRNano(device=self.device)
                if hasattr(self.model, "inference"):
                    try:
                        self.predictor = self.model.inference()
                    except Exception:
                        self.predictor = self.model
                else:
                    self.predictor = self.model

                if hasattr(self.model, "model") and hasattr(self.model.model, "eval"):
                    self.model.model.eval()

            logger.info(f"RF-DETR successfully loaded on {self.device}.")
        except Exception as e:
            logger.warning(f"Could not initialize RF-DETR ({e}). Vehicle measurements are unavailable.")
            self.model = None
            self.predictor = None

    def process_frame(self, frame_bgr: np.ndarray) -> Tuple[np.ndarray, int, sv.Detections]:
        import cv2
        import torch
        if frame_bgr is None or len(frame_bgr.shape) != 3 or frame_bgr.size == 0:
            raise InferenceUnavailable("invalid_frame")
        engine = self.predictor or self.model
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        with torch.inference_mode():
            detections = predict_vehicles(engine, frame_rgb, self.confidence_threshold)

        tracked = self.tracker.update_with_detections(detections)

        annotated = frame_bgr.copy()
        active_count = 0

        if len(tracked) > 0 and tracked.tracker_id is not None:
            for box, tid, conf in zip(tracked.xyxy, tracked.tracker_id, tracked.confidence):
                if int(tid) > 0:
                    active_count += 1
                    x1, y1, x2, y2 = [int(v) for v in box]
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (34, 197, 94), 2)
                    label = f"ID {tid} ({conf:.2f})"
                    
                    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                    cv2.rectangle(annotated, (x1, max(y1 - 18, 0)), (x1 + tw + 6, max(y1, 18)), (15, 23, 42), -1)
                    cv2.putText(annotated, label, (x1 + 3, max(y1 - 4, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

        return annotated, active_count, tracked

    def reset(self):
        self.tracker.reset()
