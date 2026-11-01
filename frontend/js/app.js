// PrivateTraffic - Location Selection & Current Status
// Defensively handles GPS-free storage, MP4/HLS dual playback, RA1 state-transitions, and PTZ failover

const API_BASE = "/api";
let map, markerGroup;
let allCameras = [];
let selectedCameraId = null;
let currentTab = "pilot";
let pollingInterval = null;
let currentHls = null;
let videoMode = "raw";

const BLANK_IMAGE_URI = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E";

function initMap() {
  const mapEl = document.getElementById('map');
  if (!mapEl) return;

  map = L.map('map').setView([44.9778, -93.2650], 11);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);

  markerGroup = L.layerGroup().addTo(map);
}

function getFollowedLocations() {
  return JSON.parse(localStorage.getItem("pt_followed_cameras") || "[]");
}

function isFollowed(cameraId) {
  return getFollowedLocations().includes(cameraId);
}

function toggleFollow(cameraId) {
  let list = getFollowedLocations();
  if (list.includes(cameraId)) {
    list = list.filter(id => id !== cameraId);
  } else {
    list.push(cameraId);
  }
  localStorage.setItem("pt_followed_cameras", JSON.stringify(list));
  updateFollowCount();
  updateFollowButtonState();
  if (currentTab === "followed") renderList();
}

function updateFollowCount() {
  const countEl = document.getElementById("follow-count");
  if (countEl) countEl.textContent = getFollowedLocations().length;
}

function stopActiveStreams() {
  if (currentHls) {
    try {
      currentHls.stopLoad();
      currentHls.detachMedia();
      currentHls.destroy();
    } catch (e) {
      console.warn("HLS teardown:", e);
    }
    currentHls = null;
  }

  const video = document.getElementById("raw-video-player");
  if (video) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }

  const augImg = document.getElementById("augmented-image-feed");
  if (augImg) {
    augImg.removeAttribute("src");
    augImg.src = BLANK_IMAGE_URI;
  }

  const streamBadge = document.getElementById("badge-stream-state");
  if (streamBadge) {
    streamBadge.className = "badge badge-stream-idle";
    streamBadge.textContent = "● Stream: Idle";
  }
}

