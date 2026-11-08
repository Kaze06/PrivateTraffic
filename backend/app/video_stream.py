import asyncio
import time
import cv2
import numpy as np
from typing import Dict, Optional

def generate_placeholder_frame(text: str = "Connecting to RF-DETR feed...") -> bytes:
    """Generates an immediate visual status frame so browser connections never hang."""
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    img[:] = (30, 41, 59)  # Slate dark #1e293b
    cv2.putText(img, "PrivateTraffic - Inference Stream", (30, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(img, text, (30, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (56, 189, 248), 1)
    cv2.putText(img, "Awaiting camera frames from detector worker...", (30, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (148, 163, 184), 1)
    ret, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    return buf.tobytes() if ret else b""

class VideoStreamManager:
    """
    Thread-safe buffer holding the latest JPEG frames augmented by the detector model.
    """
    def __init__(self):
        self._frames: Dict[str, bytes] = {}
        self._last_frame_time: Dict[str, float] = {}

    def update_frame(self, camera_id: str, jpeg_bytes: bytes):
        self._frames[camera_id] = jpeg_bytes
        self._last_frame_time[camera_id] = time.time()

    def has_active_stream(self, camera_id: str, timeout_seconds: float = 3.5) -> bool:
        last_time = self._last_frame_time.get(camera_id, 0)
        return (time.time() - last_time) < timeout_seconds

    async def get_stream(self, camera_id: str):
        placeholder = generate_placeholder_frame("Waiting for detector frames...")
        while True:
            if self.has_active_stream(camera_id):
                frame = self._frames.get(camera_id)
            else:
                frame = placeholder

            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                    frame + b"\r\n"
                )
            await asyncio.sleep(0.04)  # ~25 FPS

stream_manager = VideoStreamManager()
