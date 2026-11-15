"""Prepare and evaluate 60-second manual count evidence for recorded samples.

This tool does not infer human truth or certify recording permissions. It keeps
source videos and completed count results outside Git unless separately shared.
"""

import argparse
import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


class EvidenceError(ValueError):
    """An evidence input is incomplete, inconsistent, or not reproducible."""


INDEX_FIELDS = [
    "sample_id", "scenario", "camera_id", "camera_code", "recording_start_utc",
    "count_rule", "count_line_y", "model_version", "video_path", "video_sha256", "reports_path",
    "report_sha256", "permission_reference", "retention_terms", "window_count",
]
MANUAL_REQUIRED = ["sample_id", "window_start_utc", "window_end_utc", "manual_count", "annotator"]
MANUAL_FIELDS = ["sample_id", "window_start_utc", "window_end_utc", "start_offset_seconds",
                 "end_offset_seconds", "manual_count", "annotator"]
AUTOMATIC_FIELDS = ["sample_id", "scenario", "camera_id", "camera_code", "window_start_utc",
                    "window_end_utc", "start_offset_seconds", "end_offset_seconds",
                    "count_rule", "count_line_y", "model_version", "automatic_count", "report_sha256"]
COMPARISON_FIELDS = [
    "sample_id", "scenario", "camera_id", "camera_code", "window_start_utc",
    "window_end_utc", "start_offset_seconds", "end_offset_seconds", "count_rule", "count_line_y",
    "model_version", "annotator", "manual_count",
    "automatic_count", "absolute_error", "relative_error", "false_positives",
    "target_error", "within_target", "video_sha256", "report_sha256",
]
TARGETS = {"normal": 0.10, "peak": 0.15}
COUNT_RULES = {"downward_line_crossing", "unique_tracks_present"}


def _required_text(obj, key, where):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{where}: {key} is required")
    return value.strip()


def _timestamp(value, where):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise EvidenceError(f"{where}: timezone-aware timestamp is required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceError(f"{where}: timezone-aware timestamp is required")
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value, where):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError(f"{where}: nonnegative integer is required")
    return value


def _existing_file(base, raw, where):
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise EvidenceError(f"{where}: file not found: {path}")
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_reports(path, sample_id, camera_id, recording_start):
    windows = []
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                report = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceError(f"{sample_id}: bad report JSON at line {line_number}") from exc
            if not isinstance(report, dict):
                raise EvidenceError(f"{sample_id}: report line {line_number} must be an object")
            if report.get("camera_id") != camera_id:
                raise EvidenceError(f"{sample_id}: report camera_id differs from manifest")
            if report.get("validity") == "invalid":
                continue  # An incomplete tail cannot enter an accuracy comparison.
            if report.get("validity") != "valid" or report.get("window_seconds") != 60:
                raise EvidenceError(f"{sample_id}: report must be a valid 60-second window")
            if report.get("is_replay") is not True or report.get("source_status") != "replay_not_live":
                raise EvidenceError(f"{sample_id}: recorded sample report must be replay_not_live")
            count = _nonnegative_int(report.get("vehicle_count"), f"{sample_id}: vehicle_count")
            end = _timestamp(report.get("timestamp"), f"{sample_id}: report timestamp")
            start = end - timedelta(seconds=60)
            if start < recording_start:
                raise EvidenceError(f"{sample_id}: report window precedes recording_start")
            if end in seen:
                raise EvidenceError(f"{sample_id}: duplicate automatic report window")
            seen.add(end)
            windows.append((start, end, count))
    windows.sort(key=lambda item: item[1])
    if not windows:
        raise EvidenceError(f"{sample_id}: no valid 60-second automatic report")
    for previous, current in zip(windows, windows[1:]):
        if current[0] < previous[1]:
            raise EvidenceError(f"{sample_id}: overlapping automatic report windows")
    return windows


