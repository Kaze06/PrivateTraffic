# First-release task record

Baseline: 14eacad05dd10515b3e05f8407e843258d652889 (remote checked 2026-11-15).

| Owner | Task | State | Evidence |
|---|---|---|---|
| Coordinator | Shared contract, additive schema migration, startup dependencies | Implemented | Migration regression; AGENTS.md; CONTRACT.md |
| Data agent (gpt-6.1-sol) | Timestamp gate, frame freshness, failure, vehicle classes, count/calibration/replay | Implemented; cadence, CLI registration and purge lifecycle fixes verified | tests/test_data_*.py |
| Alert agent (gpt-6.1-sol) | Baseline/crossing, session lifecycle and page UI | Implemented; cadence repair and readable status hints verified | tests/test_alert_sessions.py |
| Independent reviewer (gpt-6.1-sol) | Actual integrated diff | Completed; reproduced blockers repaired and independently rechecked | docs/VALIDATION.md |
| Coordinator | Integrated tests, demo app, screenshots, handoff | 91 tests passed after HLS timing addition; prior unchanged UI evidence retained | tools/check-browser.cjs; artifacts/ |
| Human/source owner | Permission evidence, permitted recordings and true recording time | External dependency | No evidence supplied yet |
| Team | Manual normal/peak counts, road calibration, live latency, customer review | External dependency | docs/ACCEPTANCE.md |

History charts, simultaneous multi-camera monitoring and closed-page notifications remain deferred. The user authorized main integration and publication of the checked stage baseline to origin/main on 2026-11-15.

## Source timestamp block (2026-11-15)

- Data owner implemented opt-in segment-aligned PDT decoding and retained custom adapter/replay entry points (af99e3f).
- Coordinator probed four configured playlists: HTTP 200, all missing PDT. Exact metadata evidence: artifacts/source-timestamp-probe.json.
- Integrated suite: 91 passed with PyAV 16.1.0; independent gpt-6.1-sol review passed 17 HLS checks and found no actionable defect.
- Source owner must provide an allowed feed with documented observation-clock semantics before real live acceptance. See SOURCE_TIMING.md. Existing demo remains a synthetic scenario application.

## Main consolidation

The user elected to consolidate the reviewed candidate into local main before further development. This is a development baseline, with the real-source acceptance gates in ACCEPTANCE.md still open. The previous main history is retained, including its reverted C prototype; the Python application is the continuing implementation. Future development starts from this consolidated baseline on isolated branches.

## Receipt-time live mode (2026-11-26)

Implemented on branch `feat/live-receipt-time-counting`, based on `2268ec0`. Run the worker with `--receipt-time` to timestamp live frames when read, publish rolling counts every 5 seconds, and preserve that time basis in API, history, and dashboard. Source-observation and replay windows retain their existing 60-second contract. Full-suite and pilot timing evidence are recorded in `docs/VALIDATION.md` and `docs/ACCEPTANCE.md`.

## Detection view auto-start (2026-11-26)

Detection view now starts or reuses the backend-managed receipt-time worker, shows its startup/failure state, and stops it when switching to Raw video or when the API shuts down. The worker follows the selected camera through the existing active-focus endpoint. See `docs/VALIDATION.md` for lifecycle test and browser evidence.
