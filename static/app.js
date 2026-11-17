"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (cents) => (cents == null ? "–" : "€" + (cents / 100).toFixed(2));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stop = (t) => { t = String(t ?? "").trim(); return /[.!?]$/.test(t) ? t : t + "."; };
const st = (cls, text) => `<span class="st ${esc(cls)}">${esc(text ?? cls)}</span>`;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

// The page can be served by the backend itself (API on the same origin) or as
// static files from GitHub Pages (API somewhere else, set in config.js).
const API = (window.SEAM_API || "").replace(/\/$/, "");

const S = {
  key: null, brands: [], cat: null, styles: [], styleId: null, data: null, tab: "pack",
  viewVersion: null, diffA: null, diffB: null, inbox: [], blobs: new Map(), sketchUrl: null, tour: null,
};

// ---------- api ----------
async function api(path, opts = {}) {
  const headers = { "X-API-Key": S.key, ...(opts.headers || {}) };
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(API + path, { ...opts, headers });
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.blob();
  if (!res.ok) throw new Error((isJson && (data.error || data.detail)) ? (typeof data.detail === "string" ? data.detail : data.error || JSON.stringify(data.detail)) : res.statusText);
  return data;
}

async function blobUrl(path) {
  if (S.blobs.has(path)) return S.blobs.get(path);
  const b = await api(path);
  const url = URL.createObjectURL(b);
  S.blobs.set(path, url);
  return url;
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; t.setAttribute("role", "status");
  document.body.append(t);
  setTimeout(() => t.remove(), 3500);
}

// ---------- boot ----------
// A free server may be asleep. Keep trying for up to 90 seconds and say so.
async function wake(fn) {
  const end = Date.now() + 90000;
  let shown = false;
  for (;;) {
    try { const r = await fn(); if (shown) toast("Server is awake."); return r; }
    catch (e) {
      if (Date.now() > end) throw e;
      if (!shown) { toast("Waking the server. This can take up to a minute on the free plan."); shown = true; }
      await sleep(3000);
    }
  }
}

async function boot() {
  $("#apiLink").href = API + "/docs";
  S.brands = await wake(() => fetch(API + "/api/demo-brands").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }));
  S.cat = await (await fetch(API + "/api/catalogue")).json();
  S.key = store.get("seam.key") || S.brands[0].key;
  if (!S.brands.some((b) => b.key === S.key)) S.key = S.brands[0].key;
  const sel = $("#brandSel");
  sel.innerHTML = S.brands.map((b) => `<option value="${esc(b.key)}">${esc(b.name)}</option>`).join("");
  sel.value = S.key;
  sel.onchange = async () => {
    S.key = sel.value; store.set("seam.key", S.key);
    S.styleId = null; S.data = null; S.blobs.clear();
    await refreshList(); renderDetail();
    toast("Switched brand. Each brand only ever sees its own styles.");
  };
  buildNewDialog();
  $("#newBtn").onclick = () => openNew();
  $("#newBtn2").onclick = () => openNew();
  $("#tourBtn").onclick = () => runTour();
  $("#tourLink").onclick = (e) => { e.preventDefault(); window.scrollTo({ top: 0 }); runTour(); };
  const clock = () => { $("#clock").textContent = "Barcelona · " + new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit" }).format(new Date()); };
  clock(); setInterval(clock, 15000);
  const closing = $(".closing");
  new IntersectionObserver(([e]) => $(".top").classList.toggle("on-paper", e.isIntersecting),
    { rootMargin: "0px 0px -92% 0px" }).observe(closing);
  $("#narrStop").onclick = () => { if (S.tour) S.tour.stopped = true; };
  await refreshList();
  const last = store.get("seam.style." + S.key);
  if (last && S.styles.some((s) => s.id === last)) await openStyle(last);
  setInterval(tick, 900);
}

async function tick() {
  if (document.hidden) return;
  try {
    await refreshBudget();
    if (S.styleId && busy()) { await loadStyle(); await refreshList(); }
  } catch { /* next tick */ }
}

function busy() {
  const d = S.data;
  if (!d) return false;
  return !d.versions.length || d.requests.some((r) => r.status === "pending") || d.images.some((i) => i.status === "queued");
}

