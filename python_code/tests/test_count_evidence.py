"""Synthetic fixtures for the P0-2 evidence preparation tool."""

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from count_evidence import EvidenceError, evaluate, prepare


class CountEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "permitted_fixture.y4m"
        self.video.write_bytes(b"synthetic fixture only")
        self.reports = self.root / "reports.jsonl"
        self.reports.write_text(json.dumps({
            "camera_id": "cam/test", "timestamp": "2020-01-01T00:01:00Z",
            "vehicle_count": 11, "window_seconds": 60, "validity": "valid",
            "source_status": "replay_not_live", "is_replay": True,
        }) + "\n", encoding="utf-8")
        self.sample = {
            "sample_id": "normal_01", "scenario": "normal",
            "video_path": self.video.name, "reports_path": self.reports.name,
            "camera_id": "cam/test", "camera_code": "TEST",
            "recording_start": "2020-01-01T00:00:00Z", "count_rule": "downward_line_crossing",
            "count_line_y": 22,
            "model_version": "baseline@fb09864", "permission_reference": "source-owner approval 1",
            "retention_terms": "retain until 2020-02-01",  # Fixture metadata, not a real grant.
        }
        self.manifest = self.root / "manifest.json"
        self.save_manifest()

    def save_manifest(self, samples=None):
        self.manifest.write_text(json.dumps({"samples": samples if samples is not None else [self.sample]}),
                                 encoding="utf-8")

    def manual_file(self, rows):
        path = self.root / "manual_counts.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["sample_id", "window_end_utc", "window_start_utc",
                                                        "manual_count", "annotator"])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def prepared_index(self):
        directory = self.root / "prepared"
        prepare(self.manifest, directory)
        return directory / "sample_index.csv"

    def test_prepare_indexes_files_and_aligns_manual_sheet_to_report(self):
        out = self.root / "evidence"
        prepare(self.manifest, out)
        with (out / "sample_index.csv").open(newline="", encoding="utf-8") as stream:
            row = list(csv.DictReader(stream))[0]
        self.assertEqual(row["video_sha256"], hashlib.sha256(self.video.read_bytes()).hexdigest())
        self.assertEqual(row["report_sha256"], hashlib.sha256(self.reports.read_bytes()).hexdigest())
        self.assertEqual(row["window_count"], "1")
        self.assertEqual(row["count_rule"], "downward_line_crossing")
        with (out / "manual_counts_template.csv").open(newline="", encoding="utf-8") as stream:
            row = list(csv.DictReader(stream))[0]
        self.assertEqual(row["window_start_utc"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(row["window_end_utc"], "2020-01-01T00:01:00+00:00")
        self.assertEqual(row["start_offset_seconds"], "0.000")
        self.assertEqual(row["end_offset_seconds"], "60.000")
        self.assertEqual(row["manual_count"], "")

    def test_prepare_does_not_partially_write_when_output_exists(self):
        out = self.root / "evidence"
        out.mkdir()
        (out / "manual_counts_template.csv").write_text("do not overwrite", encoding="utf-8")
        with self.assertRaisesRegex(EvidenceError, "overwrite"):
            prepare(self.manifest, out)
        self.assertFalse((out / "sample_index.csv").exists())
        self.assertEqual((out / "manual_counts_template.csv").read_text(encoding="utf-8"),
                         "do not overwrite")

    def test_prepare_rejects_missing_permission_and_naive_recording_time(self):
        self.sample["permission_reference"] = ""
        self.save_manifest()
        with self.assertRaisesRegex(EvidenceError, "permission_reference"):
            prepare(self.manifest, self.root / "out")
        self.sample["permission_reference"] = "approval"
        self.sample["recording_start"] = "2020-01-01T00:00:00"
        self.save_manifest()
        with self.assertRaisesRegex(EvidenceError, "recording_start"):
            prepare(self.manifest, self.root / "out")

    def test_prepare_records_product_worker_rule_without_a_count_line(self):
        self.sample["count_rule"] = "unique_tracks_present"
        self.sample["count_line_y"] = None
        self.save_manifest()
        out = self.root / "out"
        prepare(self.manifest, out)
        with (out / "sample_index.csv").open(newline="", encoding="utf-8-sig") as stream:
            row = list(csv.DictReader(stream))[0]
        self.assertEqual(row["count_rule"], "unique_tracks_present")
        self.assertEqual(row["count_line_y"], "")

    def test_prepare_requires_line_for_downward_crossing(self):
        self.sample["count_line_y"] = None
        self.save_manifest()
        with self.assertRaisesRegex(EvidenceError, "count_line_y"):
            prepare(self.manifest, self.root / "out")

    def test_prepare_rejects_wrong_camera_and_duplicate_report_windows(self):
        original = self.reports.read_text(encoding="utf-8")
        report = json.loads(original)
        report["camera_id"] = "other"
        self.reports.write_text(json.dumps(report) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(EvidenceError, "camera_id"):
            prepare(self.manifest, self.root / "out")
        self.reports.write_text(original + original, encoding="utf-8")
        with self.assertRaisesRegex(EvidenceError, "duplicate"):
            prepare(self.manifest, self.root / "out")

    def test_prepare_rejects_overlapping_automatic_windows(self):
        second = json.loads(self.reports.read_text(encoding="utf-8"))
        second["timestamp"] = "2020-01-01T00:01:30Z"
        self.reports.write_text(self.reports.read_text(encoding="utf-8") + json.dumps(second) + "\n",
                                encoding="utf-8")
        with self.assertRaisesRegex(EvidenceError, "overlapping"):
            prepare(self.manifest, self.root / "out")

    def test_evaluate_calculates_nonzero_and_zero_reference_separately(self):
        second = dict(json.loads(self.reports.read_text(encoding="utf-8")))
        second.update(timestamp="2020-01-01T00:02:00Z", vehicle_count=3)
        self.reports.write_text(self.reports.read_text(encoding="utf-8") + json.dumps(second) + "\n",
                                encoding="utf-8")
        manual = self.manual_file([
            {"sample_id": "normal_01", "window_start_utc": "2020-01-01T00:00:00+00:00",
             "window_end_utc": "2020-01-01T00:01:00+00:00", "manual_count": "10", "annotator": "A"},
            {"sample_id": "normal_01", "window_start_utc": "2020-01-01T00:01:00+00:00",
             "window_end_utc": "2020-01-01T00:02:00+00:00", "manual_count": "0", "annotator": "A"},
        ])
        out = self.root / "evidence"
        summary = evaluate(self.manifest, manual, out, self.prepared_index())
        with (out / "count_comparison.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["relative_error"], "0.100000")
        self.assertEqual(rows[0]["within_target"], "true")
        self.assertEqual(rows[1]["relative_error"], "")
        self.assertEqual(rows[1]["false_positives"], "3")
        self.assertEqual(rows[1]["within_target"], "")
        self.assertEqual(summary["normal"]["nonzero_windows"], 1)
        self.assertEqual(summary["normal"]["zero_truth_false_positives"], 3)

    def test_evaluate_rejects_missing_or_duplicate_manual_rows(self):
        manual = self.manual_file([])
        index = self.prepared_index()
        with self.assertRaisesRegex(EvidenceError, "missing manual"):
            evaluate(self.manifest, manual, self.root / "out", index)
        row = {"sample_id": "normal_01", "window_start_utc": "2020-01-01T00:00:00+00:00",
               "window_end_utc": "2020-01-01T00:01:00+00:00", "manual_count": "10", "annotator": "A"}
        self.manual_file([row, row])
        with self.assertRaisesRegex(EvidenceError, "duplicate manual"):
            evaluate(self.manifest, manual, self.root / "out", index)

    def test_evaluate_reports_peak_failure_without_changing_truth(self):
        self.sample["scenario"] = "peak"
        self.save_manifest()
        manual = self.manual_file([{
            "sample_id": "normal_01", "window_start_utc": "2020-01-01T00:00:00+00:00",
            "window_end_utc": "2020-01-01T00:01:00+00:00", "manual_count": "8", "annotator": "A",
        }])
        summary = evaluate(self.manifest, manual, self.root / "out", self.prepared_index())
        self.assertEqual(summary["peak"]["failed_windows"], 1)
        with (self.root / "out" / "count_comparison.csv").open(newline="", encoding="utf-8") as stream:
            row = list(csv.DictReader(stream))[0]
        self.assertEqual(row["relative_error"], "0.375000")
        self.assertEqual(row["within_target"], "false")

    def test_evaluate_rejects_changed_video_after_manual_sheet_prepared(self):
        index = self.prepared_index()
        manual = self.manual_file([{
            "sample_id": "normal_01", "window_start_utc": "2020-01-01T00:00:00+00:00",
            "window_end_utc": "2020-01-01T00:01:00+00:00", "manual_count": "10", "annotator": "A",
        }])
        self.video.write_bytes(b"different content")
        with self.assertRaisesRegex(EvidenceError, "prepared sample index differs"):
            evaluate(self.manifest, manual, self.root / "out", index)


if __name__ == "__main__":
    unittest.main()
