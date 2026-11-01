import sys
import time
from pathlib import Path
import cv2
import requests
from ultralytics import YOLO

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.video_stream import stream_manager

BACKEND_API_URL = "http://127.0.0.1:8000/api/measurements"
STATUS_API_URL = "http://127.0.0.1:8000/api/cameras"

TARGET_CAMERA_ID = "camera/507320"
TARGET_CLASSES = [2, 3, 5, 7] # car, motorcycle, bus, truck

LINE_Y = 320
LINE_X_START = 50
LINE_X_END = 670

def is_crossing_line(prev_y, curr_y, line_y):
    return prev_y < line_y <= curr_y

def run_pipeline():
    print(f"Loading YOLOv8 model for camera: {TARGET_CAMERA_ID}...")
    model = YOLO("yolov8n.pt")

    # Fetch initial stream URL (or local uploaded backup clip) from backend
    try:
        status_res = requests.get(f"{STATUS_API_URL}/{TARGET_CAMERA_ID}/status").json()
        stream_source = status_res.get("stream_url")
        is_replay = (status_res.get("status") == "replay_not_live")
    except Exception:
        stream_source = "https://video.dot.state.mn.us/public/C631.stream/playlist.m3u8"
        is_replay = False

    print(f"Opening video source: {stream_source} (Replay Mode: {is_replay})")
    cap = cv2.VideoCapture(stream_source)

    track_history = {}
    counted_ids = set()
    minute_vehicle_count = 0
    window_start_time = time.time()

    try:
        while True:
            if not cap.isOpened():
                time.sleep(1)
                cap = cv2.VideoCapture(stream_source)
                continue

            success, frame = cap.read()
            if not success:
                # If reading a local backup file, loop back to frame 0 for continuous demonstration
                if is_replay or (isinstance(stream_source, str) and stream_source.endswith(".mp4")):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                else:
                    time.sleep(1)
                    cap = cv2.VideoCapture(stream_source)
                    continue

            results = model.track(frame, persist=True, classes=TARGET_CLASSES, verbose=False)
            current_frame_tracked_ids = set()

            if results[0].boxes and results[0].boxes.id is not None:
                boxes = results[0].boxes.xyxy.cpu().numpy()
                track_ids = results[0].boxes.id.int().cpu().numpy()

                for box, track_id in zip(boxes, track_ids):
                    current_frame_tracked_ids.add(track_id)
                    x1, y1, x2, y2 = box
                    cx = int((x1 + x2) / 2)
                    cy = int(y2)

                    if track_id in track_history:
                        prev_y = track_history[track_id]
                        if track_id not in counted_ids:
                            if is_crossing_line(prev_y, cy, LINE_Y):
                                counted_ids.add(track_id)
                                minute_vehicle_count += 1

                    track_history[track_id] = cy

            # Clean memory
            if len(track_history) > 200:
                track_history = {tid: y for tid, y in track_history.items() if tid in current_frame_tracked_ids}

            # Draw tripwire and HUD
            cv2.line(frame, (LINE_X_START, LINE_Y), (LINE_X_END, LINE_Y), (0, 0, 255), 2)
            mode_tag = "REPLAY (NOT LIVE)" if is_replay else "LIVE STREAM"
            cv2.putText(frame, f"MODE: {mode_tag} | 1-Min Count: {minute_vehicle_count}", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)

            annotated_frame = results[0].plot(img=frame)
            _, jpeg_buffer = cv2.imencode('.jpg', annotated_frame)
            stream_manager.update_frame(TARGET_CAMERA_ID, jpeg_buffer.tobytes())

            # Submit 60-second measurement
            if time.time() - window_start_time >= 60.0:
                payload = {
                    "camera_id": TARGET_CAMERA_ID,
                    "vehicle_count": minute_vehicle_count,
                    "flow_per_minute": float(minute_vehicle_count),
                    "source_status": "replay_not_live" if is_replay else "live_fresh",
                    "is_replay": is_replay
                }
                try:
                    requests.post(BACKEND_API_URL, json=payload, timeout=3.0)
                except Exception as e:
                    print(f"Error posting measurement: {e}")

                minute_vehicle_count = 0
                counted_ids.clear()
                window_start_time = time.time()

    except KeyboardInterrupt:
        print("Pipeline stopped.")
    finally:
        cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    run_pipeline()
