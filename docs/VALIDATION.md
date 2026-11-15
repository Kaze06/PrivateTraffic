# Integration and review evidence

## Candidate

Local branch: integration/first-release. Compare with 14eacad05dd10515b3e05f8407e843258d652889. The user subsequently authorized local main consolidation as a stage baseline; no remote push. Functional candidate through fc840dc; subsequent delivery commit adds documentation and demonstration evidence.

## Independent review

The gpt-6.1-sol reviewer inspected the actual integration candidate and reproduced defects through the real routes. Resolved findings:

- Demo configuration could inherit the production database/upload directory; its own paths are now forced.
- Demo stale injection rewrote observation timestamps; it now changes source state only.
- Normal 60-second source cadence plus receipt delay erased alert baselines; continuity now uses source windows and survives monitor-persisted stale state. Nine focused regressions confirmed continuity, real interruption and replay registration.
- CLI recording metadata did not reach the API; replay configuration now registers before processing.
- Purging an active CLI replay could be undone by a late worker report; retired replay reports now receive 409 and the fixed-source reader stops. Five focused review tests passed, including restored custom URL and cleared recording time. No additional actionable defect was found in that focused review.

## Software evidence

On 2026-11-15, the final integrated candidate passed `python -m pytest -q`: **74 passed, 0 failed**, with 9 Starlette/httpx TestClient deprecation warnings. The environment used the existing progress-review-20261115 virtual environment.

`node tools/check-browser.cjs` passed against the running local server and real WebSocket. Checks: enable, first baseline, crossing, repeated-high suppression, rearm, stale, offline, replay suppression, 390px mobile width, disable, and page close with a re-enabled rule and an established low baseline. No page or browser console errors were recorded. PLAYWRIGHT_MODULE pointed to the installed runtime copy of Playwright.

Screenshots: artifacts/alert-desktop.png, stale-desktop.png, offline-desktop.png, replay-desktop.png and replay-mobile.png. Machine-readable browser result: artifacts/browser-evidence.json.

The demonstration uses its own SQLite database, a clearly labelled synthetic camera, and synthetic measurement inputs. It exercises real API and page-session alert behavior; it does not run RF-DETR, show an actual permitted recording, or establish source timing, count accuracy or real latency.

## Merge decision and next work

The reviewed local software candidate is ready for team inspection. Consolidation into main records the software development baseline; full first-release acceptance remains pending. The source permission/timestamp path, permitted samples, real inference and manual count comparison, road calibration and user acceptance remain open in ACCEPTANCE.md. Local main consolidation is now authorized as a stage baseline; remote push remains unauthorized.

The next timestamp software block is now implemented as opt-in segment-aligned HLS decoding; source-specific activation still needs authoritative metadata and source permission. It translates per-frame source metadata into UTC capture time; receipt time cannot substitute for it. In parallel, the source owner supplies permitted recordings with original start times and the measurement owner prepares manual 60-second normal/peak counts.

## View and stop

Dashboard: http://127.0.0.1:8765
Controls: http://127.0.0.1:8765/demo

Keep the dashboard open, enable threshold 25, then use Low 20 followed by High 30 in the controls. Repeat High, use Low to rearm, and try Stopped source, Offline and Recorded mode. Screenshots capture these software scenarios.

The background app is left running. From this repository, run `powershell -File tools/stop-demo.ps1` to stop only the demo process on port 8765. To restart with installed dependencies, run `python tools/demo_server.py`.

## Source timestamp increment

Candidate af99e3f adds `backend/app/hls_timestamp.py`, integrates the worker reader, and adds 17 focused tests. Full suite after installing pinned PyAV 16.1.0: **91 passed, 0 failed, 9 existing TestClient deprecation warnings**. Tests encode/decode actual synthetic MPEG-TS with reordered frames and verify source UTC/frame identity; no real traffic video was downloaded. Independent gpt-6.1-sol review reran all 17 HLS tests and found no actionable defect.

Metadata-only public probe of C631, C825, C1628 and C8351 returned HTTP 200 but no PROGRAM-DATE-TIME in any selected media playlist. This leaves selected-source live timing acceptance open; permission and the provider's clock semantics are still required. See SOURCE_TIMING.md for supported inputs and the exact source evidence.

This increment changes no frontend files. Prior browser layout/alert evidence remains applicable; the running synthetic demo was not restarted or replaced with a live feed. HLS-specific source results are established by the decoder/worker tests, not by that demo.