async function refreshBudget() {
  const me = await api("/api/me");
  const b = me.budget;
  const pct = Math.min(100, (b.spent_cents / b.limit_cents) * 100);
  $("#budget").innerHTML = `Images ${eur(b.spent_cents)} / ${eur(b.limit_cents)}<div class="bar"><i style="width:${pct}%"></i></div>`;
}

async function refreshList() {
  S.styles = await api("/api/styles");
  $("#emptyHint").hidden = S.styles.length > 0;
  $("#styleList").innerHTML = S.styles.map((s) => `
    <li><button data-id="${s.id}" aria-current="${s.id === S.styleId}">
      <span class="code">${esc(s.code)}</span>
      <span class="name">${esc(s.name)}</span>
      <span class="tags">${s.latest_status ? st(s.latest_status, `v${s.latest_version} ${s.latest_status}`) : st("queued", "drafting")}
        ${s.quotes ? st("quoted", `${s.quotes} quotes`) : ""}</span>
    </button></li>`).join("");
  $("#styleList").querySelectorAll("button").forEach((b) => (b.onclick = () => openStyle(b.dataset.id)));
  await refreshBudget();
}

async function openStyle(id) {
  S.styleId = id; S.viewVersion = null; S.diffA = S.diffB = null; S.sketchUrl = null;
  store.set("seam.style." + S.key, id);
  await loadStyle();
  await refreshList();
  $("#workspace").scrollIntoView({ block: "start" });
}

async function loadStyle() {
  if (!S.styleId) return;
  S.data = await api(`/api/styles/${S.styleId}`);
  const keys = S.data.requests.map((r) => "key=" + encodeURIComponent(r.idempotency_key)).join("&");
  S.inbox = keys ? await (await fetch(API + "/sim/suppliers/inbox?" + keys)).json() : [];
  if (S.tab === "time") S.timeline = await api(`/api/styles/${S.styleId}/timeline`);
  renderDetail();
}

// ---------- detail ----------
function current() { return S.data && S.data.current; }
function approved() { return S.data && S.data.versions.find((v) => v.status === "approved"); }
function latest() { return S.data && S.data.versions[S.data.versions.length - 1]; }

async function renderDetail() {
  const el = $("#detail");
  if (!S.data) { el.hidden = true; $("#emptyMain").hidden = false; return; }
  $("#emptyMain").hidden = true; el.hidden = false;
  const { style, versions, requests, images } = S.data;
  const ap = approved();
  const quoted = requests.filter((r) => r.status === "quoted").length;
  const done = images.filter((i) => i.status === "done").length;
  const step = (n, cond, busyCond, label, text) =>
    `<div class="${cond ? "done" : busyCond ? "busy" : ""}"><span class="n">${n} · ${label}</span>${text}</div>`;

  el.innerHTML = `
    <div class="style-head">
      <div>
        <div class="label">${esc(style.code)}</div>
        <h1 class="huge">${esc(stop(style.name))}</h1>
        <div class="meta"><span>${esc(S.cat.garments.find((g) => g.code === style.garment)?.label || style.garment)}</span>
          ${style.season ? `<span>${esc(style.season)}</span>` : ""}<span>${versions.length} version${versions.length === 1 ? "" : "s"}</span></div>
      </div>
      <img class="sketch" id="sketchImg" alt="Designer's sketch">
    </div>
    <div class="steps">
      ${step("01", true, false, "Sketch", "Uploaded")}
      ${step("02", versions.length > 0, versions.length === 0, "Draft", versions.length ? "Tech pack drafted" : "Reading sketch")}
      ${step("03", !!ap, false, "Approved", ap ? `Version ${ap.version}` : "Waiting")}
      ${step("04", requests.length && quoted === requests.length, requests.length && quoted < requests.length, "Factories", requests.length ? `${quoted} of ${requests.length} quoted` : "Not sent")}
      ${step("05", images.length && done === images.length, images.some((i) => i.status === "queued"), "Images", images.length ? `${done} of ${images.length} ready` : "None yet")}
    </div>
    <div class="tabs" role="tablist">
      ${tabBtn("pack", "Tech pack")}${tabBtn("versions", "Versions", versions.length)}
      ${tabBtn("suppliers", "Factories", requests.length)}${tabBtn("images", "Images", images.length)}${tabBtn("time", "Timeline")}
    </div>
    <div id="tabBody"></div>`;
  el.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
  blobUrl(`/api/styles/${style.id}/sketch`).then((u) => { const i = $("#sketchImg"); if (i) i.src = u; }).catch(() => {});
  const body = $("#tabBody");
  ({ pack: renderPack, versions: renderVersions, suppliers: renderSuppliers, images: renderImages, time: renderTime })[S.tab](body);
}

