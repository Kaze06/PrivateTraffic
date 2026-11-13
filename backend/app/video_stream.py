import asyncio
import time
import os
from pathlib import Path
from typing import Dict, Optional

SHM_DIR = Path("/dev/shm") if Path("/dev/shm").is_dir() else Path(os.environ.get("TEMP", "/tmp"))

class VideoStreamManager:
    """
    Reads the latest frame from zero-latency shared memory and streams it
    at a smooth, stable 30 FPS to the browser.
    """
    def __init__(self):
        self._last_frame_bytes: Dict[str, bytes] = {}

    def _get_shm_path(self, camera_id: str) -> Path:
        safe_name = camera_id.replace("/", "_").replace(":", "_")
        return SHM_DIR / f"pt_frame_{safe_name}.jpg"

    def has_active_stream(self, camera_id: str, timeout_seconds: float = 4.0) -> bool:
        shm_file = self._get_shm_path(camera_id)
        if shm_file.exists():
            try:
                mtime = shm_file.stat().st_mtime
                return (time.time() - mtime) < timeout_seconds
            except OSError:
                return False
        return False

    async def get_stream(self, camera_id: str):
        shm_file = self._get_shm_path(camera_id)
        target_interval = 1.0 / 30.0  # 30 FPS (~33.3ms)
        last_bytes = None

        while True:
            t0 = time.time()

            # Read latest frame from shared memory (takes ~0.05ms)
            if shm_file.exists():
                try:
                    with open(shm_file, "rb") as f:
                        last_bytes = f.read()
                except (OSError, IOError):
                    pass

            if last_bytes:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(last_bytes)).encode() + b"\r\n\r\n" +
                    last_bytes + b"\r\n"
                )

            elapsed = time.time() - t0
            sleep_time = max(0.005, target_interval - elapsed)
            await asyncio.sleep(sleep_time)

stream_manager = VideoStreamManager()
