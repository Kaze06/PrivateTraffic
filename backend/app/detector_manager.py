import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from app.video_stream import frame_metadata


ROOT_DIR = Path(__file__).resolve().parents[2]
WORKER_PATH = ROOT_DIR / "run_detector_worker.py"
FRAME_WAIT_SECONDS = 90


class DetectorManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._camera_id: Optional[str] = None
        self._started_at: Optional[float] = None

    def start(self, camera_id: str, api_base_url: str):
        with self._lock:
            if self._process is not None and self._process.poll() is not None:
                self._process = None
            if self._process is None:
                env = os.environ.copy()
                env["API_BASE_URL"] = api_base_url.rstrip("/")
                options = {}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NO_WINDOW
                self._process = subprocess.Popen(
                    [sys.executable, str(WORKER_PATH), "--receipt-time"],
                    cwd=str(ROOT_DIR),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    **options,
                )
                self._started_at = time.monotonic()
            self._camera_id = camera_id
            return self.status(camera_id)

    def status(self, camera_id: Optional[str] = None):
        with self._lock:
            process = self._process
            target_camera = camera_id or self._camera_id
            if process is None:
                return {"state": "idle", "camera_id": target_camera}

            exit_code = process.poll()
            if exit_code is not None:
                return {"state": "failed", "camera_id": self._camera_id, "exit_code": exit_code}

            if target_camera and frame_metadata(target_camera):
                state = "running"
            elif self._started_at and time.monotonic() - self._started_at >= FRAME_WAIT_SECONDS:
                state = "waiting_for_frame"
            else:
                state = "starting"
            return {"state": state, "camera_id": self._camera_id}

    def stop(self):
        with self._lock:
            process = self._process
            self._process = None
            self._camera_id = None
            self._started_at = None
            if process is None or process.poll() is not None:
                return {"state": "idle", "camera_id": None}

            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            return {"state": "idle", "camera_id": None}


detector_manager = DetectorManager()
