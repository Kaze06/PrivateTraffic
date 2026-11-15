let map = null;
let defaultCenter = null;
let defaultZoom = null;
let markersMap = {};
let allCameras = [];
let currentFilter = "all";
let currentCameraId = null;
let currentStatusTimer = null;
let hlsInstance = null;
let augmentedWs = null;
let currentVideoMode = "raw";

function getFollowedList() {
  try {
    return JSON.parse(localStorage.getItem("pt_followed_cams") || "[]");
  } catch (e) {
    return [];
  }
}

function setFollowedList(list) {
  localStorage.setItem("pt_followed_cams", JSON.stringify(list));
  const countEl = document.getElementById("follow-count");
  if (countEl) countEl.innerText = list.length;
}

function isCameraFollowed(camId) {
  return getFollowedList().includes(camId);
}

function resetMapView() {
  if (defaultCenter && defaultZoom !== null && map) {
    map.setView(defaultCenter, defaultZoom, { animate: false });
  }
}

function initMap() {
  map = L.map("map");
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "© OpenStreetMap contributors"
  }).addTo(map);

  const resetControl = L.control({ position: "topleft" });
  resetControl.onAdd = function () {
    const div = L.DomUtil.create("div", "leaflet-bar leaflet-control");
    div.innerHTML = '<a href="#" title="Reset Map View" role="button" style="display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:bold;cursor:pointer;background:#fff;text-decoration:none;color:#333;width:30px;height:30px;line-height:30px;">⟲</a>';
    
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.disableScrollPropagation(div);

    div.onclick = function (e) {
      e.preventDefault();
      e.stopPropagation();
      resetMapView();
    };
    return div;
  };
  resetControl.addTo(map);
}

function formatIso(isoString) {
  if (!isoString) return "Not available";
  try {
    const utcValue = /(?:Z|[+-]\d{2}:\d{2})$/i.test(isoString) ? isoString : `${isoString}Z`;
    const d = new Date(utcValue);
    if (isNaN(d.getTime())) return isoString;
    return d.toISOString().replace("T", " ").substring(0, 19) + " UTC";
  } catch (e) {
    return isoString;
  }
}

async function loadCameras() {
  try {
    const res = await fetch("/api/cameras");
    if (!res.ok) throw new Error("Failed to load camera sources");
    allCameras = await res.json();
    renderMarkers();
    renderCameraList();

    const validCoords = allCameras
      .filter(c => c.latitude && c.longitude)
      .map(c => [c.latitude, c.longitude]);
    if (validCoords.length > 0 && map) {
      const bounds = L.latLngBounds(validCoords);
      map.fitBounds(bounds, { padding: [20, 20], animate: false, maxZoom: 12 });
      defaultZoom = Math.min(map.getZoom() + 1, 12);
      defaultCenter = bounds.getCenter();
      map.setView(defaultCenter, defaultZoom, { animate: false });
    }

    const pilotCam = allCameras.find(c => c.is_default) || allCameras.find(c => c.is_pilot) || allCameras[0];
    if (pilotCam) {
      selectCamera(pilotCam.id, false);
    }
  } catch (err) {
    document.getElementById("camera-list").innerText = "Failed loading cameras.";
  }
}

function renderMarkers() {
  Object.values(markersMap).forEach(m => map.removeLayer(m));
  markersMap = {};

  const showPilotSpecial = (currentFilter === "pilot");

  allCameras.forEach(cam => {
    if (!cam.latitude || !cam.longitude) return;
    const isSpecial = showPilotSpecial && cam.is_pilot;

    const marker = L.circleMarker([cam.latitude, cam.longitude], {
      radius: isSpecial ? 7.5 : 4.5,
      fillColor: isSpecial ? "#2563eb" : "#64748b",
      color: "#ffffff",
      weight: 1.2,
      opacity: 1,
      fillOpacity: 0.85
    });

    marker.bindTooltip(`<b>${cam.title}</b>`, { direction: "top" });
    marker.on("click", () => selectCamera(cam.id, true));
    marker.addTo(map);
    markersMap[cam.id] = marker;
  });
}