function tabBtn(id, label, n) {
  return `<button role="tab" data-tab="${id}" aria-selected="${S.tab === id}">${label}${n ? `<span class="count">${n}</span>` : ""}</button>`;
}

async function setTab(t) {
  S.tab = t;
  if (t === "time" && S.styleId) S.timeline = await api(`/api/styles/${S.styleId}/timeline`);
  renderDetail();
}

// ---------- tech pack ----------
async function renderPack(el) {
  const versions = S.data.versions;
  if (!versions.length) { el.innerHTML = `<p class="hint">Reading the sketch and drafting the tech pack.</p>`; return; }
  const n = S.viewVersion || current().version;
  const v = n === current().version ? current() : await api(`/api/styles/${S.styleId}/versions/${n}`);
  const b = v.body;
  const sizes = b.header.sizes;
  el.innerHTML = `
    <div class="bar-row">
      <select id="verSel" aria-label="Version" style="width:auto">${versions.map((x) =>
        `<option value="${x.version}" ${x.version === n ? "selected" : ""}>Version ${x.version} · ${x.status}</option>`).join("")}</select>
      ${st(v.status)}
      <span class="spacer"></span>
      ${v.status === "draft" ? `<button class="link-btn strong" id="approveBtn">Approve version ${v.version} ↗</button>` : ""}
      <button class="link-btn" id="editBtn">Edit as new version ↗</button>
    </div>
    <div class="read-from">Read by <b>${esc(b.read_from.provider || "–")}</b>${b.read_from.confidence != null ? ` · confidence ${Math.round(b.read_from.confidence * 100)}%` : ""}.
      ${esc(stop(b.read_from.notes || ""))} Sizes, grading, fabric use and cost come from fixed rules, never from a guess.</div>
    <div class="figures">
      <div class="lead"><small>Target price, FOB</small><b>${eur(b.cost.target_fob_cents)}</b></div>
      <div><small>Fabric</small><b>${eur(b.cost.fabric_cents)}</b></div>
      <div><small>Trims</small><b>${eur(b.cost.trims_cents)}</b></div>
      <div><small>Cut, make, trim</small><b>${eur(b.cost.cmt_cents)}</b></div>
    </div>
    <h3 class="sec-title">Measurements.<small>cm, flat, base size ${b.header.base_size}</small></h3>
    <div class="tbl-wrap"><table>
      <thead><tr><th>POM</th><th>Point of measure</th><th class="num">Tol ±</th><th class="num">Grade</th>${sizes.map((s) => `<th class="num">${s}</th>`).join("")}</tr></thead>
      <tbody>${b.measurements.map((m) => `<tr><td>${m.pom}</td><td>${esc(m.label)}</td><td class="num">${m.tolerance}</td><td class="num">${m.grade}</td>
        ${sizes.map((s) => `<td class="num ${s === b.header.base_size ? "base" : ""}">${m.values[s]}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
    <h3 class="sec-title">Materials.</h3>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Item</th><th>Description</th><th class="num">Qty</th><th>Unit</th><th class="num">Unit cost</th></tr></thead>
      <tbody>${b.bom.map((x) => `<tr><td>${esc(x.item)}</td><td>${esc(x.description)}</td><td class="num">${x.qty}</td><td>${esc(x.unit)}</td><td class="num">${eur(x.unit_cost_cents)}</td></tr>`).join("")}</tbody>
    </table></div>
    <p class="hint">Fabric per size: ${sizes.map((s) => `${s} ${b.fabric_per_size_m[s]} m`).join(" · ")}.</p>
    <h3 class="sec-title">Construction.</h3>
    <ul class="notes">${b.construction.map((c) => `<li>${esc(stop(c))}</li>`).join("")}</ul>
    ${b.designer_notes.length ? `<h3 class="sec-title">Designer notes.</h3><ul class="notes">${b.designer_notes.map((c) => `<li>${esc(stop(c))}</li>`).join("")}</ul>` : ""}`;
  $("#verSel").onchange = (e) => { S.viewVersion = +e.target.value; renderDetail(); };
  if ($("#approveBtn")) $("#approveBtn").onclick = () => doApprove(v.id);
  $("#editBtn").onclick = () => openEdit(latest().version === v.version ? v : null);
}

async function doApprove(versionId) {
  try {
    const r = await api(`/api/versions/${versionId}/approve`, { method: "POST" });
    toast(r.replaces ? `Approved v${r.version}. ${r.quotes_stale ? "Older quotes are now out of date." : "Older quotes still hold."}` : `Approved v${r.version}.`);
    S.viewVersion = null;
    await loadStyle(); await refreshList();
    return r;
  } catch (e) { toast(stop(e.message)); }
}

// ---------- versions ----------
async function renderVersions(el) {
  const vs = S.data.versions;
  if (vs.length < 1) { el.innerHTML = `<p class="hint">No versions yet.</p>`; return; }
  const a = S.diffA || (vs.length > 1 ? vs[vs.length - 2].version : 1);
  const b = S.diffB || vs[vs.length - 1].version;
  const opts = (sel) => vs.map((v) => `<option value="${v.version}" ${v.version === sel ? "selected" : ""}>v${v.version}</option>`).join("");
  el.innerHTML = `
    <div class="tbl-wrap"><table>
      <thead><tr><th>Version</th><th>Status</th><th>Made by</th><th>Note</th><th>Created</th><th>Approved</th></tr></thead>
      <tbody>${vs.map((v) => `<tr><td>v${v.version}</td><td>${st(v.status)}</td>
        <td>${v.source === "generated" ? "Read from sketch" : "Designer edit"}</td><td>${v.note ? esc(stop(v.note)) : ""}</td>
        <td>${new Date(v.created_at).toLocaleTimeString()}</td><td>${v.approved_at ? new Date(v.approved_at).toLocaleTimeString() : ""}</td></tr>`).join("")}</tbody>
    </table></div>
    <div class="bar-row"><h3 class="sec-title" style="margin:40px 0 0">What changed.</h3><span class="spacer"></span>
      <div class="diff-pick"><select id="dA" aria-label="From version">${opts(a)}</select> → <select id="dB" aria-label="To version">${opts(b)}</select></div></div>
    <div id="diffBox"><p class="hint">Loading.</p></div>`;
  $("#dA").onchange = (e) => { S.diffA = +e.target.value; renderDetail(); };
  $("#dB").onchange = (e) => { S.diffB = +e.target.value; renderDetail(); };
  if (a === b) { $("#diffBox").innerHTML = `<p class="hint">Pick two different versions.</p>`; return; }
  const d = await api(`/api/styles/${S.styleId}/diff?a=${a}&to=${b}`);
  $("#diffBox").innerHTML = (d.changes.length ? d.changes.map((c) => `
    <div class="change"><span class="area">${esc(c.area)}</span>
      <span><b>${esc(c.label)}</b> ${c.from != null ? `<del>${esc(c.from)}</del>` : ""} ${c.to != null ? `→ <ins>${esc(c.to)}</ins>` : ""}
        ${c.sizes ? `<span class="hint">(${c.sizes.join(", ")})</span>` : ""}</span>
      ${c.affects_quote ? st("stale", "Needs a new quote") : st("fresh", "Quote still holds")}</div>`).join("")
    : `<p class="hint">No changes.</p>`) +
    `<div class="change"><span class="area">price</span><span>Target price ${eur(d.cost_from_cents)} → <b>${eur(d.cost_to_cents)}</b></span><span></span></div>`;
}

// ---------- suppliers ----------
function renderSuppliers(el) {
  const ap = approved();
  const reqs = S.data.requests;
  const sentForAp = ap && reqs.filter((r) => r.version_id === ap.id).length;
  el.innerHTML = `
    <div class="bar-row">
      <p class="hint" style="margin:0">${ap ? `Version ${ap.version} is approved.` : "Approve a version first. A draft can't reach a factory. The database refuses it."}</p>
      <span class="spacer"></span>
      <button class="link-btn strong" id="sendBtn" ${!ap || sentForAp ? "disabled" : ""}>${sentForAp ? `Version ${ap.version} already sent` : `Send version ${ap ? ap.version : ""} to all factories ↗`}</button>
    </div>
    ${reqs.length ? `<div class="tbl-wrap"><table>
      <thead><tr><th>Factory</th><th>Version</th><th>Status</th><th class="num">Tries</th><th class="num">Unit price</th><th class="num">Lead time</th><th class="num">MOQ</th><th>Still valid?</th></tr></thead>
      <tbody>${reqs.map((r) => `<tr>
        <td><b>${esc(r.supplier_name)}</b><br><span class="hint">${esc(r.country)}</span></td>
        <td>v${r.version}</td>
        <td>${st(r.status)}${r.status === "pending" && r.last_error ? `<br><span class="hint">${esc(r.last_error.split(":")[0])}, retrying.</span>` : ""}</td>
        <td class="num">${r.attempts}</td>
        <td class="num">${r.quote ? eur(r.quote.unit_price_cents) : "–"}</td>
        <td class="num">${r.quote ? r.quote.lead_time_days + " days" : "–"}</td>
        <td class="num">${r.quote ? r.quote.moq : "–"}</td>
        <td>${r.status !== "quoted" ? "" : r.stale ? `${st("stale", "Out of date")}<br><span class="hint">${esc(r.stale_reason)} changed.</span>` : st("fresh", "Valid")}</td>
      </tr>`).join("")}</tbody></table></div>
    <h3 class="sec-title">Factory side.<small>Test systems</small></h3>
    <p class="hint">Deliveries count every time our request reached the factory. Orders count what the factory actually holds. Retries never become a second order.</p>
    <div class="inbox">${S.inbox.map((i) => `<div><span class="label">${esc(i.supplier_code)}</span><b>${i.orders} order${i.orders === 1 ? "" : "s"}.</b><span class="hint">${i.deliveries} deliver${i.deliveries === 1 ? "y" : "ies"} received.</span></div>`).join("") || `<div class="hint">Nothing has reached a factory yet.</div>`}</div>`
    : `<p class="hint">Not sent to any factory yet. Porto answers straight away. Izmir is down for the first two tries. Dhaka saves the order, but its first reply gets lost.</p>`}`;
  $("#sendBtn").onclick = () => doSend(ap.id);
}

async function doSend(versionId) {
  try {
    const r = await api(`/api/versions/${versionId}/send`, { method: "POST", json: {} });
    toast(r.sent.length ? `Sent to ${r.sent.join(", ")}.` : "Already sent. Nothing new went out.");
    await loadStyle();
  } catch (e) { toast(e.message); }
}

// ---------- images ----------
function renderImages(el) {
  const v = current();
  const imgs = S.data.images;
  el.innerHTML = `
    <div class="bar-row">
      <p class="hint" style="margin:0">Images are made from the tech pack. If a garment looks the same as one already shot, Seam reuses it and charges nothing.</p>
      <span class="spacer"></span>
      <button class="link-btn strong" id="imgBtn" ${!v ? "disabled" : ""}>Make images for version ${v ? v.version : ""} ↗</button>
    </div>
    <div class="imgs">${imgs.map((i) => `
      <figure class="img-card" style="margin:0">
        <div class="frame">${i.status === "done" ? `<img data-src="/api/images/${i.id}.svg" alt="${esc(i.shot)} of version ${i.version}">` : i.status === "failed" ? "Failed. Refunded." : "Rendering."}
          <span class="tagline">${esc({ flat_lay: "Flat lay", on_model: "On model", detail: "Detail" }[i.shot] || i.shot)}</span></div>
        <figcaption class="cap">
          <span style="display:flex;gap:14px;flex-wrap:wrap">${st(i.status)}${i.reused ? st("reused", `Reused ${i.reused}×`) : ""}</span>
          ${i.from_other_style ? `<span>First made for another style with the same look. Cost nothing here.</span>` : ""}
          <span>${esc(i.provenance.model)} · seed ${i.provenance.seed} · ${eur(i.cost_cents)} · version ${i.version}</span>
          <span>Request ${esc(i.provenance.request_hash.slice(0, 12))}.</span>
        </figcaption>
      </figure>`).join("")}</div>
    ${imgs.length ? "" : `<p class="hint">No images yet.</p>`}`;
  el.querySelectorAll("img[data-src]").forEach((img) => blobUrl(img.dataset.src).then((u) => (img.src = u)).catch(() => {}));
  if ($("#imgBtn")) $("#imgBtn").onclick = () => doImages(v.id);
}

async function doImages(versionId, shots) {
  try {
    const r = await api(`/api/versions/${versionId}/images`, { method: "POST", json: shots ? { shots } : {} });
    const reused = r.filter((x) => x.result === "reused").length;
    const queued = r.filter((x) => x.result === "queued").length;
    const over = r.filter((x) => x.result === "over_budget").length;
    toast(stop([queued && `${queued} new`, reused && `${reused} reused for free`, over && `${over} over budget`].filter(Boolean).join(", ")));
    await loadStyle();
    return r;
  } catch (e) { toast(e.message); }
}

// ---------- timeline ----------
function renderTime(el) {
  const t = S.timeline;
  if (!t) { el.innerHTML = `<p class="hint">Loading.</p>`; return; }
  el.innerHTML = `
    <div class="label">Sketch to first factory quote</div>
    <div class="big">${t.sketch_to_first_quote_seconds != null ? `${t.sketch_to_first_quote_seconds}s.` : `Not yet.`}</div>
    <p class="hint">Today this usually takes days of emails and spreadsheets. Every step below is written to the same record.</p>
    <ul class="timeline">${t.events.map((e) => `<li><span class="t">+${e.seconds.toFixed(2)}s</span><span>${esc(stop(e.label))}
      <span class="d">${esc(describe(e))}</span></span></li>`).join("")}</ul>`;
}

function describe(e) { const t = describeRaw(e); return t ? stop(t) : ""; }

function describeRaw(e) {
  const d = e.detail || {};
  switch (e.kind) {
    case "draft_ready": return `v${d.version}, read by ${d.read_by}`;
    case "edited": return `v${d.version}: ${(d.changes || []).join(", ")}`;
    case "approved": return `v${d.version}${d.replaces ? ` replaces v${d.replaces}${d.quotes_stale ? ", old quotes out of date" : ", old quotes still hold"}` : ""}`;
    case "sent_to_suppliers": return `v${d.version} to ${(d.suppliers || []).join(", ")}`;
    case "supplier_retry": return `${d.supplier}, try ${d.attempt}: ${String(d.error || "").split(":")[0]}`;
    case "quote_received": return `${d.supplier}, ${eur(d.unit_price_cents)} on try ${d.attempt}`;
    case "images_requested": return Object.entries(d.shots || {}).map(([k, v]) => `${k} ${v}`).join(", ");
    case "image_ready": return d.shot;
    default: return "";
  }
}

// ---------- new style ----------
const SAMPLES = [
  { file: "tee", garment: "tee", name: "Boxy pocket tee", features: ["chest_pocket"] },
  { file: "shirt", garment: "shirt", name: "Poplin pocket shirt", features: ["chest_pocket"] },
  { file: "dress", garment: "dress", name: "Bias midi dress", features: ["back_zip", "pockets"] },
  { file: "trousers", garment: "trousers", name: "Pleated wool trouser", features: ["zip_fly", "belt_loops"] },
];
let pickedSample = null;

function buildNewDialog() {
  $("#samples").innerHTML = SAMPLES.map((s, i) => `<button type="button" data-i="${i}" aria-pressed="false"><img src="static/samples/${s.file}.svg" alt=""><span>${esc(s.name)}</span></button>`).join("");
  $("#samples").querySelectorAll("button").forEach((b) => (b.onclick = () => pickSample(+b.dataset.i)));
  $("#garmentSel").innerHTML = S.cat.garments.map((g) => `<option value="${g.code}">${esc(g.label)}</option>`).join("");
  $("#fabricSel").innerHTML = S.cat.fabrics.map((f) => `<option value="${f.code}">${esc(f.label)} · ${eur(f.price_per_m_cents)}/m</option>`).join("");
  $("#garmentSel").onchange = () => syncFeatures();
  $("#newCancel").onclick = () => $("#newDlg").close();
  $("#newForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await createFromForm(); $("#newDlg").close(); } catch (err) { $("#newErr").textContent = err.message; }
  };
}

function syncFeatures(selected = []) {
  const g = S.cat.garments.find((x) => x.code === $("#garmentSel").value);
  $("#featureBox").innerHTML = `<legend>Details on the sketch</legend>` + g.features.map((f) =>
    `<label><input type="checkbox" name="feature" value="${f.code}" ${selected.includes(f.code) ? "checked" : ""}> ${esc(f.label)}</label>`).join("");
  $("#fabricSel").value = g.default_fabric;
}

function pickSample(i) {
  pickedSample = SAMPLES[i];
  $("#samples").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.i === i)));
  const f = $("#newForm");
  f.garment.value = pickedSample.garment;
  syncFeatures(pickedSample.features);
  if (!f.name.value) f.name.value = pickedSample.name;
  if (!f.code.value) f.code.value = `SS27-${pickedSample.garment.toUpperCase()}-${Date.now().toString(36).slice(-5).toUpperCase()}`;
  if (!f.season.value) f.season.value = "SS27";
}

