"""Audit labelled 60-second road windows without choosing calibration thresholds."""
import argparse
import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path


LABELS = ("clear", "normal", "congested")
REQUIRED = ("sample_id", "camera_id", "model_version", "count_rule",
            "window_start_utc", "window_end_utc", "flow_vpm", "label", "label_source",
            "video_sha256", "report_sha256")


class CalibrationError(ValueError):
    """The supplied windows cannot support a calibration review."""


def _aware_time(value, field):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise CalibrationError(f"{field}: timezone-aware timestamp required") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise CalibrationError(f"{field}: timezone-aware timestamp required")
    return result


def audit_rows(rows):
    """Return evidence bounds; never select T1/T2 or update the application."""
    rows = list(rows)
    if not rows:
        raise CalibrationError("at least one labelled window is required")
    groups = {label: [] for label in LABELS}
    sample_ids = set()
    reference = None
    for number, row in enumerate(rows, 1):
        for field in REQUIRED:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise CalibrationError(f"row {number}: {field} is required")
        for field in ("video_sha256", "report_sha256"):
            if re.fullmatch(r"[0-9a-fA-F]{64}", row[field]) is None:
                raise CalibrationError(f"row {number}: {field} must be SHA-256")
        sample_id = row["sample_id"]
        if sample_id in sample_ids:
            raise CalibrationError(f"duplicate sample_id: {sample_id}")
        sample_ids.add(sample_id)
        label = row["label"].strip().lower()
        if label not in groups:
            raise CalibrationError(f"row {number}: unsupported label {label}")
        current = (row["camera_id"], row["model_version"], row["count_rule"])
        if reference is not None and current != reference:
            raise CalibrationError("windows must share camera, model version and count rule")
        reference = current
        start = _aware_time(row["window_start_utc"], "window_start_utc")
        end = _aware_time(row["window_end_utc"], "window_end_utc")
        if abs((end - start).total_seconds() - 60) > 0.001:
            raise CalibrationError(f"row {number}: window must be 60 seconds")
        try:
            flow = float(row["flow_vpm"])
        except ValueError as exc:
            raise CalibrationError(f"row {number}: invalid flow_vpm") from exc
        if not math.isfinite(flow) or flow < 0:
            raise CalibrationError(f"row {number}: invalid flow_vpm")
        groups[label].append(flow)
    missing = [label for label in LABELS if not groups[label]]
    low = {"above": max(groups["clear"]) if groups["clear"] else None,
           "at_most": min(groups["normal"]) if groups["normal"] else None}
    high = {"above": max(groups["normal"]) if groups["normal"] else None,
            "at_most": min(groups["congested"]) if groups["congested"] else None}
    conflict = ((low["above"] is not None and low["at_most"] is not None
                 and low["above"] >= low["at_most"])
                or (high["above"] is not None and high["at_most"] is not None
                    and high["above"] >= high["at_most"]))
    return {
        "camera_id": reference[0], "model_version": reference[1], "count_rule": reference[2],
        "sample_count": len(rows), "label_counts": {label: len(groups[label]) for label in LABELS},
        "missing_labels": missing,
        "status": "overlapping_classes" if conflict else "insufficient_classes" if missing else "ready_for_review",
        "t1_range": None if conflict else low, "t2_range": None if conflict else high,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("windows", type=Path, help="Local labelled 60-second CSV")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.windows.open(encoding="utf-8-sig", newline="") as stream:
            result = audit_rows(csv.DictReader(stream))
        if args.output.exists():
            raise CalibrationError(f"refusing to overwrite: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (CalibrationError, OSError) as exc:
        parser.exit(1, f"Calibration audit error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
