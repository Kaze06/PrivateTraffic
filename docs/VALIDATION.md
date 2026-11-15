# Integration and review evidence

## Candidate

Local branch: integration/first-release. Compare with 14eacad05dd10515b3e05f8407e843258d652889. No remote push or main merge. Functional candidate through fc840dc; subsequent delivery commit adds documentation and demonstration evidence.

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

The reviewed local software candidate is ready for team inspection. Do not declare the full first release accepted or merge main as a completed release yet. The source permission/timestamp path, permitted samples, real inference and manual count comparison, road calibration and user acceptance remain open in ACCEPTANCE.md. No push or merge is authorized.

Next implementation focus: implement the selected feed's timestamp adapter for run_detector_worker.py after its authoritative metadata format and source permission are supplied. It translates per-frame source metadata into UTC capture time; receipt time cannot substitute for it. In parallel, the source owner supplies permitted recordings with original start times and the measurement owner prepares manual 60-second normal/peak counts.

## View and stop

Dashboard: http://127.0.0.1:8765
Controls: http://127.0.0.1:8765/demo

Keep the dashboard open, enable threshold 25, then use Low 20 followed by High 30 in the controls. Repeat High, use Low to rearm, and try Stopped source, Offline and Recorded mode. Screenshots capture these software scenarios.

The background app is left running. From this repository, run `powershell -File tools/stop-demo.ps1` to stop only the demo process on port 8765. To restart with installed dependencies, run `python tools/demo_server.py`.
