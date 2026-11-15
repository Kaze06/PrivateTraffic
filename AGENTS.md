# PrivateTraffic development agreement

- First release follows Task2/Task3: one permitted active camera, 60-second vehicle measurements, source observation time, explicit source state, calibrated traffic level and page-session threshold alerts. History charts, closed-page notifications and simultaneous cameras are deferred.
- Live input requires increasing UTC observation timestamps and age <=60 seconds. Invalid frames, inference failures and replay must never manufacture fresh live results. Missing calibration means `unable_to_determine`.
- Alert: first valid observation establishes a baseline; below-to-at/above threshold fires once; sustained high does not repeat; below re-arms. Replay/stale/offline/recovering/closed sessions produce no live events.
- Read docs/CONTRACT.md before editing interfaces. Keep existing endpoint paths and update callers together. Explicitly document incompatible corrections required by Task3.
- Ownership: coordinator owns models.py, schemas.py, database.py, main.py, config.py, dependencies and docs. Data owner owns detector, worker, measurement/camera routes and health monitor. Alert owner owns alert route and frontend. Request shared changes from coordinator.
- Each owner uses an isolated worktree and test data. Never reset another owner's changes. Local commits are authorized. The coordinator is authorized to integrate the reviewed stage baseline into local main; remote push remains unauthorized. Develop subsequent changes on isolated branches and merge only within the user's current authorization.
- Test meaningful public behavior with `python -m pytest -q`; regression checks must reproduce the former fault. Exercise changed UI in a browser and save screenshots. Do not claim real accuracy/latency from synthetic inputs.
- Deliver commit IDs, changed behavior, commands/results and unresolved external dependencies. The coordinator integrates and an independent reviewer checks the actual candidate.

| Rule | Enforcement |
|---|---|
| Freshness and crossing semantics | API regression tests and integrated failure scenarios |
| Visible behavior | Browser interaction checks and desktop/mobile screenshots |
