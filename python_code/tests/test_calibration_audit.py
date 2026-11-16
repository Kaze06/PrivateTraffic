"""Calibration evidence checks use synthetic rows; no real traffic is bundled."""
from datetime import datetime, timedelta, timezone

import pytest
from calibration_audit import CalibrationError, audit_rows


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def row(sample_id, flow, label, minute=0):
    start = START + timedelta(minutes=minute)
    return {
        "sample_id": sample_id,
        "camera_id": "camera/test",
        "model_version": "model-1",
        "count_rule": "unique_tracks_present",
        "window_start_utc": start.isoformat(),
        "window_end_utc": (start + timedelta(seconds=60)).isoformat(),
        "flow_vpm": str(flow),
        "label": label,
        "label_source": "reviewed_fixture",
        "video_sha256": "a" * 64,
        "report_sha256": "b" * 64,
    }


def test_two_labels_keep_calibration_incomplete_without_inventing_clear():
    result = audit_rows([row("normal", 37, "normal"), row("peak", 247, "congested", 1)])
    assert result["status"] == "insufficient_classes"
    assert result["missing_labels"] == ["clear"]
    assert result["t1_range"] == {"above": None, "at_most": 37.0}
    assert result["t2_range"] == {"above": 37.0, "at_most": 247.0}
    assert "selected_t1" not in result and "selected_t2" not in result


def test_three_separated_labels_give_review_ranges_only():
    result = audit_rows([
        row("clear", 12, "clear"), row("normal", 37, "normal", 1),
        row("peak", 247, "congested", 2),
    ])
    assert result["status"] == "ready_for_review"
    assert result["t1_range"] == {"above": 12.0, "at_most": 37.0}
    assert result["t2_range"] == {"above": 37.0, "at_most": 247.0}
    assert "selected_t1" not in result and "selected_t2" not in result


def test_overlapping_labels_fail_instead_of_returning_thresholds():
    result = audit_rows([
        row("clear", 50, "clear"), row("normal", 37, "normal", 1),
        row("peak", 247, "congested", 2),
    ])
    assert result["status"] == "overlapping_classes"
    assert result["t1_range"] is None


@pytest.mark.parametrize("change", [
    {"window_end_utc": START.isoformat()},
    {"window_start_utc": "2026-01-01T00:00:00"},
    {"flow_vpm": "nan"},
    {"label_source": ""},
    {"model_version": "other-model"},
    {"camera_id": "other-camera"},
    {"video_sha256": "not-a-hash"},
])
def test_invalid_or_mixed_evidence_rejected(change):
    first = row("normal", 37, "normal")
    second = row("peak", 247, "congested", 1)
    second.update(change)
    with pytest.raises(CalibrationError):
        audit_rows([first, second])


def test_duplicate_sample_and_invalid_label_rejected():
    with pytest.raises(CalibrationError, match="duplicate"):
        audit_rows([row("a", 10, "clear"), row("a", 37, "normal", 1)])
    with pytest.raises(CalibrationError, match="label"):
        audit_rows([row("a", 10, "unknown")])