function openNew() {
  $("#newForm").reset(); $("#newErr").textContent = ""; pickedSample = null;
  $("#samples").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
  syncFeatures();
  $("#newDlg").showModal();
}

async function createFromForm() {
  const f = $("#newForm");
  const fd = new FormData();
  let file = f.sketch.files[0];
  if (!file && pickedSample) {
    const blob = await (await fetch(`static/samples/${pickedSample.file}.svg`)).blob();
    file = new File([blob], `${pickedSample.file}.svg`, { type: "image/svg+xml" });
  }
  if (!file) throw new Error("Pick a sample or upload a sketch.");
  for (const k of ["code", "name", "garment", "season", "fabric"]) fd.append(k, f[k].value);
  fd.append("features", [...f.querySelectorAll("input[name=feature]:checked")].map((x) => x.value).join(","));
  fd.append("sketch", file);
  const r = await api("/api/styles", { method: "POST", body: fd });
  S.tab = "pack";
  await openStyle(r.id);
  return r.id;
}

// ---------- edit ----------
function openEdit(v) {
  const base = v || current();
  if (!base) return;
  const b = base.body;
  const g = S.cat.garments.find((x) => x.code === b.header.garment);
  const have = b.features.map((x) => x.code);
  $("#editFabric").innerHTML = S.cat.fabrics.map((f) => `<option value="${f.code}" ${f.code === b.header.fabric.code ? "selected" : ""}>${esc(f.label)} · ${eur(f.price_per_m_cents)}/m</option>`).join("");
  $("#editFeatures").innerHTML = `<legend>Details</legend>` + g.features.map((f) =>
    `<label><input type="checkbox" value="${f.code}" ${have.includes(f.code) ? "checked" : ""}> ${esc(f.label)}</label>`).join("");
  $("#editMeasures").innerHTML = `<legend>Size M measurements (cm). Other sizes follow the grade rules.</legend>` + b.measurements.map((m) =>
    `<label><span>${m.pom} · ${esc(m.label)}</span><input type="number" step="0.5" min="1" data-pom="${m.pom}" data-was="${m.values.M}" value="${m.values.M}"></label>`).join("");
  $("#editErr").textContent = "";
  $("#editForm").designer_note.value = "";
  $("#editCancel").onclick = () => $("#editDlg").close();
  $("#editForm").onsubmit = async (e) => {
    e.preventDefault();
    const change = {};
    if ($("#editFabric").value !== b.header.fabric.code) change.fabric = $("#editFabric").value;
    const feats = [...$("#editFeatures").querySelectorAll("input:checked")].map((x) => x.value).sort();
    if (feats.join() !== [...have].sort().join()) change.features = feats;
    const ms = {};
    $("#editMeasures").querySelectorAll("input").forEach((i) => { if (+i.value !== +i.dataset.was) ms[i.dataset.pom] = +i.value; });
    if (Object.keys(ms).length) change.measurements = ms;
    const note = $("#editForm").designer_note.value.trim();
    if (note) change.designer_note = note;
    try {
      const r = await doEdit(change);
      $("#editDlg").close();
      toast(`Saved as draft v${r.version}. Approve it to make it current.`);
    } catch (err) { $("#editErr").textContent = err.message; }
  };
  $("#editDlg").showModal();
}