def _load_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read manifest: {manifest_path}") from exc
    raw_samples = data.get("samples") if isinstance(data, dict) else None
    if not isinstance(raw_samples, list) or not raw_samples:
        raise EvidenceError("manifest must contain at least one sample")
    samples = []
    seen_ids = set()
    for raw in raw_samples:
        if not isinstance(raw, dict):
            raise EvidenceError("each sample must be an object")
        sample_id = _required_text(raw, "sample_id", "sample")
        if sample_id in seen_ids:
            raise EvidenceError(f"duplicate sample_id: {sample_id}")
        seen_ids.add(sample_id)
        scenario = _required_text(raw, "scenario", sample_id)
        if scenario not in TARGETS:
            raise EvidenceError(f"{sample_id}: scenario must be normal or peak")
        camera_id = _required_text(raw, "camera_id", sample_id)
        camera_code = _required_text(raw, "camera_code", sample_id)
        recording_start = _timestamp(_required_text(raw, "recording_start", sample_id),
                                     f"{sample_id}: recording_start")
        count_rule = _required_text(raw, "count_rule", sample_id)
        if count_rule not in COUNT_RULES:
            raise EvidenceError(f"{sample_id}: unknown count_rule")
        if count_rule == "downward_line_crossing":
            count_line_y = _nonnegative_int(raw.get("count_line_y"), f"{sample_id}: count_line_y")
        elif raw.get("count_line_y") is None:
            count_line_y = ""
        else:
            raise EvidenceError(f"{sample_id}: count_line_y must be empty for unique_tracks_present")
        model_version = _required_text(raw, "model_version", sample_id)
        permission = _required_text(raw, "permission_reference", sample_id)
        retention = _required_text(raw, "retention_terms", sample_id)
        video = _existing_file(manifest_path.parent, _required_text(raw, "video_path", sample_id),
                               f"{sample_id}: video_path")
        reports = _existing_file(manifest_path.parent, _required_text(raw, "reports_path", sample_id),
                                 f"{sample_id}: reports_path")
        windows = _read_reports(reports, sample_id, camera_id, recording_start)
        samples.append({
            "sample_id": sample_id, "scenario": scenario, "camera_id": camera_id,
            "camera_code": camera_code, "recording_start_utc": recording_start.isoformat(),
            "count_rule": count_rule, "count_line_y": count_line_y,
            "model_version": model_version,
            "video_path": str(video), "video_sha256": _sha256(video),
            "reports_path": str(reports), "report_sha256": _sha256(reports),
            "permission_reference": permission, "retention_terms": retention,
            "window_count": len(windows), "windows": windows,
        })
    return samples


def _write_csv(path, fields, rows):
    if path.exists():
        raise EvidenceError(f"refusing to overwrite existing evidence: {path}")
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _index_rows(samples):
    return [{key: sample[key] for key in INDEX_FIELDS} for sample in samples]


def _verify_prepared_index(path, samples):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != INDEX_FIELDS:
            raise EvidenceError("prepared sample index has different columns")
        recorded = list(reader)
    expected = [{key: str(row[key]) for key in INDEX_FIELDS} for row in _index_rows(samples)]
    if recorded != expected:
        raise EvidenceError("prepared sample index differs from current video, report or metadata")


def _offsets(sample, start, end):
    recording_start = _timestamp(sample["recording_start_utc"], "recording_start_utc")
    return (f"{(start - recording_start).total_seconds():.3f}",
            f"{(end - recording_start).total_seconds():.3f}")


def prepare(manifest_path, output_dir):
    """Write source hashes and a blank manual sheet aligned to automatic windows."""
    samples = _load_manifest(manifest_path)
    manual = [{"sample_id": sample["sample_id"], "window_start_utc": start.isoformat(),
               "window_end_utc": end.isoformat(),
               "start_offset_seconds": _offsets(sample, start, end)[0],
               "end_offset_seconds": _offsets(sample, start, end)[1],
               "manual_count": "", "annotator": ""}
              for sample in samples for start, end, _ in sample["windows"]]
    automatic = [{
        "sample_id": sample["sample_id"], "scenario": sample["scenario"],
        "camera_id": sample["camera_id"], "camera_code": sample["camera_code"],
        "window_start_utc": start.isoformat(), "window_end_utc": end.isoformat(),
        "start_offset_seconds": _offsets(sample, start, end)[0],
        "end_offset_seconds": _offsets(sample, start, end)[1],
        "count_rule": sample["count_rule"], "count_line_y": sample["count_line_y"],
        "model_version": sample["model_version"], "automatic_count": count,
        "report_sha256": sample["report_sha256"],
    } for sample in samples for start, end, count in sample["windows"]]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = [output_dir / "sample_index.csv", output_dir / "manual_counts_template.csv",
               output_dir / "automatic_counts.csv"]
    for target in targets:
        if target.exists():
            raise EvidenceError(f"refusing to overwrite existing evidence: {target}")
    _write_csv(targets[0], INDEX_FIELDS, _index_rows(samples))
    _write_csv(targets[1], MANUAL_FIELDS, manual)
    _write_csv(targets[2], AUTOMATIC_FIELDS, automatic)
    return {"samples": len(samples), "windows": len(manual)}


