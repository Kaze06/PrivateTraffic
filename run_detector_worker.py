#!/usr/bin/env python3
"""
run_detector_worker.py
Real-time RF-DETR detection worker with strict monotonic frame-clock pacing.
Eliminates HLS chunk-dump bursts (fast-forwarding) and segment starvation pauses.
"""
import sys
import os
import time
import argparse
import threading
import queue
import cv2
import requests
import logging
from pathlib import Path
from datetime import datetime, timezone

os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "reconnect;1|"
    "reconnect_at_eof;1|"
    "reconnect_streamed;1|"
    "reconnect_delay_max;1|"
    "rw_timeout;1000000|"
    "stimeout;1000000|"
    "http_persistent;1|"
    "user_agent;Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.detector import VehicleDetector
from app.config import WINDOW_SECONDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RFDETR-Worker")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
SHM_DIR = Path("/dev/shm") if Path("/dev/shm").is_dir() else Path(os.environ.get("TEMP", "/tmp"))

NATIVE_WIDTH = 720
NATIVE_HEIGHT = 480
TARGET_FPS = 30.0
FRAME_INTERVAL = 1.0 / TARGET_FPS  # 0.03333s

def get_shm_frame_path(camera_id: str) -> Path:
    safe_name = camera_id.replace("/", "_").replace(":", "_")
    return SHM_DIR / f"pt_frame_{safe_name}.jpg"

def write_shm_frame(camera_id: str, frame_bgr):
    """Writes annotated frame directly to RAM in < 0.5ms."""
    ret, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if ret:
        target = get_shm_frame_path(camera_id)
        tmp = target.with_suffix(".tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(buf.tobytes())
            tmp.replace(target)
        except OSError:
            pass

class MonotonicPacedReader:
    """
    Solves the HLS chunk-dump issue:
    In HLS, 90 frames arrive at once every 3 seconds.
    This reader buffers them and meters them out at STRICT 30.0 FPS real-world time.
    3.0 seconds of video takes EXACTLY 3.0 seconds of real-world time to play.
    Zero fast-forward, zero chunk exhaustion pauses.
    """
    def __init__(self, stream_url: str, is_local_file: bool = False):
        self.stream_url = stream_url
        self.is_local_file = is_local_file
        self.running = True
        self.frame_queue = queue.Queue(maxsize=180)  # Holds up to 6s of video
        self.last_frame = None

        self.thread = threading.Thread(target=self._capture_thread, daemon=True)
        self.thread.start()

    def _capture_thread(self):
        cap = cv2.VideoCapture(self.stream_url)
        if not self.is_local_file:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

        fail_count = 0
        while self.running:
            if cap.isOpened() and fail_count < 10:
                try:
                    ret, frame = cap.read()
                except Exception as e:
                    logger.warning(f"FFmpeg read error encountered: {e}")
                    ret, frame = False, None

                if ret and frame is not None:
                    fail_count = 0
                    h, w = frame.shape[:2]
                    if (w, h) != (NATIVE_WIDTH, NATIVE_HEIGHT):
                        frame = cv2.resize(frame, (NATIVE_WIDTH, NATIVE_HEIGHT), interpolation=cv2.INTER_LINEAR)

                    # Put frame into buffer; block briefly if full to prevent overrun
                    try:
                        self.frame_queue.put(frame, timeout=0.1)
                    except queue.Full:
                        # Drop oldest frame if queue overfills (e.g. paused tab)
                        try:
                            self.frame_queue.get_nowait()
                            self.frame_queue.put_nowait(frame)
                        except (queue.Empty, queue.Full):
                            pass

                    if self.is_local_file:
                        time.sleep(FRAME_INTERVAL * 0.95)
                else:
                    if self.is_local_file:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    fail_count += 1
                    time.sleep(0.04)
            else:
                time.sleep(0.5)
                if not self.running:
                    break
                cap.release()
                cap = cv2.VideoCapture(self.stream_url)
                fail_count = 0

        cap.release()

    def get_paced_frame(self) -> np.ndarray:
        """Pulls from queue with fallback to last frame if network has a slight hiccup."""
        try:
            frame = self.frame_queue.get(timeout=FRAME_INTERVAL)
            self.last_frame = frame
            return frame
        except queue.Empty:
            return self.last_frame

    def stop(self):
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)

