/* Flood Triage Console — front-end controller (vanilla JS, no build step).
   Candidates are named settlements ranked by flood exposure + road disruption. */
"use strict";

const API = "/api";
const state = {
  event: null, data: null, base: "after", selected: null,
  layers: { base: null, priority: null, flood: null, roads: null },
  places: null,      // L.layerGroup of settlement markers + radius rings
  rings: {},         // id -> L.circle
  markers: {},       // id -> L.marker
  opacity: 0.6,
};
let map;

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const dataUrl = (p) => `/data/${state.event}/${p}`;
const pct = (x) => Math.round(x * 100);
const esc = (s) => (s || "").replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
const sevColor = { Severe: "#d9603f", High: "#e08a3c", Moderate: "#e0b64a", Low: "#9fb04a", Minimal: "#6a8aa0" };

function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2200); }
function loading(on, msg) { const l = $("#loading"); if (msg) l.textContent = msg; l.classList.toggle("on", on); }
function latLngBounds(b) { const [w, s, e, n] = b; return [[s, w], [n, e]]; }

async function init() {
  map = L.map("map", { zoomControl: true, attributionControl: false, minZoom: 10, maxZoom: 18 });
  map.setView([27.1, 67.74], 12);
  await refreshVision();
  const events = await (await fetch(`${API}/events`)).json();
  const sel = $("#eventSel");
  events.forEach((ev) => { const o = el("option"); o.value = ev.event; o.textContent = ev.event; sel.appendChild(o); });
  sel.onchange = () => loadEvent(sel.value);
  wireControls();
  if (events.length) await loadEvent(events[0].event);
}

async function refreshVision() {
  try {
    const v = await (await fetch(`${API}/vision/status`)).json();
    const pill = $("#visionPill");
    const on = !!v.available;
    pill.classList.toggle("on", on); pill.classList.toggle("off", !on);
    const label = v.active === "cloud" ? `notes: ${v.model}`
      : v.active === "ollama" ? `vision: ${v.model}`
      : "notes: heuristic";
    pill.querySelector(".txt").textContent = label;
    pill.title = v.detail || "";
  } catch { /* ignore */ }
}

async function loadEvent(event) {
  state.event = event;
  loading(true, "computing flood exposure…");
  const data = await (await fetch(`${API}/events/${event}`)).json();
  state.data = data; state.selected = null;
  renderSummary(data);
  renderBase(); renderOverlays();
  await renderRoads();
  renderPlaces();
  renderCards(data);
  map.fitBounds(latLngBounds(data.bounds_wgs84));
  loading(false);
}

function renderSummary(d) {
  const s = d.stats;
  $("#summary").innerHTML = "";
  const items = [
    ["Settlements hit", `${s.settlements_affected}`, `/ ${s.settlements_in_aoi}`],
    ["Roads flooded", `${s.total_flooded_road_km}`, "km"],
    ["New water", `${Math.round(s.new_water_hectares).toLocaleString()}`, "ha"],
  ];
  items.forEach(([k, v, u]) => {
    const st = el("div", "stat");
    st.appendChild(el("div", "k", k));
    st.appendChild(el("div", "v", `${v}${u ? ` <small>${u}</small>` : ""}`));
    $("#summary").appendChild(st);
  });
  $("#listCount").textContent = `${d.candidates.length} ranked`;
}

function renderBase() {
  if (state.layers.base) map.removeLayer(state.layers.base);
  state.layers.base = L.imageOverlay(dataUrl(state.data.layers[state.base]),
    latLngBounds(state.data.bounds_wgs84), { opacity: 1, zIndex: 100 }).addTo(map);
}

