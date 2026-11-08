#!/usr/bin/env python3
"""
run_detector_worker.py
Thread-safe, real-time RF-DETR detection worker with strict 1.0x wall-clock pacing,
zero buffer bursts, and quiet operation.
"""
import sys
import os
import time
import argparse
import threading
import cv2
import numpy as np
import requests
import logging
from pathlib import Path
from datetime import datetime, timezone

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "reconnect;1|reconnect_streamed;1|reconnect_delay_max;3|"
    "rw_timeout;8000000|http_persistent;0|"
    "user_agent;Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.detector import VehicleDetector
from app.config import WINDOW_SECONDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RFDETR-Worker")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

class PacedStreamReader:
    """
    Reads stream frames with strict wall-clock pacing (~30 FPS).
    Prevents HLS chunk-dump bursts (fast-forwarding) followed by long buffer stalls.
    """
    def __init__(self, stream_url: str):
        self.stream_url = stream_url
        self.latest_frame = None
        self.running = True
        self.has_fresh_frame = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()

    def _worker_loop(self):
        cap = cv2.VideoCapture(self.stream_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        fail_count = 0
        target_fps = 30.0
        frame_interval = 1.0 / target_fps

        while self.running:
            t0 = time.time()
            if cap.isOpened() and fail_count < 10:
                ret, frame = cap.read()
                if ret and frame is not None:
                    fail_count = 0
                    with self.lock:
                        self.latest_frame = frame
                        self.has_fresh_frame = True

                    # Pace decoding to real-time playback
                    elapsed = time.time() - t0
                    sleep_time = max(0.001, frame_interval - elapsed)
                    time.sleep(sleep_time)
                else:
                    fail_count += 1
                    time.sleep(0.05)
            else:
                time.sleep(0.5)
                if not self.running:
                    break
                cap.release()
                cap = cv2.VideoCapture(self.stream_url)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                fail_count = 0

        cap.release()

    def get_latest_frame(self):
        with self.lock:
            if self.latest_frame is not None:
                frame = self.latest_frame.copy()
                self.has_fresh_frame = False
                return frame
            return None

    def stop(self):
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)

def get_camera_by_id(camera_id: str, session: requests.Session):
    try:
        res = session.get(f"{API_BASE_URL}/api/cameras/{camera_id}/status", timeout=2)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

def get_active_focus_camera_id(session: requests.Session):
    try:
        res = session.get(f"{API_BASE_URL}/api/cameras/active-focus", timeout=2)
        if res.status_code == 200:
            return res.json().get("active_camera_id")
    except Exception:
        pass
    return None

def get_default_camera(session: requests.Session):
    try:
        res = session.get(f"{API_BASE_URL}/api/cameras?pilot_only=true", timeout=3)
        if res.status_code == 200:
            cams = res.json()
            for c in cams:
                if c.get("is_default"):
                    return c
            if cams:
                return cams[0]
    except Exception:
        pass
    return {
        "id": "camera/507320",
        "camera_code": "C631",
        "title": "I-35W SB @ Johnson St",
        "stream_url": "https://video.dot.state.mn.us/public/C631.stream/playlist.m3u8"
    }

