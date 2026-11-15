# Release acceptance record

The local software candidate passes synthetic API and browser checks; independent final review is recorded in VALIDATION.md. Real source and user acceptance must remain distinct from synthetic software checks.

| Gate | Evidence required | Owner | Current state |
|---|---|---|---|
| Source permission | Access, reuse, video retention and aggregate retention permission reference | Chen Jiajun / source owner | Pending external evidence |
| Observation time | Authoritative source timestamp path for chosen feed; UTC display | Data implementation + source owner | Four configured playlists probed: no PROGRAM-DATE-TIME; chosen source needs an authoritative UTC path (SOURCE_TIMING.md) |
| Count accuracy | Manual and automatic counts over matching 60-second normal/peak windows, error formula and sample identity | Yao Ziyan | Pending permitted samples and manual counts |
| Road calibration | T1/T2 and supporting selected-road samples, interpretation review | Yao Ziyan | Pending; UI must show unable_to_determine |
| Freshness and alerts | Source-to-display and crossing-to-alert timings on actual pilot | Song Zian | Synthetic behavior tests only until live pilot exists |
| Replay | Permission, original recording time, not-live label and no live alerts | Chen Jiajun | Replay API/worker setup and browser mode checks passed; permitted clip pending |
| Phone usability | Three users complete road/state/threshold task on phone/laptop | Team | Browser layout check does not substitute user study |
| Retention | Aggregate and replay cleanup by end of evaluation | Team | Purge workflow and retention date to record |

For manual counts, use error = abs(automatic - manual) / manual for nonzero manual count. Record zero-reference windows separately (absolute false counts), rather than dividing by zero. Targets: normal <=10%, peak <=15%. Do not adjust ground truth or thresholds to conceal failed samples.
