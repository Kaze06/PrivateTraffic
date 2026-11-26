# PrivateTraffic development agreement

- The current development baseline supports one active camera, rolling measurements up to 60 seconds, explicit time basis and source state, road levels and page-session threshold alerts. Task2/Task3 are historical references; update the repository contract when behavior changes. History charts, closed-page notifications and simultaneous cameras are deferred.
- Live input may use increasing UTC source-observation time or the explicit `--receipt-time` mode, which timestamps each frame when the worker reads it. Both must be no more than 60 seconds old. Invalid frames, inference failures and replay must never manufacture fresh live results. Missing calibration means `unable_to_determine`.
- Alert: first valid observation establishes a baseline; below-to-at/above threshold fires once; sustained high does not repeat; below re-arms. Replay/stale/offline/recovering/closed sessions produce no live events.
- Read docs/CONTRACT.md before editing interfaces. Keep existing endpoint paths and update callers together. Document intentional compatibility changes and their effects.
- Ownership: coordinator owns models.py, schemas.py, database.py, main.py, config.py, dependencies and docs. Data owner owns detector, worker, measurement/camera routes and health monitor. Alert owner owns alert route and frontend. Request shared changes from coordinator.
- Each owner uses an isolated worktree and test data. Never reset another owner's changes. Local commits are authorized. The user authorized consolidation of the reviewed stage baseline into main and a normal push to origin/main after checks on 2026-11-15. Develop subsequent changes on isolated branches and merge only within the user's current authorization.
- Test meaningful public behavior with `python -m pytest -q`; regression checks must reproduce the former fault. Exercise changed UI in a browser and save screenshots. Do not claim real accuracy/latency from synthetic inputs.
- Deliver commit IDs, changed behavior, commands/results and unresolved external dependencies. The coordinator integrates and an independent reviewer checks the actual candidate.

| Rule | Enforcement |
|---|---|
| Freshness and crossing semantics | API regression tests and integrated failure scenarios |
| Visible behavior | Browser interaction checks and desktop/mobile screenshots |
