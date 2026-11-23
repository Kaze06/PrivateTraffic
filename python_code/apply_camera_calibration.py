"""Apply a selected pilot camera's recorded calibration to an existing SQLite DB."""
import argparse
import json
import math
import sqlite3
from pathlib import Path


class CalibrationUpdateError(ValueError):
    """The requested database or calibration cannot be applied safely."""


def _configured_calibration(config_path, camera_code):
    try:
        items = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CalibrationUpdateError(f"pilot config unavailable: {config_path}") from exc
    matches = [item for item in items if item.get("camera_code") == camera_code]
    if len(matches) != 1:
        raise CalibrationUpdateError(f"camera_code must occur once in pilot config: {camera_code}")
    item = matches[0]
    low, high, note = item.get("calibration_t1"), item.get("calibration_t2"), item.get("calibration_note")
    if (isinstance(low, bool) or isinstance(high, bool) or not isinstance(low, (int, float))
            or not isinstance(high, (int, float)) or not math.isfinite(low) or not math.isfinite(high)
            or not 0 < low < high or not isinstance(note, str) or not note.strip()):
        raise CalibrationUpdateError(f"invalid calibration for {camera_code}")
    return float(low), float(high), note.strip()


def apply_camera_calibration(database_path, pilot_config_path, camera_code="C8351"):
    """Change one existing camera row; preserve other cameras and measurements."""
    database_path = Path(database_path)
    if not database_path.is_file():
        raise CalibrationUpdateError(f"database file not found: {database_path}")
    low, high, note = _configured_calibration(pilot_config_path, camera_code)
    try:
        with sqlite3.connect(str(database_path)) as connection:
            rows = connection.execute(
                "SELECT id, calibration_t1, calibration_t2, calibration_note "
                "FROM camera_sources WHERE camera_code = ?", (camera_code,)
            ).fetchall()
            if len(rows) != 1:
                raise CalibrationUpdateError(f"expected one {camera_code} camera, found {len(rows)}")
            camera_id, old_low, old_high, old_note = rows[0]
            connection.execute(
                "UPDATE camera_sources SET calibration_t1 = ?, calibration_t2 = ?, calibration_note = ? WHERE id = ?",
                (low, high, note, camera_id),
            )
    except sqlite3.Error as exc:
        raise CalibrationUpdateError(f"database schema or update failed: {exc}") from exc
    return {"camera_id": camera_id, "camera_code": camera_code, "t1": low, "t2": high,
            "calibration_note": note, "previous": {"t1": old_low, "t2": old_high, "note": old_note}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="Existing SQLite traffic.db")
    parser.add_argument("--pilot-config", required=True, type=Path, help="Repository pilot_cameras.json")
    parser.add_argument("--camera-code", default="C8351")
    args = parser.parse_args()
    try:
        result = apply_camera_calibration(args.database, args.pilot_config, args.camera_code)
    except CalibrationUpdateError as exc:
        parser.exit(1, f"Calibration update error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