function renderOverlays() {
  [["priority", "priority_overlay", "#tgPriority", 200], ["flood", "flood_overlay", "#tgFlood", 210]].forEach(([key, lk, tg, z]) => {
    if (state.layers[key]) { map.removeLayer(state.layers[key]); state.layers[key] = null; }
    const lyr = L.imageOverlay(dataUrl(state.data.layers[lk]), latLngBounds(state.data.bounds_wgs84), { opacity: state.opacity, zIndex: z });
    state.layers[key] = lyr;
    if ($(tg).checked) lyr.addTo(map);
  });
}

async function renderRoads() {
  if (state.layers.roads) { map.removeLayer(state.layers.roads); state.layers.roads = null; }
  try {
    const gj = await (await fetch(dataUrl(state.data.layers.flooded_roads))).json();
    state.layers.roads = L.geoJSON(gj, {
      style: { color: "#ff4d4d", weight: 2.5, opacity: 0.9 },
      onEachFeature: (f, lyr) => {
        const p = f.properties || {};
        lyr.bindTooltip(`${p.name || p.highway || "road"} — ${p.length_m} m under water`, { sticky: true });
      },
    });
    if ($("#tgRoads").checked) state.layers.roads.addTo(map);
  } catch { /* no roads */ }
}

function renderPlaces() {
  if (state.places) map.removeLayer(state.places);
  state.places = L.layerGroup();
  state.rings = {}; state.markers = {};
  const ranked = new Map(state.data.candidates.map((c) => [c.name + "|" + c.lat, c]));

  // faint context markers for all settlements (unranked)
  (state.data.places || []).forEach((p) => {
    if (ranked.has(p.name + "|" + p.lat)) return;
    const dot = L.circleMarker([p.lat, p.lon], { radius: 2.5, color: "#67737f", weight: 1, fillOpacity: 0.5 });
    dot.bindTooltip(esc(p.name_en || p.name), { direction: "top" });
    state.places.addLayer(dot);
  });

  state.data.candidates.forEach((c) => {
    const ring = L.circle([c.lat, c.lon], { radius: c.radius_m, color: sevColor[c.severity] || "#e6a73c", weight: 1, opacity: 0.6, fillOpacity: 0.06 });
    ring.on("click", () => selectCandidate(c.id, true));
    state.rings[c.id] = ring; state.places.addLayer(ring);

    const icon = L.divIcon({ className: "", iconSize: [120, 22], iconAnchor: [11, 11],
      html: `<div class="cand-label"><span class="cand-marker" data-id="${c.id}">${c.rank}</span><span class="cand-name">${esc(c.name_en || c.name)}</span></div>` });
    const m = L.marker([c.lat, c.lon], { icon });
    m.on("click", () => selectCandidate(c.id, true));
    state.markers[c.id] = m; state.places.addLayer(m);
  });
  if ($("#tgPlaces").checked) state.places.addTo(map);
}

function renderCards(d) {
  const wrap = $("#cards"); wrap.innerHTML = "";
  d.candidates.forEach((c) => wrap.appendChild(cardFor(c)));
}

