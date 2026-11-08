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

## Quickstart
### 1. Prerequisites & Installation
Ensure you have Python 3.10+ installed, then install the required dependencies:
```bash
pip install -r requirements.txt
```

### 2. Initializing the Database & Mock Data
To seed initial mock traffic history records into SQLite (`traffic.db`) for testing and demonstrations without requiring the CV model to be running continuously:
```bash
python seed_mock_data.py
```

### 3. Running the Application Server
Navigate to the backend directory and launch the FastAPI server (it serves both the REST API and the responsive browser dashboard on port 8000):
```bash
# cd /mnt/d/2609/PrivateTraffic && pytest -v

# Terminal 1 (Backend)
cd /mnt/d/2609/PrivateTraffic/backend && uvicorn app.main:app --reload --port 8000

# Terminal 2 (Worker)
cd /mnt/d/2609/PrivateTraffic && python run_detector_worker.py
```
Open **`http://localhost:8000`** in your phone or laptop browser.

### 4. Integration Guide
- **For (CV / Detection Pipeline):** Run your local video detection worker. It will automatically process MnDOT streams (or local replay files) and push aggregate 1-minute vehicle counts to:
  `POST /api/measurements`
- **For (Charts & Alerts):** Fetch historical aggregate buckets for comparison graphs and trend analysis via:
  `GET /api/history?camera_id=<id>&start_time=<iso>&end_time=<iso>`
- **For (Location & Current Status):** Manages the OpenStreetMap catalog, GPS-free session persistence (`localStorage`), live HLS/MP4 media streaming, and source health monitoring (`live_fresh`, `stale`, `offline`, `replay_not_live`).