def create_synthetic_roadway_frame(frame_idx: int) -> np.ndarray:
    h, w = 360, 640
    frame = np.full((h, w, 3), 60, dtype=np.uint8)

    cv2.line(frame, (0, 180), (w, 180), (255, 255, 255), 2)
    for x in range(0, w, 50):
        cv2.line(frame, (x, 120), (x + 25, 120), (255, 255, 255), 2)
        cv2.line(frame, (x, 240), (x + 25, 240), (255, 255, 255), 2)

    v1_x = int((frame_idx * 4) % (w + 100)) - 80
    cv2.rectangle(frame, (v1_x, 135), (v1_x + 75, 170), (40, 40, 180), -1)

    v2_x = int((frame_idx * 3 + 180) % (w + 120)) - 90
    cv2.rectangle(frame, (v2_x, 195), (v2_x + 85, 235), (180, 50, 50), -1)

    cv2.putText(frame, "Stream Connecting / Reconnecting", (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
    return frame

def resolve_stream_url(stream_url: str) -> str:
    if str(stream_url).startswith("/uploads/"):
        local_path = BASE_DIR / stream_url.lstrip("/")
        if local_path.exists():
            return str(local_path)
        return f"{API_BASE_URL}{stream_url}"
    return stream_url

def main():
    parser = argparse.ArgumentParser(description="PrivateTraffic Detection Worker")
    parser.add_argument("--source", type=str, default=None, help="Fixed video file or stream URL")
    args = parser.parse_args()

    logger.info("Initializing Vehicle Detection Worker with RF-DETR...")
    detector = VehicleDetector()
    session = requests.Session()

    current_cam = get_default_camera(session)
    camera_id = current_cam["id"]
    stream_url = resolve_stream_url(args.source or current_cam.get("stream_url"))

    logger.info(f"Target Camera: {camera_id} ({current_cam.get('title')})")
    reader = PacedStreamReader(stream_url)

    unique_tracked_ids = set()
    window_start_time = time.time()
    last_frame_push_time = 0.0
    last_focus_check_time = time.time()
    frame_counter = 0

    try:
        while True:
            # Poll for camera focus changes every 3 seconds
            if args.source is None and (time.time() - last_focus_check_time > 3.0):
                last_focus_check_time = time.time()
                active_id = get_active_focus_camera_id(session)
                if active_id and active_id != camera_id:
                    cam_info = get_camera_by_id(active_id, session)
                    if cam_info and cam_info.get("stream_url"):
                        logger.info(f"[Camera Switch] Transitioning to: {active_id} ({cam_info.get('camera_title')})")
                        camera_id = active_id
                        stream_url = resolve_stream_url(cam_info["stream_url"])

                        reader.stop()
                        reader = PacedStreamReader(stream_url)

                        unique_tracked_ids.clear()
                        window_start_time = time.time()

            raw_frame = reader.get_latest_frame()
            if raw_frame is None:
                frame = create_synthetic_roadway_frame(frame_counter)
                time.sleep(0.033)
            else:
                h, w = raw_frame.shape[:2]
                if w != 640:
                    new_h = int(h * (640 / w))
                    frame = cv2.resize(raw_frame, (640, new_h), interpolation=cv2.INTER_LINEAR)
                else:
                    frame = raw_frame

            frame_counter += 1

            annotated_frame, active_count, detections = detector.process_frame(frame)

            if detections.tracker_id is not None:
                for tid in detections.tracker_id:
                    if int(tid) > 0:
                        unique_tracked_ids.add(int(tid))

            # Push live augmented JPEG frame smoothly at ~15 FPS
            now_ts = time.time()
            if now_ts - last_frame_push_time >= 0.066:
                ret_enc, jpeg_buf = cv2.imencode(".jpg", annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                if ret_enc:
                    try:
                        session.post(
                            f"{API_BASE_URL}/api/cameras/{camera_id}/frame",
                            data=jpeg_buf.tobytes(),
                            headers={"Content-Type": "image/jpeg"},
                            timeout=0.20
                        )
                        last_frame_push_time = now_ts
                    except Exception:
                        pass

            # 60s measurement window completion
            elapsed = time.time() - window_start_time
            if elapsed >= WINDOW_SECONDS:
                total_unique = len(unique_tracked_ids)
                flow_vpm = round((total_unique * 60.0) / elapsed, 2)
                is_replay = "/uploads" in str(stream_url) or "backup" in str(stream_url)

                payload = {
                    "camera_id": camera_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "vehicle_count": total_unique,
                    "flow_per_minute": flow_vpm,
                    "source_status": "replay_not_live" if is_replay else "live",
                    "is_replay": is_replay
                }

                try:
                    res = session.post(f"{API_BASE_URL}/api/measurements", json=payload, timeout=5)
                    logger.info(f"[Window Complete] Camera={camera_id} Count={total_unique}, Flow={flow_vpm} vpm | Status={res.status_code}")
                except Exception as ex:
                    logger.error(f"Failed to submit measurement: {ex}")

                unique_tracked_ids.clear()
                window_start_time = time.time()

            time.sleep(0.005)

    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
    finally:
        reader.stop()

if __name__ == "__main__":
    main()