async function doEdit(change) {
  const r = await api(`/api/styles/${S.styleId}/versions`, { method: "POST", json: change });
  S.viewVersion = r.version; S.tab = "pack";
  await loadStyle(); await refreshList();
  return r;
}

// ---------- guided tour ----------
async function waitFor(fn, ms = 20000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (S.tour?.stopped) throw new Error("stopped");
    await loadStyle();
    if (fn()) return true;
    await sleep(500);
  }
  return false;
}

async function say(n, text, ms = 3200) {
  if (S.tour?.stopped) throw new Error("stopped");
  $("#narrStep").textContent = `Step ${n} of 8`;
  $("#narrText").textContent = text;
  const bar = $("#narration"); bar.classList.remove("flash"); void bar.offsetWidth; bar.classList.add("flash");
  await sleep(ms);
  if (S.tour?.stopped) throw new Error("stopped");
}

function show(tab) {
  S.tab = tab; renderDetail();
  requestAnimationFrame(() => $(".tabs")?.scrollIntoView({ block: "start", behavior: "smooth" }));
}

async function runTour() {
  if (S.tour) return;
  S.tour = { stopped: false };
  $("#narration").hidden = false; $("#tourBtn").disabled = true;
  $("#workspace").scrollIntoView({ block: "start" });
  try {
    await say(1, "A designer drops in a sketch of a boxy tee with a chest pocket.", 2600);
    openNew(); pickSample(0); await sleep(1400);
    $("#newDlg").close();
    await createFromForm();
    await waitFor(() => S.data.versions.length > 0);
    show("pack");
    await say(2, "Seam read the sketch and drafted the tech pack: 7 measurements graded across 5 sizes, a bill of materials and a target price.", 5200);
    await say(3, "Version 1 is a draft. A draft can never reach a factory. The database itself refuses it, not just the app.", 4200);
    await doApprove(current().id);
    show("suppliers");
    await say(4, "Approved. Now it goes to three factories at once.", 2400);
    await doSend(approved().id);
    await sleep(1500);
    await say(4, "Izmir's portal is down. Seam backs off and tries again on its own, with no person chasing it.", 3800);
    await waitFor(() => S.data.requests.length && S.data.requests.every((r) => r.status !== "pending"), 20000);
    await say(4, "Dhaka saved the order but its reply got lost. The retry carried the same key, so Dhaka still holds exactly one order.", 5200);
    show("images");
    await say(5, "Campaign images next: flat lay, on model and a detail shot, each one paid from the brand's budget.", 3000);
    const shots = await doImages(current().id);
    await waitFor(() => S.data.images.length && S.data.images.every((i) => i.status !== "queued"), 15000);
    if (shots && shots.every((x) => x.result === "reused")) {
      await say(5, "This exact look was already shot for another style of this brand, so Seam reused those pictures. Nothing was charged.", 4600);
    }
    await say(5, "Each image records which model made it, with which seed, from which version. Brands need that when they ask who owns a picture.", 4600);
    await say(6, "The designer adds a note for the factory: bar-tack the pocket corners.", 2600);
    const v2 = await doEdit({ designer_note: "Bar-tack the pocket corners." });
    await doApprove(S.data.versions.find((v) => v.version === v2.version).id);
    show("images");
    await doImages(current().id);
    await say(6, "Same garment, same look, so the images are reused at no cost. And a note doesn't change the product, so every quote still holds.", 5200);
    await say(7, "Now the designer switches the fabric to linen.", 2400);
    const v3 = await doEdit({ fabric: "linen" });
    await doApprove(S.data.versions.find((v) => v.version === v3.version).id);
    S.diffA = v2.version; S.diffB = v3.version; show("versions");
    await say(7, "Seam shows exactly what changed and what it does to the price.", 4000);
    show("suppliers");
    await say(7, "The fabric changed what the factory makes, so every old quote is marked out of date, with the reason.", 4600);
    await setTab("time"); $(".tabs")?.scrollIntoView({ block: "start", behavior: "smooth" });
    await say(8, "Sketch to first factory quote, in seconds. Every step is on one record that every tool reads and writes.", 6000);
  } catch (e) {
    if (e.message !== "stopped") toast("Tour stopped: " + e.message);
  } finally {
    S.tour = null; $("#narration").hidden = true; $("#tourBtn").disabled = false;
  }
}

boot().catch((e) => { document.body.insertAdjacentHTML("beforeend", `<p class="toast">Could not load: ${esc(e.message)}</p>`); });
