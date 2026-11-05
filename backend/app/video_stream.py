import asyncio
import time
from typing import Dict, Optional

class VideoStreamManager:
    """
    Thread-safe buffer holding the latest JPEG frames augmented by YOLO model.
    """
    def __init__(self):
        self._frames: Dict[str, bytes] = {}
        self._last_frame_time: Dict[str, float] = {}

    def update_frame(self, camera_id: str, jpeg_bytes: bytes):
        """Called by CV loop for every processed frame."""
        self._frames[camera_id] = jpeg_bytes
        self._last_frame_time[camera_id] = time.time()

    def has_active_stream(self, camera_id: str, timeout_seconds: float = 3.0) -> bool:
        """Returns True if frames were received within the last timeout_seconds."""
        last_time = self._last_frame_time.get(camera_id, 0)
        return (time.time() - last_time) < timeout_seconds

    async def get_stream(self, camera_id: str):
        """Generates a multipart HTTP MJPEG stream for the frontend."""
        while True:
            frame = self._frames.get(camera_id)
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            await asyncio.sleep(0.033367)  # ~30 fps

stream_manager = VideoStreamManager()