function renderCameraList() {
  const container = document.getElementById("camera-list");
  const searchVal = (document.getElementById("search-box").value || "").toLowerCase();
  const followed = getFollowedList();

  const filtered = allCameras.filter(c => {
    const matchesSearch = c.title.toLowerCase().includes(searchVal) || (c.road_name && c.road_name.toLowerCase().includes(searchVal));
    if (!matchesSearch) return false;
    if (currentFilter === "pilot") return c.is_pilot;
    if (currentFilter === "followed") return followed.includes(c.id);
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = `<div style="font-size:0.8rem; color:#94a3b8; padding:8px;">No cameras match current filter.</div>`;
    return;
  }

  container.innerHTML = filtered.map(c => `
    <div class="camera-item ${c.id === currentCameraId ? 'selected' : ''}" onclick="selectCamera('${encodeURIComponent(c.id)}', true)">
      <div>
        <div style="font-weight:600;">${c.title}</div>
        <div style="font-size:0.7rem; color:#64748b;">${c.road_name || 'Highway'} · ${c.camera_code || 'ID: ' + c.id}</div>
      </div>
      <div>
        <span class="badge ${c.is_pilot ? 'badge-live' : 'badge-unavailable'}">${c.is_pilot ? 'Pilot' : 'Feed'}</span>
      </div>
    </div>
  `).join("");
}

async function selectCamera(camId, panTo = true) {
  camId = decodeURIComponent(camId);
  currentCameraId = camId;
  refreshAlerts();
  renderCameraList();

  const cam = allCameras.find(c => c.id === camId);
  if (cam && map && panTo) {
    map.flyTo([cam.latitude, cam.longitude], 13, { duration: 0.6 });
    if (markersMap[camId]) {
      markersMap[camId].openTooltip();
    }
  }

  if (cam && cam.camera_code) {
    const codeInput = document.getElementById("upload-cam-code");
    if (codeInput) codeInput.value = cam.camera_code;
  }

  try {
    await fetch("/api/cameras/active-focus", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_id: camId })
    });
  } catch (e) {}

  const followBtn = document.getElementById("btn-toggle-follow");
  if (followBtn) {
    followBtn.style.display = "block";
    updateFollowButtonState();
  }

  const videoArea = document.getElementById("video-display-area");
  if (videoArea) videoArea.style.display = cam && cam.stream_url ? "block" : "none";

  await fetchAndUpdateStatus();

  if (currentStatusTimer) clearInterval(currentStatusTimer);
  currentStatusTimer = setInterval(fetchAndUpdateStatus, 2500);

  setupVideoPlayer(cam);
}

function updateFollowButtonState() {
  const followBtn = document.getElementById("btn-toggle-follow");
  if (!followBtn || !currentCameraId) return;
  const isF = isCameraFollowed(currentCameraId);
  followBtn.innerText = isF ? "Unfollow this Location" : "Follow this Location";
  followBtn.className = isF ? "follow-btn followed" : "follow-btn";
}

async function fetchAndUpdateStatus() {
  if (!currentCameraId) return;
  const requestedCamera = currentCameraId;

  try {
    const res = await fetch(`/api/cameras/${encodeURIComponent(requestedCamera)}/status`);
    if (!res.ok) throw new Error("Status unavailable");
    const data = await res.json();
    if (requestedCamera !== currentCameraId) return;
    renderStatusData(data);
    refreshAlerts();
  } catch (e) {
    if (requestedCamera !== currentCameraId) return;
    const camera = allCameras.find(c => c.id === requestedCamera);
    renderStatusData({camera_title: camera ? camera.title : "Selected location", status: "unavailable",
      no_measurement_reason: "Cannot reach source status. Waiting to reconnect."});
  }
}

