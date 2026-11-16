import cv2
import time
import logging
import warnings
import numpy as np
import torch
from typing import Tuple, List, Optional
import supervision as sv

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*loss_type=None.*")

for logger_name in ["rfdetr", "rfdetr.models", "rf_detr"]:
    l = logging.getLogger(logger_name)
    l.setLevel(logging.ERROR)
    l.propagate = False

logger = logging.getLogger(__name__)

# COCO IDs: Car (3), Motorcycle (4), Bus (6), Truck (8)
COCO_VEHICLE_CLASS_IDS = {3, 4, 6, 8}

class DirectVehicleTracker:
    """
    Traffic-camera optimized vehicle tracker featuring:
    - 2-stage IoU and adaptive centroid distance association
    - Dynamic search distance expansion during lost frames to re-link flickering detections
    - Exponential moving average bounding-box smoothing to halt visual jitter
    - Minimum box area gating and hit-confirmation to reject horizon noise and ID flipping
    """
    def __init__(self, iou_threshold: float = 0.25, max_dist: float = 75.0, max_age: int = 60):
        self.iou_threshold = iou_threshold
        self.max_dist = max_dist
        self.max_age = max_age
        self.min_hits = 2  # NEW: Requires 2 consecutive hits before showing a permanent ID
        self.next_id = 1
        self.tracks = {}

    def reset(self):
        self.tracks.clear()
        self.next_id = 1

    def update_with_detections(self, detections: sv.Detections) -> sv.Detections:
        for tid in self.tracks:
            self.tracks[tid]["lost_count"] += 1

        if len(detections) == 0:
            self.tracks = {tid: tr for tid, tr in self.tracks.items() if tr["lost_count"] <= self.max_age}
            detections.tracker_id = np.array([], dtype=int)
            return detections

        boxes = detections.xyxy
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

            # 1. Primary IoU matching pass
            ious = self._compute_iou_batch(boxes, prev_boxes)
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

            # 2. Secondary Centroid distance pass with controlled expansion for lost tracks
            for d_idx in range(len(boxes)):
                if assigned_ids[d_idx] != -1:
                    continue
                c = centroids[d_idx]
                best_tid = None
                best_dist = float("inf")
                for p_idx, tid in enumerate(prev_ids):
                    if tid in matched_prev:
                        continue
                    lost = self.tracks[tid]["lost_count"]
                    capped_lost = min(lost, 10) 
                    allowed_dist = self.max_dist + (3.0 * capped_lost)
                    dist = float(np.linalg.norm(c - prev_centroids[p_idx]))
                    if dist < allowed_dist and dist < best_dist:
                        best_dist = dist
                        best_tid = tid

                if best_tid is not None:
                    assigned_ids[d_idx] = best_tid
                    matched_prev.add(best_tid)

        final_tracker_ids = []
        for i in range(len(boxes)):
            box_w = boxes[i][2] - boxes[i][0]
            box_h = boxes[i][3] - boxes[i][1]
            if box_w * box_h < 500:
                final_tracker_ids.append(-1)
                continue

            if assigned_ids[i] == -1:
                tid = self.next_id
                self.next_id += 1
                smoothed_box = boxes[i]
                hits = 1
            else:
                tid = assigned_ids[i]
                prev = self.tracks[tid]
                hits = prev.get("hits", 1) + 1
                # Smooth coordinates (EMA) to prevent box jumping/flicker
                smoothed_box = 0.80 * boxes[i] + 0.20 * prev["box"]

            self.tracks[tid] = {
                "box": smoothed_box,
                "centroid": centroids[i],
                "lost_count": 0,
                "hits": hits
            }
            
            # Gating: Only display IDs for tracks that have persisted long enough
            if hits >= self.min_hits:
                final_tracker_ids.append(tid)
            else:
                final_tracker_ids.append(-1)

        self.tracks = {tid: tr for tid, tr in self.tracks.items() if tr["lost_count"] <= self.max_age}
        detections.tracker_id = np.array(final_tracker_ids, dtype=int)
        return detections

    @staticmethod
    def _compute_iou_batch(boxes1: np.ndarray, boxes2: np.ndarray) -> np.ndarray:
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
    def __init__(self, confidence_threshold: float = 0.30, device: Optional[str] = None):
        self.confidence_threshold = confidence_threshold
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.model = None
        self.predictor = None
        self.tracker = DirectVehicleTracker()

        self._load_rfdetr()

    def _load_rfdetr(self):
        try:
            from rfdetr import RFDETRNano
            logger.info("Initializing RF-DETR Nano model...")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.model = RFDETRNano()
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
            logger.warning(f"Could not initialize RF-DETR ({e}). Using lightweight detector fallback.")
            self.model = None
            self.predictor = None

    def process_frame(self, frame_bgr: np.ndarray) -> Tuple[np.ndarray, int, sv.Detections]:
        h, w, _ = frame_bgr.shape
        engine = self.predictor or self.model

        if engine is not None:
            try:
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                with torch.inference_mode():
                    raw_detections = engine.predict(frame_rgb, threshold=self.confidence_threshold)

                mask = []
                for cid in raw_detections.class_id:
                    mask.append(int(cid) in COCO_VEHICLE_CLASS_IDS)
                mask = np.array(mask, dtype=bool) if len(mask) > 0 else np.array([], dtype=bool)

                if len(mask) > 0 and np.any(mask):
                    detections = raw_detections[mask]
                else:
                    detections = sv.Detections.empty()
            except Exception as e:
                detections = sv.Detections.empty()
        else:
            detections = self._fallback_motion_detect(frame_bgr)

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

    def _fallback_motion_detect(self, frame_bgr: np.ndarray) -> sv.Detections:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        edges = cv2.Canny(blurred, 60, 160)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes = []
        for c in contours:
            area = cv2.contourArea(c)
            # Expanded area and aspect ratio to capture buses and commercial vehicles
            if 600 < area < 160000:
                x, y, w, h = cv2.boundingRect(c)
                aspect_ratio = float(w) / max(h, 1)
                if 0.35 < aspect_ratio < 5.5:
                    boxes.append([x, y, x + w, y + h])

        if not boxes:
            return sv.Detections.empty()

        xyxy = np.array(boxes, dtype=np.float32)
        confidence = np.full(len(boxes), 0.70, dtype=np.float32)
        class_id = np.full(len(boxes), 2, dtype=int)
        return sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)

    def reset(self):
        self.tracker.reset()
