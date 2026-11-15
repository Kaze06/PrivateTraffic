# First-release task record

Baseline: 14eacad05dd10515b3e05f8407e843258d652889 (remote checked 2026-11-15).

| Owner | Task | State | Evidence |
|---|---|---|---|
| Coordinator | Shared contract, additive schema migration, startup dependencies | Implemented | Migration regression; AGENTS.md; CONTRACT.md |
| Data agent (gpt-6.1-sol) | Timestamp gate, frame freshness, failure, vehicle classes, count/calibration/replay | Implemented; cadence, CLI registration and purge lifecycle fixes verified | tests/test_data_*.py |
| Alert agent (gpt-6.1-sol) | Baseline/crossing, session lifecycle and page UI | Implemented; cadence repair and readable status hints verified | tests/test_alert_sessions.py |
| Independent reviewer (gpt-6.1-sol) | Actual integrated diff | Completed; reproduced blockers repaired and independently rechecked | docs/VALIDATION.md |
| Coordinator | Integrated tests, demo app, screenshots, handoff | 74 tests passed; browser scenarios and screenshots verified | tools/check-browser.cjs; artifacts/ |
| Human/source owner | Permission evidence, permitted recordings and true recording time | External dependency | No evidence supplied yet |
| Team | Manual normal/peak counts, road calibration, live latency, customer review | External dependency | docs/ACCEPTANCE.md |

History charts, simultaneous multi-camera monitoring and closed-page notifications remain deferred. No push or main merge is authorized.