function renderStatusData(data) {
  document.getElementById("card-cam-title").innerText = data.camera_title;

  const statusBadge = document.getElementById("card-cam-badge");
  statusBadge.className = "badge";

  let badgeText = data.status || "Unknown";
  if (!data.is_replay && (data.status === "live" || data.status === "live_fresh")) {
    statusBadge.classList.add("badge-live");
    badgeText = "Live";
  } else if (data.status === "stale") {
    statusBadge.classList.add("badge-stale");
    badgeText = "Stale (Inactive)";
  } else if (data.status === "recovering") {
    statusBadge.classList.add("badge-recovering");
    badgeText = "Recovering";
  } else if (data.status === "offline") {
    statusBadge.classList.add("badge-offline");
    badgeText = "Offline";
  } else if (data.is_replay || ["replay", "replay_not_live"].includes(data.status)) {
    statusBadge.classList.add("badge-replay");
    badgeText = "Replay / Not Live";
  } else {
    statusBadge.classList.add("badge-unavailable");
  }
  statusBadge.innerText = `State: ${badgeText}`;

  const streamStateBadge = document.getElementById("badge-stream-state");
  if (data.has_augmented_stream) {
    streamStateBadge.className = "badge badge-stream-active";
    streamStateBadge.innerText = "● RF-DETR: Active";
  } else {
    streamStateBadge.className = "badge badge-stream-idle";
    streamStateBadge.innerText = "○ RF-DETR: Standby";
  }

  const isReplay = Boolean(data.is_replay) || ["replay", "replay_not_live"].includes(data.status);
  const isLive = !isReplay && ["live", "live_fresh"].includes(data.status);
  const liveIndicator = document.getElementById("live-indicator-overlay");
  if (liveIndicator) {
    liveIndicator.style.display = isLive ? "flex" : "none";
  }

  const vidStatusTag = document.getElementById("vid-status-tag");
  if (vidStatusTag) {
    vidStatusTag.innerText = isReplay ? "Replay · Not live" : (isLive ? "Live source" : `Source: ${badgeText}`);
    vidStatusTag.style.color = isReplay ? "#c084fc" : (isLive ? "#22c55e" : "#fbbf24");
  }

  const isSuppressed = !isReplay && !isLive;
  const recording = document.getElementById("recording-time");
  recording.hidden = !isReplay;
  recording.textContent = `Recording time: ${formatIso(data.recording_time)}`;
  document.getElementById("source-reason").textContent = data.no_measurement_reason || "";
  document.getElementById("calibration-note").textContent = data.calibrated
    ? "Traffic level uses this source’s recorded calibration."
    : "Traffic level unavailable: calibration has not been recorded.";

  document.getElementById("val-count").innerText = (!isSuppressed && data.vehicle_count !== null && data.vehicle_count !== undefined) ? data.vehicle_count : "--";
  document.getElementById("val-flow").innerText = (!isSuppressed && data.flow_per_minute !== null && data.flow_per_minute !== undefined) ? Number(data.flow_per_minute).toFixed(1) : "--";

  const levelVal = document.getElementById("val-level");
  levelVal.className = "metric-val";

  if (isSuppressed || data.traffic_level === "unable_to_determine" || !data.traffic_level) {
    levelVal.innerText = "Unavailable";
    levelVal.classList.add("level-suppressed");
  } else {
    const raw = String(data.traffic_level).toLowerCase();
    levelVal.innerText = raw.charAt(0).toUpperCase() + raw.slice(1);
    if (raw === "clear") levelVal.classList.add("level-clear");
    else if (raw === "normal") levelVal.classList.add("level-normal");
    else if (raw === "congested") levelVal.classList.add("level-congested");
  }

  const measTimeEl = document.getElementById("val-updated");
  measTimeEl.innerText = `Measurement Time: ${formatIso(data.measurement_time)}`;
  if (data.status === "stale") {
    measTimeEl.classList.add("stale-warning");
  } else {
    measTimeEl.classList.remove("stale-warning");
  }

  const lastValidEl = document.getElementById("val-last-valid");
  if (lastValidEl) {
    lastValidEl.innerText = `Last Valid Time: ${formatIso(data.last_valid_time)}`;
    if (data.status === "offline" || data.status === "stale") {
      lastValidEl.classList.add("stale-warning");
    } else {
      lastValidEl.classList.remove("stale-warning");
    }
  }
}

