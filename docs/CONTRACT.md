# First-release shared contract

Source: Task2 FR-01..07 and Task3 sections 2.3, 5.4, 5.7 and 6. Local source documents are under D:/Song/M.U.S.T/2609/SE/Task2 and Task3.

## Measurements and status
Existing POST /api/measurements payload names remain: camera_id, timestamp, vehicle_count, flow_per_minute, is_replay, source_status. Timestamp is now required for valid measurement; never substitute receipt time. New window_seconds defaults to 60 and validity defaults to valid; no_measurement_reason optional. Accept only finite, consistent count/flow/window data. First-release window is 60 seconds. Live timestamps must be timezone-aware, increasing, not future and <=60 seconds old. Invalid input cannot advance last valid time or fire an alert. A late/duplicate submission should be rejected rather than overwrite a valid result.

Camera fields: calibration_t1/t2 nullable, calibration_note nullable (a genuine evidence reference), recording_time nullable, permission_reference nullable. Classification requires valid recorded calibration; arbitrary GET t1/t2 overrides do not establish calibration. Existing GET query parameters can remain accepted but cannot bypass it. Status adds is_replay, recording_time, calibrated, no_measurement_reason. Replay mode is explicit, not inferred solely from filename; legacy uploads remain recognized. Frame freshness is based on usable observations, never writing an overlay file.

Measurement fields add window_seconds, validity, no_measurement_reason. Alert evaluator called from measurement route:
`evaluate_threshold_crossing(db, camera_id, flow_vpm, source_status, is_replay, *, measurement_id=None, measurement_time=None, validity='valid')`.
No event without a valid referenced fresh live measurement and an active page session.

## Alerts
Rules add session_id, last_seen_at and nullable was_above; events add measurement_id. Old rule clients may still create/read/delete rules without session_id, but sessionless rules cannot emit page-session live alerts (Task3 correction). Alert owner defines and documents session open/heartbeat/close transport, scopes all operations by session, and handles disconnect. Prefer explicit connection liveness over unload-only cleanup; document bounded network-loss detection. First baseline is persisted per rule rather than a camera-global in-memory flag. Existing rule/event endpoint paths stay.

Shared schema owner is coordinator. Children may request additional fields before editing shared files. Existing SQLite databases receive additive migration; never delete their data. Application does not certify source permission: pending evidence remains explicit.

## Acceptance
Normal error <=10%, peak <=15%, live freshness <=60s, visible alert <=30s. Real source/sample results are external acceptance dependencies; synthetic UI checks prove only software behavior. Replay recording time and mode are always visible. The first release requires only one active source.
