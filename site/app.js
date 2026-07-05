/* Charlotte Food Grades — map front-end.
   Reads prebuilt data/facilities.geojson + data/meta.json (no live backend). */

const GRADE_COLORS = { A: "#1f9d55", B: "#e2a600", C: "#d64533", ungraded: "#8a95a5" };
const CLT_CENTER = [-80.843, 35.227];

const state = {
  all: null,               // full FeatureCollection
  search: "",
  type: "",
  grades: new Set(["A", "B", "C", "ungraded"]),
  recentOnly: false,
};

const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/positron",
  center: CLT_CENTER,
  zoom: 10.3,
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), "bottom-right");

function gradeBucket(g) {
  if (!g) return "ungraded";
  if (g === "A" || g === "B") return g;
  return "C"; // C or below
}

function applyFilters() {
  if (!state.all) return;
  const q = state.search.toLowerCase();
  const cutoff = Date.now() - 90 * 864e5;
  const feats = state.all.features.filter((f) => {
    const p = f.properties;
    if (state.type && p.type !== state.type) return false;
    if (!state.grades.has(gradeBucket(p.grade))) return false;
    if (state.recentOnly && Date.parse(p.last_date) < cutoff) return false;
    if (q && !(p.name.toLowerCase().includes(q) ||
               (p.address || "").toLowerCase().includes(q))) return false;
    return true;
  });
  map.getSource("facilities").setData({ type: "FeatureCollection", features: feats });
  document.getElementById("count").textContent =
    `${feats.length.toLocaleString()} of ${state.all.features.length.toLocaleString()} facilities`;
}

map.on("load", async () => {
  const [geo, meta] = await Promise.all([
    fetch("data/facilities.geojson").then((r) => r.json()),
    fetch("data/meta.json").then((r) => r.json()),
  ]);
  state.all = geo;
  // features carry a precomputed bucket so paint expressions stay simple
  geo.features.forEach((f) => { f.properties.bucket = gradeBucket(f.properties.grade); });

  document.getElementById("updated").textContent =
    `data updated ${meta.generated_at.slice(0, 10)} · ${meta.facilities.toLocaleString()} facilities`;

  const typeSel = document.getElementById("type-filter");
  Object.entries(meta.types || {})
    .sort((a, b) => b[1] - a[1])
    .forEach(([t, n]) => {
      const o = document.createElement("option");
      o.value = t;
      o.textContent = `${t.replace(/^\d+\s*-\s*/, "")} (${n.toLocaleString()})`;
      typeSel.appendChild(o);
    });

  map.addSource("facilities", {
    type: "geojson",
    data: geo,
    cluster: true,
    clusterMaxZoom: 13,
    clusterRadius: 46,
  });

  map.addLayer({
    id: "clusters",
    type: "circle",
    source: "facilities",
    filter: ["has", "point_count"],
    paint: {
      "circle-color": "#14202b",
      "circle-opacity": 0.82,
      "circle-radius": ["step", ["get", "point_count"], 14, 50, 19, 200, 25, 600, 31],
      "circle-stroke-width": 2,
      "circle-stroke-color": "#ffffff",
    },
  });
  map.addLayer({
    id: "cluster-count",
    type: "symbol",
    source: "facilities",
    filter: ["has", "point_count"],
    layout: {
      "text-field": ["get", "point_count_abbreviated"],
      "text-font": ["Noto Sans Bold"],
      "text-size": 12,
    },
    paint: { "text-color": "#ffffff" },
  });
  map.addLayer({
    id: "points",
    type: "circle",
    source: "facilities",
    filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": ["match", ["get", "bucket"],
        "A", GRADE_COLORS.A, "B", GRADE_COLORS.B, "C", GRADE_COLORS.C,
        GRADE_COLORS.ungraded],
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3.5, 14, 7, 17, 10],
      "circle-stroke-width": 1.4,
      "circle-stroke-color": "#ffffff",
    },
  });

  map.on("click", "clusters", async (e) => {
    const feat = map.queryRenderedFeatures(e.point, { layers: ["clusters"] })[0];
    const zoom = await map.getSource("facilities").getClusterExpansionZoom(feat.properties.cluster_id);
    map.easeTo({ center: feat.geometry.coordinates, zoom: zoom + 0.3 });
  });

  const popup = new maplibregl.Popup({ closeButton: false, offset: 10 });
  map.on("mouseenter", "points", (e) => {
    map.getCanvas().style.cursor = "pointer";
    const p = e.features[0].properties;
    popup.setLngLat(e.features[0].geometry.coordinates)
      .setHTML(`<strong>${esc(p.name)}</strong> · ${p.grade || "ungraded"}`)
      .addTo(map);
  });
  map.on("mouseleave", "points", () => {
    map.getCanvas().style.cursor = "";
    popup.remove();
  });
  map.on("click", "points", (e) => showPanel(e.features[0].properties));
  ["clusters"].forEach((l) => {
    map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
  });

  applyFilters();
});

