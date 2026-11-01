import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.config import JSON_511MN_PATH, PILOT_CAMERAS_PATH
from app.models import CameraSourceModel

logger = logging.getLogger(__name__)

def extract_road_name(title: str) -> str:
    if not title:
        return "Other"
    parts = title.split(":")
    return parts[0].strip() if len(parts) > 1 else title.strip()

def load_pilot_codes() -> Dict[str, Dict[str, Any]]:
    try:
        with open(PILOT_CAMERAS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["camera_code"]: item for item in data if "camera_code" in item}
    except Exception as e:
        logger.error(f"Failed to read pilot_cameras.json: {e}")
        return {}

def initialize_camera_database(db: Session) -> int:
    """
    Parses 511MN.json once on startup and populates the SQLite database.
    Matches cameras with pilot_cameras.json to flag is_pilot.
    """
    if db.query(CameraSourceModel).count() > 0:
        return db.query(CameraSourceModel).count()

    try:
        with open(JSON_511MN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Cannot load 511MN.json: {e}")
        return 0

    features = data.get("data", {}).get("mapFeaturesQuery", {}).get("mapFeatures", [])
    pilot_map = load_pilot_codes()
    cameras_to_create = []

    for item in features:
        cam_id = item.get("uri")
        title = item.get("title", "Unknown Camera")
        road = extract_road_name(title)
        
        feature_list = item.get("features", [])
        if not feature_list:
            continue
        geom = feature_list[0].get("geometry", {})
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        lng, lat = float(coords[0]), float(coords[1])

        # Extract camera code from view URL (e.g. .../cameras/MN/C631 -> C631)
        camera_code = None
        stream_url = None
        for view in item.get("views", []):
            url = view.get("url", "")
            if "/cameras/MN/" in url:
                code_part = url.split("/cameras/MN/")[1].split("?")[0]
                camera_code = code_part
                stream_url = f"https://video.dot.state.mn.us/public/{camera_code}.stream/playlist.m3u8"
                break

        is_pilot = False
        if camera_code and camera_code in pilot_map:
            is_pilot = True
            # Allow pilot config to override stream url if specified
            if pilot_map[camera_code].get("stream_url"):
                stream_url = pilot_map[camera_code]["stream_url"]

        cam_record = CameraSourceModel(
            id=cam_id,
            camera_code=camera_code,
            title=title,
            road_name=road,
            latitude=lat,
            longitude=lng,
            stream_url=stream_url,
            is_pilot=is_pilot,
            current_status="unavailable"
        )
        cameras_to_create.append(cam_record)

    db.bulk_save_objects(cameras_to_create)
    db.commit()
    logger.info(f"Loaded {len(cameras_to_create)} camera sources into local database.")
    return len(cameras_to_create)