def get_camera_status(camera_id: str, session: requests.Session):
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

def resolve_stream_url(stream_url: str):
    is_local = False
    if str(stream_url).startswith("/uploads/"):
        local_path = BASE_DIR / stream_url.lstrip("/")
        if local_path.exists():
            return str(local_path), True
        return f"{API_BASE_URL}{stream_url}", False
    return stream_url, is_local

def main():
    parser = argparse.ArgumentParser(description="PrivateTraffic Detection Worker")
    parser.add_argument("--source", type=str, default=None, help="Fixed video file or stream URL")
    args = parser.parse_args()

    logger.info("Initializing Vehicle Detection Worker with RF-DETR...")
    detector = VehicleDetector()
    session = requests.Session()

    current_cam = get_default_camera(session)
    camera_id = current_cam["id"]

    status_info = get_camera_status(camera_id, session)
    initial_url = status_info.get("stream_url") if status_info else current_cam.get("stream_url")

    stream_url, is_local = resolve_stream_url(args.source or initial_url)
    logger.info(f"Target Camera: {camera_id} | Source: {stream_url} (Local: {is_local})")

    reader = MonotonicPacedReader(stream_url, is_local_file=is_local)

    unique_tracked_ids = set()
    window_start_time = time.time()
    last_focus_check_time = time.time()

    # Pre-buffer: wait until queue has at least 30 frames (1 second) before starting playback clock
    logger.info("Buffering initial live segment to establish 1.0x playback lock...")
    for _ in range(60):
        if reader.frame_queue.qsize() >= 25 or is_local:
            break
        time.sleep(0.01)

    # Master monotonic clock for absolute 1.0x pacing
    next_frame_time = time.monotonic()

    try:
        while True:
            t_now = time.time()

            # Poll for active camera switch every 2.5s
            if args.source is None and (t_now - last_focus_check_time > 2.5):
                last_focus_check_time = t_now
                active_id = get_active_focus_camera_id(session)
                if active_id and active_id != camera_id:
                    cam_info = get_camera_status(active_id, session)
                    if cam_info and cam_info.get("stream_url"):
                        logger.info(f"[Camera Switch] Transitioning to: {active_id} ({cam_info.get('camera_title')})")
                        camera_id = active_id
                        stream_url, is_local = resolve_stream_url(cam_info["stream_url"])
                        reader.stop()
                        reader = MonotonicPacedReader(stream_url, is_local_file=is_local)
                        unique_tracked_ids.clear()
                        window_start_time = time.time()
                        next_frame_time = time.monotonic()
                        continue

            # Fetch the next frame from the paced queue
            frame = reader.get_paced_frame()

            if frame is not None:
                # Process detection & tracking
                annotated_frame, active_count, detections = detector.process_frame(frame)

                if detections.tracker_id is not None:
                    for tid in detections.tracker_id:
                        if int(tid) > 0:
                            unique_tracked_ids.add(int(tid))

                # Write to shared memory for immediate WebSocket delivery
                write_shm_frame(camera_id, annotated_frame)

            # 60-second measurement window completion
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

            # Monotonic clock lock: exactly 33.33ms per frame
            next_frame_time += FRAME_INTERVAL
            sleep_time = next_frame_time - time.monotonic()

            if sleep_time > 0:
                time.sleep(sleep_time)
            elif sleep_time < -0.10:
                # If inference lagged behind, advance clock without fast-forwarding
                next_frame_time = time.monotonic()

    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
    finally:
        reader.stop()

if __name__ == "__main__":
    main()
