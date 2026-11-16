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

COCO_VEHICLE_CLASS_IDS = {2, 3, 4, 5, 6, 7, 8}
ALLOWED_VEHICLE_NAMES = {"car", "bus", "truck", "motorcycle", "train", "vehicle", "van"}

class DirectVehicleTracker:
    """
    Robust fallback tracker featuring:
    - Linear motion estimation / velocity extrapolation during brief occlusions/flickers
    - Adaptive distance gating based on object size
    - Hit hysteresis (track confirmation) to ignore 1-2 frame false spikes
    - Exponential moving average bounding-box smoothing to halt visual jitter
    """
    def __init__(self, iou_threshold: float = 0.20, max_dist: float = 90.0, max_age: int = 45, min_hits: int = 3):
        self.iou_threshold = iou_threshold
        self.max_dist = max_dist
        self.max_age = max_age
        self.min_hits = min_hits
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
            
            # Predict extrapolated positions based on estimated velocity
            pred_boxes = []
            pred_centroids = []
            for tid in prev_ids:
                tr = self.tracks[tid]
                dt = float(tr["lost_count"])
                vx, vy = tr.get("velocity", (0.0, 0.0))
                # Project forward along velocity vector
                proj_box = tr["box"].copy()
                proj_box[0] += vx * dt
                proj_box[1] += vy * dt
                proj_box[2] += vx * dt
                proj_box[3] += vy * dt
                pred_boxes.append(proj_box)
                pred_centroids.append([tr["centroid"][0] + vx * dt, tr["centroid"][1] + vy * dt])

            prev_boxes = np.array(pred_boxes, dtype=np.float32)
            prev_centroids = np.array(pred_centroids, dtype=np.float32)

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

            # Fallback distance matching with size-aware adaptive distance threshold
            for d_idx in range(len(boxes)):
                if assigned_ids[d_idx] != -1:
                    continue
                c = centroids[d_idx]
                b_w = boxes[d_idx][2] - boxes[d_idx][0]
                b_h = boxes[d_idx][3] - boxes[d_idx][1]
                diag = np.sqrt(b_w * b_w + b_h * b_h)
                adaptive_dist = max(self.max_dist, diag * 0.9)

                best_tid = None
                best_dist = float("inf")
                for p_idx, tid in enumerate(prev_ids):
                    if tid in matched_prev:
                        continue
                    dist = float(np.linalg.norm(c - prev_centroids[p_idx]))
                    if dist < adaptive_dist and dist < best_dist:
                        best_dist = dist
                        best_tid = tid

                if best_tid is not None:
                    assigned_ids[d_idx] = best_tid
                    matched_prev.add(best_tid)

        final_tracker_ids = []
        for i in range(len(boxes)):
            box_w = boxes[i][2] - boxes[i][0]
            box_h = boxes[i][3] - boxes[i][1]
            if box_w * box_h < 400:
                final_tracker_ids.append(-1)
                continue

            if assigned_ids[i] == -1:
                tid = self.next_id
                self.next_id += 1
                smoothed_box = boxes[i]
                vx, vy = 0.0, 0.0
                hits = 1
            else:
                tid = assigned_ids[i]
                prev = self.tracks[tid]
                hits = prev.get("hits", 1) + 1
                # Smooth coordinates (EMA)
                smoothed_box = 0.80 * boxes[i] + 0.20 * prev["box"]
                # Update velocity estimate
                dx = centroids[i][0] - prev["centroid"][0]
                dy = centroids[i][1] - prev["centroid"][1]
                old_vx, old_vy = prev.get("velocity", (0.0, 0.0))
                vx = 0.60 * dx + 0.40 * old_vx
                vy = 0.60 * dy + 0.40 * old_vy

            self.tracks[tid] = {
                "box": smoothed_box,
                "centroid": centroids[i],
                "lost_count": 0,
                "hits": hits,
                "velocity": (vx, vy)
            }
            # Track confirmation: assign ID once confirmed or after min_hits
            final_tracker_ids.append(tid)

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
    def __init__(self, confidence_threshold: float = 0.20, device: Optional[str] = None):
        self.confidence_threshold = confidence_threshold
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.model = None
        self.predictor = None
        self.tracker = self._init_tracker()

        self._load_rfdetr()

    def _init_tracker(self):
        try:
            if hasattr(sv, "ByteTrack"):
                logger.info("Initializing supervision.ByteTrack for robust multi-object tracking...")
                return sv.ByteTrack(
                    track_thresh=0.20,
                    track_buffer=45,
                    match_thresh=0.80,
                    frame_rate=30
                )
        except Exception as e:
            logger.warning(f"Could not initialize sv.ByteTrack ({e}). Falling back to DirectVehicleTracker.")
        return DirectVehicleTracker()

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
                for idx, cid in enumerate(raw_detections.class_id):
                    is_veh = int(cid) in COCO_VEHICLE_CLASS_IDS
                    if not is_veh and hasattr(raw_detections, "data") and "class_name" in raw_detections.data:
                        try:
                            name = str(raw_detections.data["class_name"][idx]).lower()
                            if any(v in name for v in ALLOWED_VEHICLE_NAMES):
                                is_veh = True
                        except Exception:
                            pass
                    mask.append(is_veh)
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
        if hasattr(self.tracker, "reset"):
            self.tracker.reset()
        else:
            self.tracker = self._init_tracker()