async function loadCameras() {
  let data = null;
  try {
    const res = await fetch(`${API_BASE}/cameras`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    console.error("Failed to connect to backend:", err);
    const listEl = document.getElementById("camera-list");
    if (listEl) {
      listEl.innerHTML = `<div style="color:red; padding:8px 0;">Backend not connected. Start FastAPI server.</div>`;
    }
    return;
  }

  allCameras = data || [];
  renderMarkers();
  renderList();

  const defaultPilot = allCameras.find(c => c.is_pilot);
  if (defaultPilot && !selectedCameraId) {
    selectCamera(defaultPilot.id);
  }
}

function renderMarkers(filterRoad = "") {
  if (!markerGroup) return;
  markerGroup.clearLayers();
  const q = filterRoad.toLowerCase().trim();

  allCameras.forEach(cam => {
    if (q && !cam.title.toLowerCase().includes(q) && !cam.road_name.toLowerCase().includes(q)) {
      return;
    }
    if (currentTab === "pilot" && !cam.is_pilot) return;
    if (currentTab === "followed" && !isFollowed(cam.id)) return;

    const isPilot = cam.is_pilot;
    const marker = L.circleMarker([cam.latitude, cam.longitude], {
      radius: isPilot ? 9 : 6,
      fillColor: isPilot ? "#eab308" : "#3b82f6",
      color: isPilot ? "#b45309" : "#ffffff",
      weight: isPilot ? 3 : 1.5,
      opacity: 1,
      fillOpacity: 0.9
    });

    marker.bindTooltip(`${isPilot ? '★ [PILOT] ' : ''}${cam.title}`);
    marker.on('click', () => selectCamera(cam.id));
    markerGroup.addLayer(marker);
  });
}

/**
 * Universal Media Player: Automatically plays either native HLS stream (.m3u8)
 * or uploaded backup MP4 video file (/uploads/...).
 */
function playMedia(streamUrl, isReplay = false) {
  const video = document.getElementById("raw-video-player");
  const streamBadge = document.getElementById("badge-stream-state");
  if (!video || !streamUrl) {
    if (streamBadge) {
      streamBadge.className = "badge badge-stream-idle";
      streamBadge.textContent = "● Stream: N/A";
    }
    return;
  }

  // Teardown previous instances
  if (currentHls) {
    currentHls.destroy();
    currentHls = null;
  }

  // CASE 1: Uploaded Backup MP4 Video (Direct HTML5 playback)
  if (streamUrl.endsWith(".mp4") || streamUrl.includes("/uploads/")) {
    video.removeAttribute("src");
    video.src = streamUrl;
    video.loop = true; // Auto-loop backup footage for smooth demo
    video.play().catch(e => console.warn("Autoplay notice:", e));

    if (streamBadge) {
      streamBadge.className = "badge badge-replay";
      streamBadge.textContent = "● Recorded Clip - Not Live";
    }
    return;
  }

  // CASE 2: Live HLS Stream (.m3u8)
  if (streamBadge) {
    streamBadge.className = "badge badge-stream-idle";
    streamBadge.textContent = "● Stream: Connecting...";
  }

  if (Hls.isSupported()) {
    currentHls = new Hls({ enableWorker: true, lowLatencyMode: true });
    currentHls.loadSource(streamUrl);
    currentHls.attachMedia(video);

    currentHls.on(Hls.Events.MANIFEST_PARSED, () => {
      if (streamBadge) {
        streamBadge.className = isReplay ? "badge badge-replay" : "badge badge-stream-active";
        streamBadge.textContent = isReplay ? "● Recorded Clip - Not Live" : "● Stream: Live (MnDOT)";
      }
      video.play().catch(() => {});
    });

    currentHls.on(Hls.Events.ERROR, () => {
      if (streamBadge) {
        streamBadge.className = "badge badge-stream-idle";
        streamBadge.textContent = "● Stream: Retrying...";
      }
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = streamUrl;
    video.addEventListener('loadedmetadata', () => {
      if (streamBadge) {
        streamBadge.className = isReplay ? "badge badge-replay" : "badge badge-stream-active";
        streamBadge.textContent = isReplay ? "● Recorded Clip - Not Live" : "● Stream: Live (MnDOT)";
      }
      video.play().catch(() => {});
    });
  }
}

function setVideoDisplayMode(mode, hasAugmented) {
  videoMode = mode;
  const rawVideo = document.getElementById("raw-video-player");
  const augImg = document.getElementById("augmented-image-feed");
  const offlineNotice = document.getElementById("cv-offline-notice");
  const btnRaw = document.getElementById("btn-mode-raw");
  const btnAug = document.getElementById("btn-mode-augmented");

  if (btnRaw) btnRaw.classList.toggle("active", mode === "raw");
  if (btnAug) btnAug.classList.toggle("active", mode === "augmented");

  const cam = allCameras.find(c => c.id === selectedCameraId);

  if (mode === "raw") {
    if (augImg) {
      augImg.removeAttribute("src");
      augImg.src = BLANK_IMAGE_URI;
      augImg.style.display = "none";
    }
    if (offlineNotice) offlineNotice.style.display = "none";

    if (rawVideo) {
      rawVideo.style.display = "block";
      if (cam && cam.stream_url) {
        playMedia(cam.stream_url, cam.current_status === "replay_not_live");
      }
    }
  } else {
    if (currentHls) {
      try {
        currentHls.stopLoad();
        currentHls.detachMedia();
        currentHls.destroy();
      } catch (e) {}
      currentHls = null;
    }
    if (rawVideo) {
      rawVideo.pause();
      rawVideo.removeAttribute("src");
      rawVideo.load();
      rawVideo.style.display = "none";
    }

    if (hasAugmented) {
      if (offlineNotice) offlineNotice.style.display = "none";
      if (augImg) {
        augImg.style.display = "block";
        augImg.src = `${API_BASE}/cameras/${encodeURIComponent(selectedCameraId)}/stream?t=${Date.now()}`;
      }
    } else {
      if (augImg) {
        augImg.removeAttribute("src");
        augImg.src = BLANK_IMAGE_URI;
        augImg.style.display = "none";
      }
      if (offlineNotice) offlineNotice.style.display = "flex";
    }
  }
}

async function fetchCurrentStatus(cameraId) {
  try {
    const res = await fetch(`${API_BASE}/cameras/${encodeURIComponent(cameraId)}/status`);
    if (!res.ok) return;
    const statusData = await res.json();

    const titleEl = document.getElementById("card-cam-title");
    if (titleEl) titleEl.textContent = statusData.camera_title;

    // Analytics Freshness Badge mapping RA1 Figure 5-5
    const badge = document.getElementById("card-cam-badge");
    if (badge) {
      if (statusData.status === "stale") {
        badge.className = "badge badge-stale";
        badge.textContent = "State: Stale (>60s)";
      } else if (statusData.status === "live_fresh" || statusData.status === "live") {
        badge.className = "badge badge-live";
        badge.textContent = "State: Live Fresh";
      } else if (statusData.status === "replay_not_live" || statusData.status === "replay") {
        badge.className = "badge badge-replay";
        badge.textContent = "Recorded clip - not live";
      } else if (statusData.status === "recovering") {
        badge.className = "badge badge-stale";
        badge.textContent = "State: Recovering";
      } else if (statusData.status === "offline") {
        badge.className = "badge badge-unavailable";
        badge.textContent = "State: Offline";
      } else {
        badge.className = "badge badge-unavailable";
        badge.textContent = "State: Initializing";
      }
    }

    // Auto-fill the upload form camera code with currently selected camera
    const uploadInput = document.getElementById("upload-cam-code");
    if (uploadInput && statusData.camera_code && !uploadInput.value) {
      uploadInput.value = statusData.camera_code;
    }

    // PTZ Failover Indicator
    const ptzBox = document.getElementById("ptz-failover-box");
    if (ptzBox) {
      if (statusData.status === "stale" || statusData.status === "offline") {
        const candidates = (statusData.failover_candidates || []).join(", ");
        ptzBox.textContent = `⚠️ Viewpoint / PTZ Alert: Feed degraded. Alternate pilot feeds ready: [${candidates}]`;
        ptzBox.style.display = "block";
      } else {
        ptzBox.style.display = "none";
      }
    }

    const countEl = document.getElementById("val-count");
    const flowEl = document.getElementById("val-flow");
    const levelEl = document.getElementById("val-level");
    const updatedEl = document.getElementById("val-updated");

    if (statusData.status === "offline" || statusData.status === "initializing" || statusData.vehicle_count === null) {
      if (countEl) countEl.textContent = "--";
      if (flowEl) flowEl.textContent = "--";
      if (levelEl) {
        levelEl.textContent = "unable_to_determine";
        levelEl.className = "metric-val";
      }
      if (updatedEl) updatedEl.textContent = "No valid inference measurements recorded.";
    } else {
      if (countEl) countEl.textContent = statusData.vehicle_count;
      if (flowEl) flowEl.textContent = statusData.flow_per_minute.toFixed(1);
      if (levelEl) {
        levelEl.textContent = statusData.traffic_level ? statusData.traffic_level.toUpperCase() : "UNABLE_TO_DETERMINE";
        levelEl.className = `metric-val level-${statusData.traffic_level}`;
      }

      const timeStr = new Date(statusData.measurement_time).toLocaleTimeString();
      const delay = statusData.seconds_since_update != null 
        ? `(${Math.round(statusData.seconds_since_update)}s ago)` 
        : "";

      if (updatedEl) {
        if (statusData.status === "stale") {
          updatedEl.innerHTML = `<span style="color:#b45309">⚠️ Last valid update: ${timeStr} ${delay}</span>`;
        } else {
          updatedEl.innerHTML = `Measured at: ${timeStr} ${delay}`;
        }
      }
    }

    const augBtn = document.getElementById("btn-mode-augmented");
    if (augBtn) {
      augBtn.textContent = statusData.has_augmented_stream 
        ? "⚡ YOLO Augmented (Live)" 
        : "⚡ YOLO (Offline)";
    }

    if (videoMode === "augmented") {
      setVideoDisplayMode("augmented", statusData.has_augmented_stream);
    }
  } catch (err) {
    console.error("Status update fetch error:", err);
  }
}

function selectCamera(cameraId) {
  stopActiveStreams();

  selectedCameraId = cameraId;
  const cam = allCameras.find(c => c.id === cameraId);
  if (!cam) return;

  if (map) map.setView([cam.latitude, cam.longitude], 14);

  const followBtn = document.getElementById("btn-toggle-follow");
  if (followBtn) followBtn.style.display = "block";

  const displayArea = document.getElementById("video-display-area");
  if (displayArea) displayArea.style.display = "block";

  // Pre-fill upload code input
  const uploadInput = document.getElementById("upload-cam-code");
  if (uploadInput && cam.camera_code) {
    uploadInput.value = cam.camera_code;
  }

  updateFollowButtonState();
  setVideoDisplayMode("raw", false);
  fetchCurrentStatus(cameraId);

  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(() => fetchCurrentStatus(cameraId), 10000);
}

function updateFollowButtonState() {
  const btn = document.getElementById("btn-toggle-follow");
  if (!btn || !selectedCameraId) return;
  if (isFollowed(selectedCameraId)) {
    btn.textContent = "Remove from Followed";
    btn.classList.add("followed");
  } else {
    btn.textContent = "Follow this Location";
    btn.classList.remove("followed");
  }
}

function renderList() {
  const container = document.getElementById("camera-list");
  const heading = document.getElementById("list-heading");
  if (!container) return;

  let filtered = allCameras;

  if (currentTab === "pilot") {
    filtered = allCameras.filter(c => c.is_pilot);
    if (heading) heading.textContent = `Pilot Cameras (${filtered.length})`;
  } else if (currentTab === "followed") {
    const followed = getFollowedLocations();
    filtered = allCameras.filter(c => followed.includes(c.id));
    if (heading) heading.textContent = `Followed Locations (${filtered.length})`;
  } else {
    if (heading) heading.textContent = `All Cameras (${allCameras.length})`;
  }

  if (filtered.length === 0) {
    container.innerHTML = `<div style="color:#64748b; padding:10px 0;">No cameras found.</div>`;
    return;
  }

  container.innerHTML = filtered.map(cam => `
    <div class="camera-item" onclick="selectCamera('${cam.id}')">
      <div>
        <strong>${cam.is_pilot ? '★ ' : ''}${cam.road_name}</strong>
        <div style="color:#64748b; font-size:0.75rem;">${cam.title}</div>
      </div>
      <span class="badge badge-${cam.current_status}">${cam.current_status}</span>
    </div>
  `).join('');
}

// Global Event Listeners
window.addEventListener("DOMContentLoaded", () => {
  initMap();
  updateFollowCount();
  loadCameras();

  const searchBox = document.getElementById("search-box");
  if (searchBox) {
    searchBox.addEventListener("input", (e) => renderMarkers(e.target.value));
  }

  const tabPilot = document.getElementById("tab-pilot");
  if (tabPilot) tabPilot.addEventListener("click", () => switchTab("pilot"));

  const tabAll = document.getElementById("tab-all");
  if (tabAll) tabAll.addEventListener("click", () => switchTab("all"));

  const tabFollowed = document.getElementById("tab-followed");
  if (tabFollowed) tabFollowed.addEventListener("click", () => switchTab("followed"));

  const followBtn = document.getElementById("btn-toggle-follow");
  if (followBtn) {
    followBtn.addEventListener("click", () => {
      if (selectedCameraId) toggleFollow(selectedCameraId);
    });
  }

  const btnRaw = document.getElementById("btn-mode-raw");
  if (btnRaw) {
    btnRaw.addEventListener("click", () => setVideoDisplayMode("raw", false));
  }

  const btnAug = document.getElementById("btn-mode-augmented");
  if (btnAug) {
    btnAug.addEventListener("click", () => {
      fetchCurrentStatus(selectedCameraId).then(() => {
        setVideoDisplayMode("augmented", false);
      });
    });
  }

  // Backup Video Upload Submission Handler
  const uploadForm = document.getElementById("replay-upload-form");
  if (uploadForm) {
    uploadForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const camCode = document.getElementById("upload-cam-code").value.trim();
      const fileInput = document.getElementById("upload-video-file");
      const msg = document.getElementById("upload-msg");

      if (!camCode || fileInput.files.length === 0) {
        msg.innerHTML = "<span style='color:red;'>Enter camera code and select a video file.</span>";
        return;
      }

      const formData = new FormData();
      formData.append("camera_code", camCode);
      formData.append("video_file", fileInput.files[0]);

      msg.innerHTML = "<span style='color:#2563eb;'>Uploading video clip...</span>";

      try {
        const res = await fetch(`${API_BASE}/cameras/upload-replay`, {
          method: "POST",
          body: formData
        });
        const data = await res.json();
        if (res.ok) {
          msg.innerHTML = `<span style='color:green;'>✔ Backup video active for ${camCode}!</span>`;
          
          // Refresh cameras and immediately switch active player to new upload
          await loadCameras();
          const targetCam = allCameras.find(c => c.camera_code === camCode);
          if (targetCam) {
            targetCam.stream_url = data.stream_url;
            targetCam.current_status = "replay_not_live";
            selectCamera(targetCam.id);
          }
        } else {
          msg.innerHTML = `<span style='color:red;'>Error: ${data.detail || "Upload failed"}</span>`;
        }
      } catch (err) {
        msg.innerHTML = `<span style='color:red;'>Network error uploading file.</span>`;
      }
    });
  }
});

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  const activeBtn = document.getElementById(`tab-${tab}`);
  if (activeBtn) activeBtn.classList.add("active");

  const searchBox = document.getElementById("search-box");
  const query = searchBox ? searchBox.value : "";
  renderMarkers(query);
  renderList();
}

window.addEventListener("beforeunload", () => {
  stopActiveStreams();
});

// Handler for Purging All Backup Videos
document.addEventListener("DOMContentLoaded", () => {
  const purgeBtn = document.getElementById("btn-purge-replays");
  if (!purgeBtn) return;

  purgeBtn.addEventListener("click", async () => {
    if (!confirm("Are you sure you want to delete all uploaded backup files and restore native live streams?")) {
      return;
    }

    const msg = document.getElementById("upload-msg");
    msg.innerHTML = "<span style='color:#ef4444;'>Purging files and resetting streams...</span>";

    try {
      const res = await fetch(`${API_BASE}/cameras/purge-replays`, { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        msg.innerHTML = `<span style='color:green;'>✔ ${data.message}</span>`;
        // Reload camera catalog and refresh active view
        await loadCameras();
        if (selectedCameraId) {
          selectCamera(selectedCameraId);
        }
      } else {
        msg.innerHTML = `<span style='color:red;'>Error: ${data.detail || "Purge failed"}</span>`;
      }
    } catch (err) {
      msg.innerHTML = "<span style='color:red;'>Network error purging replays.</span>";
    }
  });
});