function setupVideoPlayer(cam) {
  const videoEl = document.getElementById("raw-video-player");
  const overlayCanvas = document.getElementById("detection-overlay-canvas");
  const cvOfflineNotice = document.getElementById("cv-offline-notice");

  if (hlsInstance) {
    hlsInstance.destroy();
    hlsInstance = null;
  }
  if (augmentedWs) {
    augmentedWs.close();
    augmentedWs = null;
  }

  videoEl.pause();
  videoEl.removeAttribute("src");
  videoEl.load();
  if (!cam || !cam.stream_url) {
    document.getElementById("video-display-area").style.display = "none";
    return;
  }
  document.getElementById("video-display-area").style.display = "block";

  videoEl.onerror = () => {
    document.getElementById("live-indicator-overlay").style.display = "none";
    document.getElementById("vid-status-tag").textContent = "Video unavailable";
  };
  if (currentVideoMode === "raw") {
    overlayCanvas.style.display = "none";
    videoEl.style.display = "block";
    cvOfflineNotice.style.display = "none";

    if (Hls.isSupported() && cam.stream_url.endsWith(".m3u8")) {
      hlsInstance = new Hls({ enableWorker: true, lowLatencyMode: true });
      hlsInstance.loadSource(cam.stream_url);
      hlsInstance.attachMedia(videoEl);
      hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => videoEl.play().catch(() => {}));
    } else {
      videoEl.src = cam.stream_url;
      videoEl.play().catch(() => {});
    }
  } else {
    videoEl.pause();
    videoEl.style.display = "none";
    overlayCanvas.style.display = "block";

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${location.host}/api/cameras/${encodeURIComponent(cam.id)}/ws`;
    augmentedWs = new WebSocket(wsUrl);
    augmentedWs.binaryType = "blob";

    const ctx = overlayCanvas.getContext("2d");
    augmentedWs.onmessage = async (evt) => {
      cvOfflineNotice.style.display = "none";
      const blob = evt.data;
      const bmp = await createImageBitmap(blob);
      ctx.drawImage(bmp, 0, 0, overlayCanvas.width, overlayCanvas.height);
    };

    augmentedWs.onclose = () => {
      if (currentVideoMode === "augmented") {
        cvOfflineNotice.style.display = "flex";
      }
    };
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  loadCameras();

  setFollowedList(getFollowedList());

  document.getElementById("search-box").addEventListener("input", renderCameraList);

  document.getElementById("tab-all").addEventListener("click", (e) => {
    setActiveTab(e.target, "all");
  });
  document.getElementById("tab-pilot").addEventListener("click", (e) => {
    setActiveTab(e.target, "pilot");
  });
  document.getElementById("tab-followed").addEventListener("click", (e) => {
    setActiveTab(e.target, "followed");
  });

  connectAlerts();
  document.getElementById("alert-form").addEventListener("submit", enableAlerts);
  document.getElementById("alert-disable").addEventListener("click", disableAlerts);

  document.getElementById("btn-mode-raw").addEventListener("click", () => {
    currentVideoMode = "raw";
    document.getElementById("btn-mode-raw").classList.add("active");
    document.getElementById("btn-mode-augmented").classList.remove("active");
    const cam = allCameras.find(c => c.id === currentCameraId);
    setupVideoPlayer(cam);
  });

  document.getElementById("btn-mode-augmented").addEventListener("click", () => {
    currentVideoMode = "augmented";
    document.getElementById("btn-mode-augmented").classList.add("active");
    document.getElementById("btn-mode-raw").classList.remove("active");
    const cam = allCameras.find(c => c.id === currentCameraId);
    setupVideoPlayer(cam);
  });

  document.getElementById("btn-fullscreen").addEventListener("click", () => {
    const container = document.getElementById("media-container");
    if (!document.fullscreenElement) {
      container.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  document.getElementById("btn-toggle-follow").addEventListener("click", () => {
    if (!currentCameraId) return;
    let list = getFollowedList();
    if (list.includes(currentCameraId)) {
      list = list.filter(id => id !== currentCameraId);
    } else {
      list.push(currentCameraId);
    }
    setFollowedList(list);
    updateFollowButtonState();
    if (currentFilter === "followed") renderCameraList();
  });

  const uploadForm = document.getElementById("replay-upload-form");
  if (uploadForm) {
    uploadForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const codeInput = document.getElementById("upload-cam-code");
      const fileInput = document.getElementById("upload-video-file");
      const msgEl = document.getElementById("upload-msg");

      if (!codeInput || !fileInput || !fileInput.files[0]) {
        if (msgEl) msgEl.innerText = "Please specify camera code and video file.";
        return;
      }

      const camCode = codeInput.value.trim();
      const formData = new FormData();
      formData.append("camera_code", camCode);
      const recordingTime = document.getElementById("upload-recording-time").value;
      if (!recordingTime || !Number.isFinite(new Date(recordingTime).getTime())) {
        msgEl.textContent = "Enter the clip’s recording time.";
        return;
      }
      formData.append("recording_time", new Date(recordingTime).toISOString());
      formData.append("video_file", fileInput.files[0]);

      if (msgEl) {
        msgEl.style.color = "#2563eb";
        msgEl.innerText = "Uploading backup clip...";
      }

      try {
        const res = await fetch("/api/cameras/upload-replay", {
          method: "POST",
          body: formData
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Upload failed");

        if (msgEl) {
          msgEl.style.color = "#16a34a";
          msgEl.innerText = data.message || "Backup clip configured.";
        }

        const targetCam = allCameras.find(c => c.camera_code === camCode);
        if (targetCam) {
          targetCam.stream_url = data.stream_url;
          targetCam.current_status = "replay_not_live";
          selectCamera(targetCam.id, true);
        } else {
          await loadCameras();
        }
      } catch (err) {
        if (msgEl) {
          msgEl.style.color = "#dc2626";
          msgEl.innerText = `Upload error: ${err.message}`;
        }
      }
    });
  }

  const purgeBtn = document.getElementById("btn-purge-replays");
  if (purgeBtn) {
    purgeBtn.addEventListener("click", async () => {
      if (!confirm("Reset all backup replays to live stream?")) return;
      try {
        const res = await fetch("/api/cameras/purge-replays", { method: "POST" });
        const json = await res.json();
        alert(json.message || "Replays purged.");
        location.reload();
      } catch (e) {
        alert("Failed to purge replays.");
      }
    });
  }
});

function setActiveTab(btn, mode) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  currentFilter = mode;
  renderMarkers();
  renderCameraList();
}

// Each page owns a fresh, server-issued connection token. Never persist it.
let alertSocket = null;
let alertSession = null;
let alertHeartbeat = null;
let alertReconnect = null;
let alertLeaving = false;
let lastAlertReply = 0;

function alertMessage(message) {
  document.getElementById("alert-state").textContent = message;
}

function connectAlerts() {
  if (alertLeaving) return;
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${scheme}//${location.host}/api/alerts/session`);
  alertSocket = socket;
  socket.onmessage = event => {
    if (socket !== alertSocket) return;
    lastAlertReply = Date.now();
    if (event.data === "pong") return;
    const data = JSON.parse(event.data);
    alertSession = data.session_id;
    alertMessage("Alerts off. Choose a threshold and enable for this page.");
    document.getElementById("alert-enable").disabled = !currentCameraId;
    refreshAlerts();
    alertHeartbeat = setInterval(() => {
      if (Date.now() - lastAlertReply >= data.timeout_seconds * 1000) socket.close();
      else if (socket.readyState === WebSocket.OPEN) socket.send("ping");
    }, data.heartbeat_seconds * 1000);
  };
  socket.onclose = () => {
    if (socket !== alertSocket) return;
    clearInterval(alertHeartbeat);
    alertSession = null;
    document.getElementById("alert-enable").disabled = true;
    document.getElementById("alert-disable").disabled = true;
    alertMessage("Alerts disconnected. Reconnecting; enable again when connected.");
    document.getElementById("alert-events").replaceChildren();
    if (!alertLeaving) alertReconnect = setTimeout(connectAlerts, 2000);
  };
  socket.onerror = () => socket.close();
}

