import os
import subprocess
import sys
import sqlite3
from pathlib import Path


def test_synthetic_seed_is_separate_idempotent_and_non_live(tmp_path):
    db = tmp_path / "demo.db"
    env = dict(os.environ, DATABASE_PATH=str(db), UPLOAD_DIR=str(tmp_path / "uploads"))
    script = Path(__file__).resolve().parents[1] / "seed_mock_data.py"
    for _ in range(2):
        subprocess.run([sys.executable, str(script)], env=env, check=True, capture_output=True)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM camera_sources").fetchone()[0] == 1
        assert connection.execute("SELECT current_status FROM camera_sources").fetchone()[0] == "replay_not_live"
        assert connection.execute("SELECT count(*) FROM traffic_measurements WHERE is_replay=1 AND source_status='replay_not_live'").fetchone()[0] == 60
        assert connection.execute("SELECT count(*) FROM alert_rules").fetchone()[0] == 0
