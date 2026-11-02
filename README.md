# PrivateTraffic

PrivateTraffic is a Software Engineering course project for Group 13. It proposes a privacy-conscious traffic monitoring and alert service based on public traffic-camera sources.

## Planned user features

- Browse and save public traffic-camera locations.
- View current traffic flow and source availability.
- Automatically produce time-stamped vehicle-count measurements.
- Compare historical traffic trends.
- Configure threshold and time-window alerts.
- Receive web notifications without providing live GPS location.

The initial scope is one to three camera sources from one provider, with permitted recorded video used for repeatable testing when a live source is unavailable. The proposal records acceptance targets for counting error, processing speed, source freshness, alert timing, history accuracy, and privacy behavior.

## Team

- Zian Song 1240008286 (leader)
- Jiajun Chen 1240000551
- Ziyan Yao 1230020566

Individual strengths and functional ownership will be confirmed by the team before implementation.

## C prototype build

The C prototype uses three sibling directories: `yzy_measurement/`, `jiajun_backend/`, and `yzy_trends_alerts/`. The trend and alert module is in this branch. Its CMake file imports the other two modules, so arrange their source files in those directories before building the combined project.

```powershell
cmake -S . -B build -G "MinGW Makefiles"
cmake --build build
ctest --test-dir build --output-on-failure
```

See [`yzy_trends_alerts/README.md`](yzy_trends_alerts/README.md) for rule, dashboard, and pipeline commands. Generated `build/` files and local measurement data are excluded from version control.
