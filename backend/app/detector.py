import cv2
import time
import logging
import warnings
import numpy as np
import torch
from typing import Tuple, List, Optional
import supervision as sv

# Suppress PyTorch JIT deprecation and tracer warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*loss_type=None.*")

# Filter out the specific rf-detr warning from ALL loggers (root and subloggers)
class RFDetrWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        return "Model is not optimized for inference" not in msg

logging.getLogger().addFilter(RFDetrWarningFilter())
for name in ["rfdetr", "rfdetr.models", "rf_detr"]:
    logging.getLogger(name).addFilter(RFDetrWarningFilter())

logger = logging.getLogger(__name__)

COCO_VEHICLE_CLASS_IDS = {2, 3, 5, 7}  # car, motorcycle, bus, truck

class RobustVehicleTracker:
    def __init__(self, max_lost_frames: int = 45, iou_threshold: float = 0.20, max_centroid_dist: float = 85.0):
        self.max_lost_frames = max_lost_frames
        self.iou_threshold = iou_threshold
        self.max_centroid_dist = max_centroid_dist
        self.next_id = 1
        self.tracks = {}

    def update_with_detections(self, detections: sv.Detections) -> sv.Detections:
        for tid in list(self.tracks.keys()):
            self.tracks[tid]["lost"] += 1
            if self.tracks[tid]["lost"] > self.max_lost_frames:
                del self.tracks[tid]

        if len(detections) == 0:
            detections.tracker_id = np.array([], dtype=int)
            return detections

        boxes = detections.xyxy
        centroids = np.column_stack([
            (boxes[:, 0] + boxes[:, 2]) / 2.0,
            (boxes[:, 1] + boxes[:, 3]) / 2.0
        ])

        assigned_ids = [-1] * len(boxes)
        matched_tracks = set()

        if self.tracks:
            track_ids = list(self.tracks.keys())
            track_boxes = np.array([self.tracks[tid]["box"] for tid in track_ids], dtype=np.float32)
            track_centroids = np.array([self.tracks[tid]["centroid"] for tid in track_ids], dtype=np.float32)

            ious = self._compute_iou_batch(boxes, track_boxes)
            for _ in range(min(len(boxes), len(track_ids))):
                max_idx = np.unravel_index(np.argmax(ious), ious.shape)
                d_idx, t_idx = max_idx
                if ious[d_idx, t_idx] < self.iou_threshold:
                    break
                tid = track_ids[t_idx]
                if tid not in matched_tracks and assigned_ids[d_idx] == -1:
                    assigned_ids[d_idx] = tid
                    matched_tracks.add(tid)
                    self.tracks[tid]["box"] = boxes[d_idx]
                    self.tracks[tid]["centroid"] = centroids[d_idx]
                    self.tracks[tid]["lost"] = 0
                ious[d_idx, :] = -1
                ious[:, t_idx] = -1

            for d_idx in range(len(boxes)):
                if assigned_ids[d_idx] != -1:
                    continue
                c = centroids[d_idx]
                best_tid = None
                best_dist = float("inf")
                for t_idx, tid in enumerate(track_ids):
                    if tid in matched_tracks:
                        continue
                    dist = float(np.linalg.norm(c - track_centroids[t_idx]))
                    if dist < self.max_centroid_dist and dist < best_dist:
                        best_dist = dist
                        best_tid = tid

                if best_tid is not None:
                    assigned_ids[d_idx] = best_tid
                    matched_tracks.add(best_tid)
                    self.tracks[best_tid]["box"] = boxes[d_idx]
                    self.tracks[best_tid]["centroid"] = c
                    self.tracks[best_tid]["lost"] = 0

        for i in range(len(boxes)):
            if assigned_ids[i] == -1:
                box_w = boxes[i][2] - boxes[i][0]
                box_h = boxes[i][3] - boxes[i][1]
                if box_w * box_h >= 600:
                    tid = self.next_id
                    self.next_id += 1
                    self.tracks[tid] = {
                        "box": boxes[i],
                        "centroid": centroids[i],
                        "lost": 0
                    }
                    assigned_ids[i] = tid
                else:
                    assigned_ids[i] = -1

        detections.tracker_id = np.array(assigned_ids, dtype=int)
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
    def __init__(self, confidence_threshold: float = 0.40, device: Optional[str] = None):
        self.confidence_threshold = confidence_threshold
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.model = None
        self.predictor = None
        self.tracker = RobustVehicleTracker()

        try:
            self.box_annotator = sv.BoxAnnotator()
            self.label_annotator = sv.LabelAnnotator()
        except Exception:
            self.box_annotator = None
            self.label_annotator = None

        self._load_rfdetr()

    def _load_rfdetr(self):
        try:
            from rfdetr import RFDETRNano
            logger.info("Initializing RF-DETR Nano model...")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.model = RFDETRNano()
                
                # Activate predictor mode if available to eliminate warning
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
            logger.warning(f"Could not initialize RF-DETR ({e}). Using lightweight fallback.")
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
                logger.error(f"Error during RF-DETR inference: {e}")
                detections = sv.Detections.empty()
        else:
            detections = self._fallback_motion_detect(frame_bgr)

        tracked_detections = self.tracker.update_with_detections(detections)
        valid_mask = tracked_detections.tracker_id > 0 if len(tracked_detections) > 0 else []
        active_count = int(np.sum(valid_mask)) if len(valid_mask) > 0 else 0

        annotated = frame_bgr.copy()
        if len(tracked_detections) > 0 and tracked_detections.tracker_id is not None:
            labels = []
            for tid, conf in zip(tracked_detections.tracker_id, tracked_detections.confidence):
                if tid > 0:
                    labels.append(f"ID {tid} ({conf:.2f})")
                else:
                    labels.append("")

            try:
                if self.box_annotator and self.label_annotator:
                    annotated = self.box_annotator.annotate(scene=annotated, detections=tracked_detections)
                    annotated = self.label_annotator.annotate(scene=annotated, detections=tracked_detections, labels=labels)
                else:
                    raise AttributeError
            except Exception:
                for box, lbl in zip(tracked_detections.xyxy, labels):
                    if not lbl:
                        continue
                    x1, y1, x2, y2 = [int(v) for v in box]
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(annotated, lbl, (x1, max(y1 - 6, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)

        return annotated, active_count, tracked_detections

    def _fallback_motion_detect(self, frame_bgr: np.ndarray) -> sv.Detections:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        edges = cv2.Canny(blurred, 60, 160)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes = []
        for c in contours:
            area = cv2.contourArea(c)
            if 900 < area < 40000:
                x, y, w, h = cv2.boundingRect(c)
                aspect_ratio = float(w) / h
                if 0.6 < aspect_ratio < 3.5:
                    boxes.append([x, y, x + w, y + h])

        if not boxes:
            return sv.Detections.empty()

        xyxy = np.array(boxes, dtype=np.float32)
        confidence = np.full(len(boxes), 0.70, dtype=np.float32)
        class_id = np.full(len(boxes), 2, dtype=int)
        return sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)
