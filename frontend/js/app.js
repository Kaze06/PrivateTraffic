(function () {
  let map;
  let markersLayer;
  let allCameras = [];
  let selectedCameraId = null;
  let activeTab = "all";
  let statusPollTimer = null;
  let hlsPlayer = null;

  const FOLLOWED_KEY = "privatetraffic_followed_cams";

  function getFollowedList() {
    try {
      return JSON.parse(localStorage.getItem(FOLLOWED_KEY)) || [];
    } catch {
      return [];
    }
  }

  function setFollowedList(list) {
    localStorage.setItem(FOLLOWED_KEY, JSON.stringify(list));
    updateFollowCountBadge();
  }

  function isFollowed(camId) {
    return getFollowedList().includes(camId);
  }

  function toggleFollow(camId) {
    let list = getFollowedList();
    if (list.includes(camId)) {
      list = list.filter(id => id !== camId);
    } else {
      list.push(camId);
    }
    setFollowedList(list);
    renderCameraList();
    updateActiveCardFollowButton();
  }

  function updateFollowCountBadge() {
    const el = document.getElementById("follow-count");
    if (el) el.innerText = getFollowedList().length;
  }

  function initMap() {
    map = L.map("map", { zoomControl: true }).setView([44.9778, -93.2650], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    markersLayer = L.layerGroup().addTo(map);
  }

  async function loadCameras() {
    try {
      const res = await fetch("/api/cameras");
      allCameras = await res.json();

      renderMarkers();
      renderCameraList();

      const defCam = allCameras.find(c => c.is_default) || allCameras[0];
      if (defCam) {
        selectCamera(defCam.id);
      }
    } catch (e) {
      console.error("Failed to fetch cameras:", e);
      document.getElementById("camera-list").innerHTML = "<div style='color:red;'>Failed to load camera sources.</div>";
    }
  }

  function renderMarkers() {
    markersLayer.clearLayers();

    allCameras.forEach(cam => {
      if (activeTab === "pilot" && !cam.is_pilot) return;
      if (activeTab === "followed" && !isFollowed(cam.id)) return;

      const marker = L.circleMarker([cam.latitude, cam.longitude], {
        radius: cam.is_pilot ? 8 : 5,
        fillColor: cam.is_pilot ? "#2563eb" : "#64748b",
        color: "#ffffff",
        weight: 1.5,
        opacity: 1,
        fillOpacity: 0.85
      });

      marker.bindTooltip(`<b>${cam.road_name}</b><br>${cam.title}`, { offset: [0, -6] });
      marker.on("click", () => selectCamera(cam.id));
      markersLayer.addLayer(marker);
    });
  }

  function renderCameraList() {
    const listEl = document.getElementById("camera-list");
    const query = document.getElementById("search-box").value.toLowerCase().trim();

    const filtered = allCameras.filter(c => {
      if (activeTab === "pilot" && !c.is_pilot) return false;
      if (activeTab === "followed" && !isFollowed(c.id)) return false;
      if (query && !c.title.toLowerCase().includes(query) && !c.road_name.toLowerCase().includes(query)) {
        return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = "<div style='font-size:0.8rem; color:#94a3b8;'>No matching cameras.</div>";
      return;
    }

    listEl.innerHTML = "";
    filtered.forEach(c => {
      const item = document.createElement("div");
      item.className = "camera-item" + (c.id === selectedCameraId ? " selected" : "");
      item.innerHTML = `
        <div>
          <div style="font-weight:600; color:#1e293b;">${c.road_name}</div>
          <div style="font-size:0.72rem; color:#64748b;">${c.title}</div>
        </div>
        <span class="badge ${c.is_pilot ? 'badge-live' : 'badge-unavailable'}">${c.is_pilot ? 'Pilot' : 'Feed'}</span>
      `;
      item.onclick = () => selectCamera(c.id);
      listEl.appendChild(item);
    });
  }

  async function selectCamera(camId) {
    selectedCameraId = camId;
    const cam = allCameras.find(c => c.id === camId);
    if (!cam) return;

    fetch("/api/cameras/active-focus", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_id: camId })
    }).catch(() => {});

    map.panTo([cam.latitude, cam.longitude]);
    document.getElementById("card-cam-title").innerText = cam.title;
    updateActiveCardFollowButton();
    renderCameraList();

    setupVideoPlayer(cam);
    pollCameraStatus();

    if (statusPollTimer) clearInterval(statusPollTimer);
    statusPollTimer = setInterval(pollCameraStatus, 5000);
  }

  function setupVideoPlayer(cam) {
    const videoArea = document.getElementById("video-display-area");
    const rawVideo = document.getElementById("raw-video-player");

    videoArea.style.display = "block";

    if (hlsPlayer) {
      hlsPlayer.destroy();
      hlsPlayer = null;
    }

    const streamUrl = cam.stream_url;
    if (streamUrl && streamUrl.endsWith(".m3u8")) {
      if (Hls.isSupported()) {
        hlsPlayer = new Hls({ enableWorker: true, lowLatencyMode: true });
        hlsPlayer.loadSource(streamUrl);
        hlsPlayer.attachMedia(rawVideo);
      } else if (rawVideo.canPlayType("application/vnd.apple.mpegurl")) {
        rawVideo.src = streamUrl;
      }
    } else if (streamUrl) {
      rawVideo.src = streamUrl;
    }

    const isAugmentedActive = document.getElementById("btn-mode-augmented").classList.contains("active");
    setStreamViewMode(isAugmentedActive ? "augmented" : "raw");
  }

  function setStreamViewMode(mode) {
    const rawVideo = document.getElementById("raw-video-player");
    const augImg = document.getElementById("augmented-image-feed");
    const btnRaw = document.getElementById("btn-mode-raw");
    const btnAug = document.getElementById("btn-mode-augmented");
    const liveTag = document.getElementById("vid-status-tag");
    const cvNotice = document.getElementById("cv-offline-notice");

    if (mode === "raw") {
      btnRaw.classList.add("active");
      btnAug.classList.remove("active");
      rawVideo.style.display = "block";
      augImg.style.display = "none";
      augImg.src = "";
      cvNotice.style.display = "none";
      liveTag.innerText = "● Live Stream (MnDOT)";
    } else {
      btnRaw.classList.remove("active");
      btnAug.classList.add("active");
      rawVideo.style.display = "none";
      augImg.style.display = "block";
      augImg.src = `/api/cameras/${selectedCameraId}/stream`;
      liveTag.innerText = "⚡ RF-DETR Detection Feed";
    }
  }

  async function pollCameraStatus() {
    if (!selectedCameraId) return;

    try {
      const res = await fetch(`/api/cameras/${selectedCameraId}/status`);
      if (!res.ok) return;
      const data = await res.json();

      updateStatusCard(data);
      checkActiveAlerts(data);
    } catch (e) {
      console.warn("Status poll error:", e);
    }
  }

  function updateStatusCard(data) {
    const badgeState = document.getElementById("card-cam-badge");
    const badgeStream = document.getElementById("badge-stream-state");
    const valCount = document.getElementById("val-count");
    const valFlow = document.getElementById("val-flow");
    const valLevel = document.getElementById("val-level");
    const valUpdated = document.getElementById("val-updated");

    badgeState.className = "badge";
    if (data.status === "live" || data.status === "live_fresh") {
      badgeState.classList.add("badge-live");
      badgeState.innerText = "State: Live";
    } else if (data.status === "stale") {
      badgeState.classList.add("badge-stale");
      badgeState.innerText = "State: Stale (>60s)";
    } else if (data.status === "replay_not_live" || data.status === "replay") {
      badgeState.classList.add("badge-replay");
      badgeState.innerText = "Recorded clip - not live";
    } else {
      badgeState.classList.add("badge-unavailable");
      badgeState.innerText = `State: ${data.status}`;
    }

    if (data.has_augmented_stream) {
      badgeStream.className = "badge badge-stream-active";
      badgeStream.innerText = "● RF-DETR: Online";
      document.getElementById("cv-offline-notice").style.display = "none";
    } else {
      badgeStream.className = "badge badge-stream-idle";
      badgeStream.innerText = "● RF-DETR: Standby";
    }

    if (data.status === "stale" || data.status === "offline" || data.status === "unavailable") {
      valCount.innerText = "--";
      valFlow.innerText = "--";
      valLevel.innerText = "unable_to_determine";
      valLevel.className = "metric-val";
    } else {
      valCount.innerText = data.vehicle_count !== null ? data.vehicle_count : "--";
      valFlow.innerText = data.flow_per_minute !== null ? data.flow_per_minute.toFixed(1) : "--";
      valLevel.innerText = data.traffic_level || "unable_to_determine";
      valLevel.className = "metric-val level-" + (data.traffic_level || "");
    }

    if (data.measurement_time) {
      const dt = new Date(data.measurement_time);
      valUpdated.innerText = `Measurement Time: ${dt.toLocaleTimeString()} (${Math.round(data.seconds_since_update || 0)}s ago)`;
    } else {
      valUpdated.innerText = "Measurement Time: Waiting for window completion";
    }
  }

  async function checkActiveAlerts(data) {
    if (!data.camera_id) return;
    try {
      const res = await fetch(`/api/alerts/events?camera_id=${encodeURIComponent(data.camera_id)}`);
      if (res.ok) {
        const events = await res.json();
        if (events.length > 0) {
          const latest = events[0];
          const eventTime = new Date(latest.trigger_time);
          const ageSeconds = (new Date() - eventTime) / 1000;
          if (ageSeconds < 60 && data.status.startsWith("live")) {
            displayInPageAlert(latest.reason);
          }
        }
      }
    } catch (e) {
      console.warn("Alert check failed:", e);
    }
  }

  function displayInPageAlert(msg) {
    let alertBanner = document.getElementById("traffic-alert-banner");
    if (!alertBanner) {
      alertBanner = document.createElement("div");
      alertBanner.id = "traffic-alert-banner";
      alertBanner.style = "position:fixed; top:16px; right:16px; background:#dc2626; color:white; padding:12px 18px; border-radius:8px; font-weight:bold; box-shadow:0 4px 12px rgba(0,0,0,0.3); z-index:9999; display:flex; align-items:center; gap:8px;";
      document.body.appendChild(alertBanner);
    }
    alertBanner.innerHTML = `⚠️ <span>${msg}</span>`;
    setTimeout(() => {
      if (alertBanner) alertBanner.remove();
    }, 10000);
  }

  function updateActiveCardFollowButton() {
    const btn = document.getElementById("btn-toggle-follow");
    if (!selectedCameraId) {
      btn.style.display = "none";
      return;
    }
    btn.style.display = "block";
    if (isFollowed(selectedCameraId)) {
      btn.classList.add("followed");
      btn.innerText = "★ Following Location (Click to Unfollow)";
    } else {
      btn.classList.remove("followed");
      btn.innerText = "☆ Follow this Location";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    initMap();
    loadCameras();
    updateFollowCountBadge();

    document.getElementById("btn-mode-raw").onclick = () => setStreamViewMode("raw");
    document.getElementById("btn-mode-augmented").onclick = () => setStreamViewMode("augmented");

    document.getElementById("btn-play-pause").onclick = () => {
      const v = document.getElementById("raw-video-player");
      if (v.paused) {
        v.play();
        document.getElementById("btn-play-pause").innerText = "⏸ Pause";
      } else {
        v.pause();
        document.getElementById("btn-play-pause").innerText = "▶ Play";
      }
    };

    document.getElementById("btn-fullscreen").onclick = () => {
      const container = document.getElementById("media-container");
      if (!document.fullscreenElement) {
        container.requestFullscreen().catch(err => alert("Fullscreen error: " + err.message));
      } else {
        document.exitFullscreen();
      }
    };

    document.getElementById("btn-toggle-follow").onclick = () => {
      if (selectedCameraId) toggleFollow(selectedCameraId);
    };

    document.getElementById("search-box").addEventListener("input", renderCameraList);

    document.getElementById("tab-all").onclick = function () {
      activeTab = "all";
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      this.classList.add("active");
      renderMarkers();
      renderCameraList();
    };

    document.getElementById("tab-pilot").onclick = function () {
      activeTab = "pilot";
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      this.classList.add("active");
      renderMarkers();
      renderCameraList();
    };

    document.getElementById("tab-followed").onclick = function () {
      activeTab = "followed";
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      this.classList.add("active");
      renderMarkers();
      renderCameraList();
    };

    document.getElementById("replay-upload-form").onsubmit = async (e) => {
      e.preventDefault();
      const code = document.getElementById("upload-cam-code").value.trim();
      const fileInput = document.getElementById("upload-video-file");
      const msg = document.getElementById("upload-msg");

      if (!fileInput.files.length) return;

      const formData = new FormData();
      formData.append("camera_code", code);
      formData.append("video_file", fileInput.files[0]);

      msg.innerText = "Uploading backup replay clip...";
      try {
        const res = await fetch("/api/cameras/upload-replay", { method: "POST", body: formData });
        const resp = await res.json();
        if (res.ok) {
          msg.style.color = "green";
          msg.innerText = "Replay active: " + resp.message;
          await loadCameras();
          if (resp.camera_id) selectCamera(resp.camera_id);
        } else {
          msg.style.color = "red";
          msg.innerText = resp.detail || "Upload failed.";
        }
      } catch (err) {
        msg.style.color = "red";
        msg.innerText = "Error: " + err.message;
      }
    };

    document.getElementById("btn-purge-replays").onclick = async () => {
      if (!confirm("Reset all cameras back to live streaming and clear uploaded videos?")) return;
      try {
        const res = await fetch("/api/cameras/purge-replays", { method: "POST" });
        const resp = await res.json();
        alert(resp.message);
        await loadCameras();
        if (selectedCameraId) selectCamera(selectedCameraId);
      } catch (e) {
        alert("Purge error: " + e.message);
      }
    };
  });
})();
