(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const fields = ["a-from", "a-to", "b-from", "b-to"];
  let data = null;
  let seenEvents = null;
  let loading = false;

  function localValue(ms) {
    const d = new Date(ms);
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function dateMs(id, fallback) {
    const value = byId(id).value;
    const ms = value ? new Date(value).getTime() : fallback;
    return Number.isFinite(ms) ? ms : fallback;
  }

  function summary(items) {
    const valid = items.filter(m => m.validity === "valid" && Number.isFinite(m.flow));
    const total = valid.reduce((sum, m) => sum + m.count, 0);
    const average = valid.length ? valid.reduce((sum, m) => sum + m.flow, 0) / valid.length : null;
    return { valid, unavailable: items.length - valid.length, total, average };
  }

  function stat(label, value, sub) {
    const node = document.createElement("div");
    node.innerHTML = '<div class="stat-label"></div><div class="stat-value"></div><div class="stat-sub"></div>';
    node.children[0].textContent = label;
    node.children[1].textContent = value;
    node.children[2].textContent = sub;
    return node;
  }

  function svg(name, attributes) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }

  function drawChart(a, b, ranges) {
    const chart = byId("chart");
    chart.replaceChildren();
    const max = Math.max(1, ...a.valid.map(m => m.flow), ...b.valid.map(m => m.flow));
    const left = 52, right = 900, top = 15, bottom = 195;
    for (let i = 0; i <= 4; i++) {
      const y = top + (bottom - top) * i / 4;
      chart.append(svg("line", { x1:left, y1:y, x2:right, y2:y, stroke:"#e8ebee", "stroke-width":1 }));
      const label = svg("text", { x:left - 10, y:y + 4, fill:"#64707d", "font-size":11, "text-anchor":"end" });
      label.textContent = (max * (4 - i) / 4).toFixed(1);
      chart.append(label);
    }
    [[a.valid, ranges[0], "#1d5fbf"], [b.valid, ranges[1], "#8c949d"]].forEach(([points, range, colour]) => {
      const denominator = Math.max(1, range[1] - range[0]);
      const coordinates = points.sort((x, y) => x.time - y.time).map(m => [
        left + (right - left) * Math.min(1, Math.max(0, (m.time - range[0]) / denominator)),
        bottom - (bottom - top) * m.flow / max
      ]);
      if (coordinates.length > 1) chart.append(svg("polyline", {
        points: coordinates.map(p => p.join(",")).join(" "), fill:"none", stroke:colour,
        "stroke-width":2.5, "stroke-linecap":"round", "stroke-linejoin":"round"
      }));
      coordinates.forEach(([x,y]) => chart.append(svg("circle", { cx:x, cy:y, r:3.5, fill:colour })));
    });
    const note = byId("chart-note");
    note.textContent = a.valid.length || b.valid.length ? "蓝色：时间段 A · 灰色：时间段 B。横轴为各时间段的相对时间。" : "所选时间段没有有效测量，暂不绘制曲线。";
  }

  function row(main, sub, side, disabled = false) {
    const item = document.createElement("div");
    item.className = `row${disabled ? " disabled" : ""}`;
    const left = document.createElement("div"), right = document.createElement("div");
    left.innerHTML = '<div class="row-main"></div><div class="row-sub"></div>';
    left.children[0].textContent = main;
    left.children[1].textContent = sub;
    right.className = "row-side";
    right.textContent = side;
    item.append(left, right);
    return item;
  }

  function renderLists() {
    const rules = byId("rules"), events = byId("events");
    rules.replaceChildren(); events.replaceChildren();
    const ownRules = data.rules.filter(r => r.location === data.location);
    if (!ownRules.length) rules.append(empty("此地点没有告警规则。"));
    ownRules.forEach(r => rules.append(row(r.id,
      `${String(Math.floor(r.start / 60)).padStart(2,"0")}:${String(r.start % 60).padStart(2,"0")} 至 ${String(Math.floor(r.end / 60)).padStart(2,"0")}:${String(r.end % 60).padStart(2,"0")}`,
      `${r.threshold.toFixed(1)} 辆/分 · ${r.enabled ? "启用" : "停用"}`, !r.enabled)));
    const recent = [...data.events].sort((a,b) => b.time - a.time).slice(0,5);
    if (!recent.length) events.append(empty("暂无告警事件。"));
    recent.forEach(e => events.append(row(e.rule,
      `${new Date(e.time).toLocaleString()} · ${e.measurement}`,
      `${e.flow.toFixed(1)} / ${e.threshold.toFixed(1)} 辆/分`)));
  }

  function empty(message) {
    const node = document.createElement("div");
    node.className = "empty";
    node.textContent = message;
    return node;
  }

  function render() {
    if (!data) return;
    byId("location-name").textContent = data.name;
    byId("location-id").textContent = `地点 ID：${data.location} · ${data.measurements.length} 条测量记录`;
    const rangeA = [dateMs("a-from", 0), dateMs("a-to", Number.MAX_SAFE_INTEGER) + 59999];
    const rangeB = [dateMs("b-from", 0), dateMs("b-to", Number.MAX_SAFE_INTEGER) + 59999];
    const select = range => data.measurements.filter(m => m.time >= range[0] && m.time <= range[1]);
    const a = summary(select(rangeA)), b = summary(select(rangeB));
    const stats = byId("stats"); stats.replaceChildren();
    stats.append(stat("A 平均流量", a.average === null ? "—" : a.average.toFixed(2), `${a.valid.length} 条有效 · ${a.unavailable} 条不可用`));
    stats.append(stat("B 平均流量", b.average === null ? "—" : b.average.toFixed(2), `${b.valid.length} 条有效 · ${b.unavailable} 条不可用`));
    stats.append(stat("A 车辆总数", String(a.total), "所选有效测量之和"));
    stats.append(stat("B 车辆总数", String(b.total), "所选有效测量之和"));
    drawChart(a, b, [rangeA, rangeB]);
    renderLists();
  }

  function initRanges() {
    const times = data.measurements.map(m => m.time).filter(Number.isFinite).sort((a,b) => a-b);
    if (!times.length) return;
    const middle = Math.floor(times.length / 2);
    const firstMinute = Math.floor(times[0] / 60000);
    const lastMinute = Math.floor(times[times.length - 1] / 60000);
    const defaults = firstMinute === lastMinute
      ? [times[0], times[times.length - 1], times[0] - 60000, times[0] - 60000]
      : [times[middle], times[times.length - 1], times[0], times[Math.max(0,middle-1)]];
    fields.forEach((id, index) => { if (!byId(id).value) byId(id).value = localValue(defaults[index]); });
  }

  function checkNewEvents() {
    const keys = new Set(data.events.map(e => `${e.rule}|${e.measurement}`));
    if (seenEvents !== null) {
      const added = data.events.filter(e => !seenEvents.has(`${e.rule}|${e.measurement}`));
      if (added.length) {
        const latest = added[added.length - 1];
        const banner = byId("alert-banner");
        banner.textContent = `新告警：${latest.rule}，流量 ${latest.flow.toFixed(1)} 辆/分，阈值 ${latest.threshold.toFixed(1)} 辆/分。`;
        banner.hidden = false;
        document.title = `(${added.length} 条新告警) PrivateTraffic`;
      }
    }
    seenEvents = keys;
  }

  function loadData() {
    if (loading) return;
    loading = true;
    const script = document.createElement("script");
    script.src = `data.js?t=${Date.now()}`;
    script.onload = () => {
      loading = false; script.remove(); data = window.PT_DATA;
      byId("connection").textContent = "本地记录 · 每 5 秒检查";
      initRanges(); checkNewEvents(); render();
    };
    script.onerror = () => {
      loading = false; script.remove();
      byId("connection").textContent = "尚无数据：运行 pt_zian export";
    };
    document.head.append(script);
  }

  fields.forEach(id => byId(id).addEventListener("change", render));
  byId("refresh").addEventListener("click", loadData);
  loadData();
  setInterval(loadData, 5000);
})();