def _read_manual(path):
    rows = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(MANUAL_REQUIRED).issubset(reader.fieldnames):
            raise EvidenceError("manual CSV must contain the template columns")
        for line_number, row in enumerate(reader, 2):
            sample_id = _required_text(row, "sample_id", f"manual line {line_number}")
            end = _timestamp(row.get("window_end_utc"), f"manual line {line_number}: window_end_utc")
            start = _timestamp(row.get("window_start_utc"), f"manual line {line_number}: window_start_utc")
            key = (sample_id, end)
            if key in rows:
                raise EvidenceError(f"duplicate manual window: {sample_id} {end.isoformat()}")
            text_count = _required_text(row, "manual_count", f"manual line {line_number}")
            if not text_count.isdecimal():
                raise EvidenceError(f"manual line {line_number}: manual_count must be nonnegative integer")
            rows[key] = (start, int(text_count), _required_text(row, "annotator", f"manual line {line_number}"))
    return rows


def evaluate(manifest_path, manual_counts_path, output_dir, prepared_index_path):
    """Compare exact source-time windows and save per-window plus summary data."""
    samples = _load_manifest(manifest_path)
    _verify_prepared_index(prepared_index_path, samples)
    manual = _read_manual(manual_counts_path)
    comparisons = []
    summary = {scenario: {"nonzero_windows": 0, "passed_windows": 0, "failed_windows": 0,
                          "zero_truth_windows": 0, "zero_truth_false_positives": 0}
               for scenario in TARGETS}
    for sample in samples:
        for start, end, automatic in sample["windows"]:
            key = (sample["sample_id"], end)
            if key not in manual:
                raise EvidenceError(f"missing manual count for {sample['sample_id']} {end.isoformat()}")
            manual_start, human, annotator = manual.pop(key)
            if manual_start != start:
                raise EvidenceError(f"{sample['sample_id']}: manual window start differs from report")
            scenario = sample["scenario"]
            absolute = abs(automatic - human)
            if human:
                relative = absolute / human
                passed = relative <= TARGETS[scenario]
                summary[scenario]["nonzero_windows"] += 1
                summary[scenario]["passed_windows" if passed else "failed_windows"] += 1
            else:
                relative = passed = None
                summary[scenario]["zero_truth_windows"] += 1
                summary[scenario]["zero_truth_false_positives"] += automatic
            comparisons.append({
                "sample_id": sample["sample_id"], "scenario": scenario,
                "camera_id": sample["camera_id"], "camera_code": sample["camera_code"],
                "window_start_utc": start.isoformat(), "window_end_utc": end.isoformat(),
                "start_offset_seconds": _offsets(sample, start, end)[0],
                "end_offset_seconds": _offsets(sample, start, end)[1],
                "count_rule": sample["count_rule"], "count_line_y": sample["count_line_y"],
                "model_version": sample["model_version"],
                "annotator": annotator, "manual_count": human, "automatic_count": automatic,
                "absolute_error": absolute,
                "relative_error": "" if relative is None else f"{relative:.6f}",
                "false_positives": automatic if human == 0 else "",
                "target_error": "" if human == 0 else f"{TARGETS[scenario]:.2f}",
                "within_target": "" if passed is None else str(passed).lower(),
                "video_sha256": sample["video_sha256"], "report_sha256": sample["report_sha256"],
            })
    if manual:
        raise EvidenceError(f"extra manual window(s): {len(manual)}")
    summary["sample_count"] = len(samples)
    summary["window_count"] = len(comparisons)
    summary["notice"] = "Operator-supplied permission, recording time and human counts are not independently certified."
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = [output_dir / "sample_index.csv", output_dir / "count_comparison.csv",
               output_dir / "summary.json"]
    for target in targets:
        if target.exists():
            raise EvidenceError(f"refusing to overwrite existing evidence: {target}")
    _write_csv(targets[0], INDEX_FIELDS, _index_rows(samples))
    _write_csv(targets[1], COMPARISON_FIELDS, comparisons)
    targets[2].write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "evaluate"):
        action = actions.add_parser(name)
        action.add_argument("--manifest", type=Path, required=True)
        action.add_argument("--output-dir", type=Path, required=True)
        if name == "evaluate":
            action.add_argument("--manual-counts", type=Path, required=True)
            action.add_argument("--prepared-index", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (prepare(args.manifest, args.output_dir) if args.command == "prepare" else
                  evaluate(args.manifest, args.manual_counts, args.output_dir, args.prepared_index))
    except (EvidenceError, OSError, csv.Error) as exc:
        parser.exit(1, f"Count evidence error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
