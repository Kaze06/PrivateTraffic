# Release acceptance record

[P1-6 handoff](P1_6_HANDOFF.md) records the measurement branch's replay evidence. The integrated dashboard keeps that branch's default tracker; the adaptive tracker is an opt-in offline comparison and has not been evaluated on those recordings. Treat the historical targets below as evaluation references, separate from software integration.

| Gate | Evidence required | Owner | Current state |
|---|---|---|---|
| Source permission | Access, reuse, video retention and aggregate retention permission reference | Chen Jiajun / source owner | Replay-use terms recorded in the handoff; selected live use still needs source evidence |
| Observation time | Clearly labelled UTC time basis for each live measurement | Data implementation + source owner | `system_received` mode is implemented; verify the dashboard label and measure frame-receipt-to-display and threshold-to-alert timing on the selected pilot |
| Detection view lifecycle | Enter Detection view to start the selected live camera; return to Raw video to stop | Software implementation | Backend-managed worker and browser controls pass automated lifecycle tests; model and live-feed startup need a pilot run |
| Count accuracy | Manual and automatic counts over matching 60-second normal/peak windows, error formula and sample identity | Yao Ziyan | Prior handoff records selected-window targets met; no new clip comparison or adaptive-tracker accuracy result |
| Road calibration | T1/T2 and supporting selected-road samples, interpretation review | Yao Ziyan | C8351 provisional T1=33/T2=142; clear label and additional samples need review |
| Freshness and alerts | Receipt-to-display and threshold-to-alert timings on actual pilot | Song Zian | Software behavior tests pass; pilot timing measurements remain to be recorded |
| Replay | Permission, original recording time, not-live label and no live alerts | Chen Jiajun | Handoff records permitted clips and original recording times; replay API and prior browser checks cover mode and alert isolation |
| Phone usability | Three users complete road/state/threshold task on phone/laptop | Team | Browser layout check does not substitute user study |
| Retention | Aggregate and replay cleanup by end of evaluation | Team | Purge workflow and retention date to record |

For manual counts, use error = abs(automatic - manual) / manual for nonzero manual count. Record zero-reference windows separately (absolute false counts), rather than dividing by zero. Targets: normal <=10%, peak <=15%. Do not adjust ground truth or thresholds to conceal failed samples.