function cardFor(c) {
  const card = el("div", "card"); card.dataset.id = c.id;
  card.style.borderLeftColor = sevColor[c.severity] || "#374656";
  if (c.verification === "dismissed") card.classList.add("dismissed");

  const row1 = el("div", "row1");
  row1.appendChild(el("span", "rank", String(c.rank)));
  const title = el("div", "title");
  title.innerHTML = `<div class="name">${esc(c.name_en || c.name)}</div><div class="sub">${esc(c.place_type)} · ${c.category}</div>`;
  row1.appendChild(title);
  const sev = el("span", "sev", c.severity); sev.style.color = sevColor[c.severity] || "var(--ink-dim)"; sev.style.borderColor = sevColor[c.severity] || "var(--line-2)";
  row1.appendChild(sev);
  card.appendChild(row1);

  card.appendChild(meter("priority", c.priority, "prio"));
  card.appendChild(meter("uncertainty", c.uncertainty, "unc"));

  const m = el("div", "metrics");
  m.innerHTML =
    `<div>footprint flooded <b>${pct(c.flood_fraction)}%</b></div>` +
    `<div>roads cut <b>${c.flooded_road_km} km</b></div>` +
    `<div>road disruption <b>${pct(c.road_disruption_frac)}%</b></div>` +
    `<div>exposure index <b>${c.exposure_index}/100</b></div>`;
  card.appendChild(m);

  const vchip = el("span", `chip ${c.verification}`, c.verification); vchip.dataset.role = "vchip";
  const th = el("div", "thumbs");
  th.innerHTML =
    `<figure><img src="${dataUrl(c.before_chip)}"><figcaption>BEFORE</figcaption></figure>` +
    `<figure><img src="${dataUrl(c.after_chip)}"><figcaption>AFTER</figcaption></figure>` +
    `<figure><img src="${dataUrl(c.overlay_chip)}"><figcaption>WATER</figcaption></figure>`;
  th.onclick = (e) => { e.stopPropagation(); openModal(c); };
  card.appendChild(th);

  const desc = el("div", "desc" + (c.description ? "" : " empty"), ""); desc.dataset.role = "desc";
  if (c.description) desc.innerHTML = `${esc(c.description)}<span class="src">— ${esc(c.description_source)}</span>`;
  else desc.textContent = "No field note generated yet.";
  card.appendChild(desc);

  const act = el("div", "actions");
  const bDesc = el("button", "btn", c.description ? "Re-note" : "Field note");
  bDesc.onclick = (e) => { e.stopPropagation(); describe(c.id, bDesc); };
  const bOk = el("button", "btn", "Confirm"); bOk.onclick = (e) => { e.stopPropagation(); verify(c.id, "confirmed"); };
  const bNo = el("button", "btn", "Dismiss"); bNo.onclick = (e) => { e.stopPropagation(); verify(c.id, c.verification === "dismissed" ? "pending" : "dismissed"); };
  act.append(bDesc, bOk, bNo); card.appendChild(act);

  card.onclick = () => selectCandidate(c.id, true);
  return card;
}

function meter(label, val, cls) {
  const w = el("div", "meter");
  w.innerHTML = `<div class="lbl"><span>${label}</span><span>${pct(val)}%</span></div>` +
    `<div class="bar ${cls}"><i style="width:${pct(val)}%"></i></div>`;
  return w;
}

function selectCandidate(id, fly) {
  state.selected = id;
  document.querySelectorAll(".card").forEach((c) => c.classList.toggle("sel", c.dataset.id === id));
  document.querySelectorAll(".cand-marker").forEach((m) => m.classList.toggle("sel", m.dataset.id === id));
  const c = state.data.candidates.find((x) => x.id === id);
  if (!c) return;
  Object.values(state.rings).forEach((r) => r.setStyle({ weight: 1, fillOpacity: 0.06 }));
  if (state.rings[id]) state.rings[id].setStyle({ weight: 2.5, fillOpacity: 0.14 });
  const card = document.querySelector(`.card[data-id="${id}"]`);
  if (card) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (fly) map.flyToBounds(latLngBounds(c.bbox_wgs84), { maxZoom: 15, padding: [50, 50], duration: 0.5 });
}

async function describe(id, btn) {
  btn.textContent = "…"; btn.disabled = true;
  try {
    const out = await (await fetch(`${API}/events/${state.event}/describe/${id}`, { method: "POST" })).json();
    const c = state.data.candidates.find((x) => x.id === id);
    c.description = out.text; c.description_source = out.source;
    const desc = document.querySelector(`.card[data-id="${id}"] [data-role="desc"]`);
    desc.classList.remove("empty");
    desc.innerHTML = `${esc(out.text)}<span class="src">— ${esc(out.source)}</span>`;
    toast(out.source.startsWith("vision") ? "Vision note generated" : "Field note generated");
  } catch { toast("Note failed"); }
  btn.textContent = "Re-note"; btn.disabled = false;
}