function alertQuery(camera, session) {
  return new URLSearchParams({ camera_id: camera, session_id: session }).toString();
}

async function alertRequest(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not update alerts.");
  return data;
}

async function refreshAlerts() {
  const camera = currentCameraId, session = alertSession;
  if (!camera || !session) return;
  const query = alertQuery(camera, session);
  try {
    const [rule, events] = await Promise.all([
      alertRequest(`/api/alerts/rules?${query}`), alertRequest(`/api/alerts/events?${query}`)
    ]);
    if (camera !== currentCameraId || session !== alertSession) return;
    document.getElementById("alert-enable").disabled = false;
    document.getElementById("alert-disable").disabled = !rule;
    document.getElementById("alert-enable").textContent = rule ? "Update threshold" : "Enable alerts";
    alertMessage(rule ? `Enabled at ${rule.threshold_vpm} veh/min · live observations only.` : "Alerts off for this location.");
    const list = document.getElementById("alert-events");
    list.replaceChildren();
    if (!events.length) {
      const item = document.createElement("li");
      item.textContent = "No alerts this session.";
      list.append(item);
    }
    for (const event of events) {
      const item = document.createElement("li");
      item.textContent = `${event.flow_at_trigger} veh/min crossed ${event.threshold_vpm} · ${formatIso(event.trigger_time)}`;
      list.append(item);
    }
  } catch (error) {
    if (camera === currentCameraId && session === alertSession) alertMessage(error.message);
  }
}

async function enableAlerts(event) {
  event.preventDefault();
  if (!alertSession || !currentCameraId) return;
  const threshold = Number(document.getElementById("alert-threshold").value);
  if (!Number.isFinite(threshold) || threshold <= 0) return;
  try {
    await alertRequest("/api/alerts/rules", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({camera_id: currentCameraId, session_id: alertSession, threshold_vpm: threshold}) });
    await refreshAlerts();
  } catch (error) { alertMessage(error.message); }
}

async function disableAlerts() {
  if (!alertSession || !currentCameraId) return;
  try {
    await alertRequest(`/api/alerts/rules?${alertQuery(currentCameraId, alertSession)}`, {method: "DELETE"});
    await refreshAlerts();
  } catch (error) { alertMessage(error.message); }
}

window.addEventListener("pagehide", () => {
  alertLeaving = true;
  clearInterval(alertHeartbeat);
  clearTimeout(alertReconnect);
  alertSession = null;
  if (alertSocket) alertSocket.close();
});
window.addEventListener("pageshow", event => {
  if (event.persisted) { alertLeaving = false; connectAlerts(); }
});
