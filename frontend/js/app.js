let map = null;
let defaultBounds = null;
let markersMap = {};
let allCameras = [];
let currentFilter = "all";
let currentCameraId = null;
let currentStatusTimer = null;
let hlsInstance = null;
let augmentedWs = null;
let currentVideoMode = "raw";

function getThresholdValues() {
  const t1Input = document.getElementById("input-t1");
  const t2Input = document.getElementById("input-t2");
  let t1 = parseFloat(t1Input ? t1Input.value : 20.0);
  let t2 = parseFloat(t2Input ? t2Input.value : 45.0);
  if (isNaN(t1) || t1 <= 0) t1 = 20.0;
  if (isNaN(t2) || t2 <= t1) t2 = Math.max(t1 + 5.0, 45.0);
  return { t1, t2 };
}

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
  if (defaultBounds && map) {
    map.fitBounds(defaultBounds, { padding: [20, 20] });
    map.setZoom(map.getZoom() + 1);
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
    div.onclick = function (e) {
      e.preventDefault();
      resetMapView();
    };
    return div;
  };
  resetControl.addTo(map);
}

function formatIso(isoString) {
  if (!isoString) return "Not available";
  try {
    const d = new Date(isoString);
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
      defaultBounds = L.latLngBounds(validCoords);
      resetMapView();
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
  if (videoArea) videoArea.style.display = "block";

  await fetchAndUpdateStatus();

  if (currentStatusTimer) clearInterval(currentStatusTimer);
  currentStatusTimer = setInterval(fetchAndUpdateStatus, 2500);

  setupVideoPlayer(cam);
}

function updateFollowButtonState() {
  const followBtn = document.getElementById("btn-toggle-follow");
  if (!followBtn || !currentCameraId) return;
  const isF = isCameraFollowed(currentCameraId);
  followBtn.innerText = isF ? "★ Unfollow this Location" : "☆ Follow this Location";
  followBtn.className = isF ? "follow-btn followed" : "follow-btn";
}

async function fetchAndUpdateStatus() {
  if (!currentCameraId) return;
  const { t1, t2 } = getThresholdValues();

  try {
    const res = await fetch(`/api/cameras/${encodeURIComponent(currentCameraId)}/status?t1=${t1}&t2=${t2}`);
    if (!res.ok) return;
    const data = await res.json();
    renderStatusData(data);
  } catch (e) {}
}

function renderStatusData(data) {
  document.getElementById("card-cam-title").innerText = data.camera_title;

  const statusBadge = document.getElementById("card-cam-badge");
  statusBadge.className = "badge";

  let badgeText = data.status || "Unknown";
  if (data.status === "live" || data.status === "live_fresh") {
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
  } else if (data.status === "replay_not_live") {
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

  const isReplay = (data.status === "replay_not_live" || (data.stream_url && data.stream_url.includes("/uploads")));
  const liveIndicator = document.getElementById("live-indicator-overlay");
  if (liveIndicator) {
    liveIndicator.style.display = isReplay ? "none" : "flex";
  }

  const vidStatusTag = document.getElementById("vid-status-tag");
  if (vidStatusTag) {
    vidStatusTag.innerText = isReplay ? "● Backup Replay (Not Live)" : "● Live Stream (MnDOT)";
    vidStatusTag.style.color = isReplay ? "#c084fc" : "#22c55e";
  }

  const isSuppressed = ["stale", "offline", "unavailable", "initializing", "recovering"].includes(data.status);

  document.getElementById("val-count").innerText = (data.vehicle_count !== null && data.vehicle_count !== undefined) ? data.vehicle_count : "--";
  document.getElementById("val-flow").innerText = (data.flow_per_minute !== null && data.flow_per_minute !== undefined) ? Number(data.flow_per_minute).toFixed(1) : "--";

  const levelVal = document.getElementById("val-level");
  levelVal.className = "metric-val";

  if (isSuppressed || data.traffic_level === "unable_to_determine" || !data.traffic_level) {
    levelVal.innerText = isSuppressed ? "Suppressed" : "--";
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

  if (!cam || !cam.stream_url) return;

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

  document.getElementById("btn-apply-thresholds").addEventListener("click", () => {
    fetchAndUpdateStatus();
  });
  document.getElementById("input-t1").addEventListener("change", () => fetchAndUpdateStatus());
  document.getElementById("input-t2").addEventListener("change", () => fetchAndUpdateStatus());

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
