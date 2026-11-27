import asyncio
import json
import os
import time
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote

FRAME_ROOT = Path(os.environ.get("FRAME_DIR", str(Path(tempfile.gettempdir()) / "private-traffic-frames")))
SHM_DIR = FRAME_ROOT


def configure_worker_frame_dir(instance_id=None):
    """Give each API process its managed worker an isolated frame directory."""
    global SHM_DIR
    configured_dir = os.environ.get("FRAME_DIR")
    if configured_dir:
        SHM_DIR = Path(configured_dir)
    else:
        SHM_DIR = FRAME_ROOT / str(instance_id or os.getpid())
    SHM_DIR.mkdir(parents=True, exist_ok=True)
    return SHM_DIR


def get_shm_frame_path(camera_id):
    return SHM_DIR / ("pt_frame_" + quote(camera_id, safe="") + ".jpg")


def frame_metadata(camera_id):
    path = get_shm_frame_path(camera_id)
    try:
        meta = json.loads(path.with_suffix(".json").read_text())
        if not meta.get("usable") or not path.is_file():
            return None
        if not 0 <= time.time() - meta["written_at"] <= 4:
            return None
        observed = datetime.fromisoformat(meta["observed_at"])
        if observed.tzinfo is None:
            return None
        if not meta["is_replay"] and not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 60:
            return None
        return meta
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_frame(camera_id):
    path = get_shm_frame_path(camera_id)
    for target in (path, path.with_suffix(".json")):
        target.unlink(missing_ok=True)


class VideoStreamManager:
    def _get_shm_path(self, camera_id):
        return get_shm_frame_path(camera_id)

    def has_active_stream(self, camera_id, timeout_seconds=4.0):
        meta = frame_metadata(camera_id)
        return bool(meta and time.time() - meta["written_at"] <= timeout_seconds)

    async def get_stream(self, camera_id):
        last_sequence = None
        while True:
            meta = frame_metadata(camera_id)
            if meta and meta["sequence"] != last_sequence:
                try:
                    data = get_shm_frame_path(camera_id).read_bytes()
                    last_sequence = meta["sequence"]
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                except OSError:
                    pass
            await asyncio.sleep(0.033)


stream_manager = VideoStreamManager()