async function verify(id, status) {
  try {
    await fetch(`${API}/events/${state.event}/verify/${id}?status=${status}`, { method: "POST" });
    const c = state.data.candidates.find((x) => x.id === id); c.verification = status;
    const card = document.querySelector(`.card[data-id="${id}"]`);
    card.classList.toggle("dismissed", status === "dismissed");
    const mk = document.querySelector(`.cand-marker[data-id="${id}"]`);
    if (mk) mk.classList.toggle("dismissed", status === "dismissed");
    toast(`Marked ${status}`);
  } catch { toast("Update failed"); }
}

function openModal(c) {
  $("#mTitle").textContent = `#${c.rank} · ${c.name_en || c.name} (${c.place_type})`;
  $("#mBefore").src = dataUrl(c.before_chip);
  $("#mAfter").src = dataUrl(c.after_chip);
  $("#mOverlay").src = dataUrl(c.overlay_chip);
  const d = $("#mDesc");
  const facts = `${c.severity} · ${pct(c.flood_fraction)}% of footprint flooded · ${c.flooded_road_km} km road cut · exposure ${c.exposure_index}/100 · uncertainty ${pct(c.uncertainty)}%`;
  d.innerHTML = c.description
    ? `<div style="color:var(--ink);margin-bottom:6px">${esc(c.description)}</div><span class="src">— ${esc(c.description_source)}</span><div class="src" style="margin-top:6px">${facts}</div>`
    : `<span style="color:var(--ink-faint)">${facts}. Use “Field note” for a written summary.</span>`;
  $("#modal").classList.add("open");
}

function wireControls() {
  $("#baseSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-base]"); if (!b) return;
    state.base = b.dataset.base;
    document.querySelectorAll("#baseSeg button").forEach((x) => x.classList.toggle("on", x === b));
    renderBase(); renderOverlays();
  });
  $("#tgPriority").onchange = (e) => toggleLayer("priority", e.target.checked);
  $("#tgFlood").onchange = (e) => toggleLayer("flood", e.target.checked);
  $("#tgRoads").onchange = (e) => { if (!state.layers.roads) return; if (e.target.checked) state.layers.roads.addTo(map); else map.removeLayer(state.layers.roads); };
  $("#tgPlaces").onchange = (e) => { if (e.target.checked) state.places.addTo(map); else map.removeLayer(state.places); };

  const op = $("#opacity");
  op.oninput = () => { state.opacity = op.value / 100; $("#opVal").textContent = `${op.value}%`; ["priority", "flood"].forEach((k) => state.layers[k] && state.layers[k].setOpacity(state.opacity)); };

  $("#btnRerun").onclick = async () => { loading(true, "re-running analysis…"); await fetch(`${API}/events/${state.event}/analyze?force=true`, { method: "POST" }); await loadEvent(state.event); toast("Analysis re-run"); };
  $("#btnGeojson").onclick = () => window.open(`${API}/events/${state.event}/export.geojson`, "_blank");
  $("#btnCsv").onclick = () => window.open(`${API}/events/${state.event}/export.csv`, "_blank");

  $("#mClose").onclick = () => $("#modal").classList.remove("open");
  $("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.remove("open"); };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("#modal").classList.remove("open");
    if (!state.data) return;
    const ids = state.data.candidates.map((c) => c.id); const i = ids.indexOf(state.selected);
    if (e.key === "j" || e.key === "ArrowDown") { selectCandidate(ids[Math.min(ids.length - 1, i + 1)] || ids[0], true); e.preventDefault(); }
    if (e.key === "k" || e.key === "ArrowUp") { selectCandidate(ids[Math.max(0, i - 1)] || ids[0], true); e.preventDefault(); }
  });
}

function toggleLayer(key, on) { const lyr = state.layers[key]; if (!lyr) return; if (on) lyr.addTo(map); else map.removeLayer(lyr); }

init();
