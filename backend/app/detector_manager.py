import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from app.video_stream import frame_metadata


ROOT_DIR = Path(__file__).resolve().parents[2]
WORKER_PATH = ROOT_DIR / "run_detector_worker.py"
FRAME_WAIT_SECONDS = 90
DETECTOR_MODULES = {
    "torch": "torch",
    "torchvision": "torchvision",
    "rfdetr": "rfdetr",
    "supervision": "supervision",
    "opencv-python-headless": "cv2",
}


def missing_detector_dependencies():
    return [package for package, module in DETECTOR_MODULES.items()
            if importlib.util.find_spec(module) is None]


class DetectorManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._camera_id: Optional[str] = None
        self._started_at: Optional[float] = None
        self._log_path = Path(tempfile.gettempdir()) / "private-traffic-detector-worker.log"
        self._log_handle = None
        self._last_error = None

    def start(self, camera_id: str, api_base_url: str):
        with self._lock:
            if self._process is not None and self._process.poll() is not None:
                self._close_log()
                self._process = None
            if self._process is None:
                env = os.environ.copy()
                env["API_BASE_URL"] = api_base_url.rstrip("/")
                options = {}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NO_WINDOW
                self._last_error = None
                self._log_handle = self._log_path.open("w", encoding="utf-8", errors="replace")
                try:
                    self._process = subprocess.Popen(
                        [sys.executable, str(WORKER_PATH), "--receipt-time"],
                        cwd=str(ROOT_DIR),
                        env=env,
                        stdout=self._log_handle,
                        stderr=subprocess.STDOUT,
                        **options,
                    )
                except Exception:
                    self._close_log()
                    raise
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
                self._read_worker_error()
                result = {"state": "failed", "camera_id": self._camera_id, "exit_code": exit_code}
                if self._last_error:
                    result["detail"] = self._last_error
                return result

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
                self._close_log()
                return {"state": "idle", "camera_id": None}

            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            self._close_log()
            return {"state": "idle", "camera_id": None}

    def _read_worker_error(self):
        if self._log_handle is not None:
            self._log_handle.flush()
            self._log_handle.close()
            self._log_handle = None
        if self._last_error is None and self._log_path.is_file():
            lines = [line.strip() for line in self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                     if line.strip()]
            if lines:
                self._last_error = lines[-1][-500:]

    def _close_log(self):
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


detector_manager = DetectorManager()