/* ---------- detail panel ---------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function sparkline(hist) {
  if (!hist || hist.length < 2) return "";
  const pts = hist.slice().reverse(); // oldest -> newest
  const w = 290, h = 56, pad = 12;
  const scores = pts.map((p) => p.s).filter((s) => s != null);
  if (!scores.length) return "";
  const min = Math.min(...scores, 90), max = Math.max(...scores, 100);
  const x = (i) => pad + (i * (w - 2 * pad)) / (pts.length - 1);
  const y = (s) => h - pad - ((s - min) * (h - 2 * pad)) / (max - min || 1);
  const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.s).toFixed(1)}`).join(" ");
  const dots = pts.map((p, i) =>
    `<circle cx="${x(i).toFixed(1)}" cy="${y(p.s).toFixed(1)}" r="2.6" fill="#1466b8"/>`).join("");
  const first = pts[0], last = pts[pts.length - 1];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img"
       aria-label="Score history">
    <polyline points="${line}" fill="none" stroke="#1466b8" stroke-width="1.6" opacity=".55"/>
    ${dots}
    <text x="${pad}" y="${h - 1}">${first.d.slice(0, 7)}</text>
    <text x="${w - pad}" y="${h - 1}" text-anchor="end">${last.d.slice(0, 7)}</text>
  </svg>`;
}

function showPanel(p) {
  const spark = typeof p.spark === "string" ? JSON.parse(p.spark) : p.spark || [];
  const viols = typeof p.violations === "string" ? JSON.parse(p.violations) : p.violations || [];
  const bucket = p.grade ? (["A", "B"].includes(p.grade) ? p.grade : "C") : "na";
  const badge = p.grade ? esc(p.grade) : "n/a";
  document.getElementById("panel-body").innerHTML = `
    <h2>${esc(p.name)}</h2>
    <p class="addr">${esc(p.address)}, ${esc(p.city)} ${esc(p.zip)} · ${esc(String(p.type).replace(/^\d+\s*-\s*/, ""))}</p>
    <div class="gradeline">
      <div class="badge ${bucket}">${badge}</div>
      <div>
        <div class="score">${p.score != null
          ? (p.grade ? `${Number(p.score).toFixed(1)} / 100` : `score ${Number(p.score).toFixed(1)}`)
          : "not scored"}</div>
        <div class="when">inspected ${esc(p.last_date)}</div>
      </div>
    </div>
    ${sparkline(spark)}
    ${viols.length ? `<h3>Recent violations</h3>` + viols.map((v) => `
      <div class="viol ${parseInt(v.code, 10) <= 29 ? "risk_factor" : ""}">
        <p>${esc(v.desc)}</p>
        <p class="meta">item ${esc(v.code)}${v.pts != null ? ` · ${v.pts} pts` : ""}</p>
      </div>`).join("") : `<h3>No violation details on file</h3>`}
    <p class="src">${p.source_url ? `<a href="${esc(p.source_url)}" target="_blank" rel="noopener">Official county record ↗</a>` : ""}</p>
    <p class="prov">Data fetched ${esc(p.fetched_at)}. Unofficial snapshot — grades may have
      changed since; the county record is authoritative.</p>`;
  document.getElementById("panel").hidden = false;
}

document.getElementById("panel-close").addEventListener("click", () => {
  document.getElementById("panel").hidden = true;
});

/* ---------- filter wiring ---------- */

let searchTimer;
document.getElementById("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.search = e.target.value.trim();
    applyFilters();
  }, 150);
});
document.getElementById("type-filter").addEventListener("change", (e) => {
  state.type = e.target.value;
  applyFilters();
});
document.querySelectorAll(".grade-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const g = btn.dataset.grade;
    if (state.grades.has(g)) { state.grades.delete(g); btn.classList.remove("on"); }
    else { state.grades.add(g); btn.classList.add("on"); }
    applyFilters();
  });
});
document.getElementById("recent").addEventListener("change", (e) => {
  state.recentOnly = e.target.checked;
  applyFilters();
});
document.getElementById("controls-toggle").addEventListener("click", () => {
  const c = document.getElementById("controls");
  c.classList.toggle("collapsed");
  document.getElementById("controls-toggle")
    .setAttribute("aria-expanded", String(!c.classList.contains("collapsed")));
});
