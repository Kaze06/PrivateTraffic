import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

JSON_511MN_PATH = os.getenv("JSON_511MN_PATH", str(BASE_DIR / "511MN.json"))
PILOT_CAMERAS_PATH = os.getenv("PILOT_CAMERAS_PATH", str(BASE_DIR / "pilot_cameras.json"))
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "traffic.db"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads"))

os.makedirs(UPLOAD_DIR, exist_ok=True)

WINDOW_SECONDS = 60
STALE_THRESHOLD_SECONDS = 60
OFFLINE_THRESHOLD_SECONDS = 180

DEFAULT_T1 = 20.0
DEFAULT_T2 = 45.0

FLOW_BAND_CLEAR_MAX = DEFAULT_T1
FLOW_BAND_NORMAL_MAX = DEFAULT_T2

SOURCE_ATTRIBUTION = "Minnesota Department of Transportation (MnDOT) MN511 Traveler Information"
DATA_RETENTION_POLICY = "Aggregate measurements only (count, flow_vpm, timestamp); raw video/frames and identity data are never stored."
