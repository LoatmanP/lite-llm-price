import { sigil, vendorLogo } from "./assets/sigils.js";

// ------------------------------------------------------------------ helpers

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const DAY = 86400000;

function fmtUsd(v) {
  if (v == null) return "—";
  let s = v.toFixed(4).replace(/0+$/, "");
  let [w, f = ""] = s.split(".");
  f = f.padEnd(2, "0");
  return `$${Number(w).toLocaleString("en-US")}.${f}`;
}
function fmtPct(v) {
  if (v == null) return "n/a";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}
// Half away from zero, matching the tracker's ROUND_HALF_UP.
function pct(a, b) {
  if (a == null || b == null || b === 0) return null;
  const x = ((a - b) / b) * 100;
  return (Math.sign(x) * Math.round(Math.abs(x) * 10)) / 10;
}
function delta(v, { big = false, suffix = "" } = {}) {
  if (v == null) return `<span class="delta na${big ? " big" : ""}">n/a</span>`;
  const dir = v < 0 ? "down" : v > 0 ? "up" : "flat";
  const word = v < 0 ? "cheaper" : v > 0 ? "pricier" : "same price";
  return `<span class="delta ${dir}${big ? " big" : ""}" title="${Math.abs(v).toFixed(1)}% ${word}"><i class="arrow"></i>${fmtPct(v)}${suffix}</span>`;
}
const dateOf = (iso) => new Date(`${iso}T00:00:00Z`);
function fmtDate(iso, opts = { month: "short", day: "numeric" }) {
  return iso ? dateOf(iso).toLocaleDateString("en-US", { ...opts, timeZone: "UTC" }) : "—";
}
function ago(isoDateTime) {
  if (!isoDateTime) return "never";
  const ms = Date.now() - new Date(isoDateTime).getTime();
  const h = ms / 3600000;
  if (h < 1) return `${Math.max(1, Math.round(ms / 60000))}m ago`;
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
function daysAgo(iso, ref) {
  const d = Math.round((dateOf(ref) - dateOf(iso)) / DAY);
  return d === 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`;
}
function copy(text, btn) {
  navigator.clipboard?.writeText(text).then(() => {
    if (!btn) return;
    const old = btn.textContent;
    btn.textContent = "copied";
    setTimeout(() => (btn.textContent = old), 1200);
  });
}
function globToRe(g) {
  return new RegExp(`^${g.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".")}$`);
}

// AWS Price List verification badge.
const VERIFY = {
  verified: { cls: "ok", label: "✓ AWS", title: "Price from the AWS Price List; LiteLLM agrees" },
  mismatch: { cls: "warn", label: "≠ AWS", title: "Price from the AWS Price List; LiteLLM disagrees" },
  litellm_only: { cls: "ll", label: "LiteLLM", title: "Not in the AWS Price List; LiteLLM price only. Confirm on the AWS pricing page" },
};
function verifyBadge(m) {
  const v = VERIFY[m.verification] || VERIFY.litellm_only;
  return `<span class="vbadge ${v.cls}" title="${v.title}">${v.label}</span>`;
}

// Vendor encoding for marks. Three branded hues plus neutral for everyone else;
// shape is the secondary channel so identity never rests on color alone.
const VSTYLE = {
  anthropic: { color: "#d4683f", shape: "circle", label: "Anthropic" },
  openai: { color: "#1a9e6e", shape: "diamond", label: "OpenAI" },
  amazon: { color: "#3b7bea", shape: "square", label: "Amazon" },
  other: { color: "#59627a", shape: "dot", label: "Other vendors" },
};
const vstyle = (v) => VSTYLE[v] || VSTYLE.other;
const GLOW = { anthropic: "rgba(217,119,87,.30)", openai: "rgba(26,158,110,.30)", amazon: "rgba(59,123,234,.30)" };

function markSvg(shape, x, y, r, fill, extra = "") {
  const ring = `stroke="#0c111d" stroke-width="2"`;
  if (shape === "diamond") {
    const k = r * 1.3;
    return `<path d="M${x} ${y - k}L${x + k} ${y}L${x} ${y + k}L${x - k} ${y}Z" fill="${fill}" ${ring} ${extra}/>`;
  }
  if (shape === "square") {
    const k = r * 0.95;
    return `<rect x="${x - k}" y="${y - k}" width="${2 * k}" height="${2 * k}" rx="2" fill="${fill}" ${ring} ${extra}/>`;
  }
  return `<circle cx="${x}" cy="${y}" r="${shape === "dot" ? r * 0.8 : r}" fill="${fill}" ${ring} ${extra}/>`;
}
function legendKey(v) {
  const s = vstyle(v);
  return `<span><svg viewBox="-7 -7 14 14">${markSvg(s.shape, 0, 0, 4.5, s.color)}</svg>${s.label}</span>`;
}

// ------------------------------------------------------------------ state

const S = {
  data: null,
  byId: new Map(),
  vendorNames: new Map(),
  vendor: "all",
  q: "",
  baseline: "auto",
  watchOnly: false,
  awsOnly: false,
  sort: { key: "first_seen", dir: -1 },
  spot: 0,
  evType: "all",
  openEvent: null,
  today: null,
};

function comparison(m) {
  if (S.baseline === "auto") {
    return { base: m.baseline ? S.byId.get(m.baseline) : null, inp: m.input_pct, out: m.output_pct };
  }
  const b = S.byId.get(S.baseline);
  return { base: b, inp: pct(m.input, b?.input), out: pct(m.output, b?.output) };
}

function matches(m) {
  if (S.vendor !== "all" && m.vendor !== S.vendor) return false;
  if (S.watchOnly && !m.watchlist) return false;
  if (S.awsOnly && m.verification === "litellm_only") return false;
  if (S.q) {
    const q = S.q.toLowerCase();
    if (!m.name.toLowerCase().includes(q) && !m.id.toLowerCase().includes(q) && !(S.vendorNames.get(m.vendor) || "").toLowerCase().includes(q)) return false;
  }
  return true;
}
const filtered = () => S.data.models.filter(matches);

function baselineRule(m) {
  const cfg = S.data.config;
  const bare = m.id.startsWith(cfg.prefix) ? m.id.slice(cfg.prefix.length) : m.id;
  for (const [pattern, target] of Object.entries(cfg.baselines || {})) {
    const re = globToRe(pattern);
    if (re.test(m.id) || re.test(bare)) {
      if (S.byId.has(target)) return { rule: 1, text: `Pattern ${pattern}` };
      break;
    }
  }
  if (m.baseline) return { rule: 2, text: "Closest input price among same-vendor watchlist models" };
  return { rule: 3, text: "No watchlist model from this vendor, so prices only" };
}

// ------------------------------------------------------------------ boot

async function boot() {
  const res = await fetch("data/dashboard.json", { cache: "no-cache" });
  if (!res.ok) throw new Error(`dashboard.json: HTTP ${res.status}`);
  const data = await res.json();
  S.data = data;
  for (const v of data.vendors) S.vendorNames.set(v.id, v.name);
  for (const m of data.models) {
    m.vendorName = S.vendorNames.get(m.vendor);
    S.byId.set(m.id, m);
  }
  for (const e of data.events) e.vendorName = S.vendorNames.get(e.vendor) || e.vendor;
  S.today = data.meta.last_success_date || data.generated_at.slice(0, 10);
  const params = new URLSearchParams(location.search);
  if (S.byId.has(params.get("baseline"))) S.baseline = params.get("baseline");
  if (data.vendors.some((v) => v.id === params.get("vendor"))) S.vendor = params.get("vendor");

  renderStatus();
  renderTicker();
  renderSpotlight();
  renderTiles();
  renderHealth();
  renderLineups();
  renderDeck();
  renderFiltered();
  renderFooter();
  wireGlobal();
  reveal();
  const bootEl = $("#boot");
  bootEl.classList.add("done");
  setTimeout(() => bootEl.remove(), 600);
  const hashModel = decodeURIComponent(location.hash.match(/^#m=(.+)$/)?.[1] || "");
  if (S.byId.has(hashModel)) openDrawer(hashModel);
}

// Keep baseline and vendor in the query string so a view can be shared.
function syncUrl() {
  const params = new URLSearchParams();
  if (S.baseline !== "auto") params.set("baseline", S.baseline);
  if (S.vendor !== "all") params.set("vendor", S.vendor);
  const qs = params.toString();
  history.replaceState(null, "", `${location.pathname}${qs ? `?${qs}` : ""}${location.hash}`);
}

function renderFiltered() {
  syncUrl();
  renderLandscape();
  renderEvents();
  renderDelta();
  renderRegistry();
}

// ------------------------------------------------------------------ status + ticker

function runState() {
  const m = S.data.meta;
  const maxH = S.data.config.heartbeat_max_age_hours || 48;
  const ageH = m.last_success_at ? (Date.now() - new Date(m.last_success_at)) / 3600000 : Infinity;
  if (m.last_status === "failed") return { cls: "bad", label: "Last run failed" };
  if (ageH > maxH) return { cls: "warn", label: "Stale" };
  return { cls: "", label: "Nominal" };
}

function renderStatus() {
  const m = S.data.meta;
  const st = runState();
  $("#status").innerHTML = `
    <span class="chip"><span class="pulse ${st.cls}" aria-hidden="true"></span><b>${st.label}</b></span>
    <span class="chip">Synced <b>${ago(m.last_success_at)}</b></span>
    <span class="chip">Prefix <b>${esc(S.data.config.prefix || "bare")}</b></span>
    <span class="chip">${S.data.models.length} <b>models</b></span>`;
}

function renderTicker() {
  const items = [...S.data.models]
    .sort((a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || a.name.localeCompare(b.name))
    .map((m) => {
      const c = comparison(m);
      return `<span class="tick" data-model="${esc(m.id)}">${sigil(m, 18)}<span class="nm">${esc(m.name)}</span>
        <span>${fmtUsd(m.input)} / ${fmtUsd(m.output)}</span>${c.inp != null ? delta(c.inp) : ""}</span>`;
    })
    .join("");
  $("#ticker").innerHTML = `<div class="ticker-track">${items}<span aria-hidden="true" style="display:contents">${items}</span></div>`;
}

// ------------------------------------------------------------------ spotlight

function spotlightEvents() {
  return S.data.events.filter((e) => e.type === "new_model").slice(0, 8);
}

function renderSpotlight() {
  const list = spotlightEvents();
  const el = $("#spotlight");
  if (!list.length) {
    el.innerHTML = `<div class="panel-title"><span class="eyebrow">Latest arrival</span><h2>No new models yet</h2>
      <p class="sub">The first run saves a snapshot without alerts. New Bedrock models show up here from the next run on.</p></div>`;
    return;
  }
  S.spot = Math.min(S.spot, list.length - 1);
  const e = list[S.spot];
  const m = S.byId.get(e.id) || e;
  const base = e.baseline ? S.byId.get(e.baseline) : null;
  const vs = vstyle(m.vendor);
  el.style.setProperty("--glow", GLOW[m.vendor] || "rgba(99,80,251,.28)");
  const words = m.name.split(" ");
  const nameHtml = words.length > 1 ? `${esc(words.slice(0, -1).join(" "))} <span class="grad">${esc(words.at(-1))}</span>` : `<span class="grad">${esc(m.name)}</span>`;

  const cmpBars = (kind) => {
    const nv = e[kind];
    const bv = base?.[kind];
    const max = Math.max(nv, bv || 0) || 1;
    return `<div class="cmpbars">
      <div class="cmpbar"><span>New</span><span class="track"><span class="fill" style="display:block;width:${(nv / max) * 100}%;background:${vs.color}"></span></span><span>${fmtUsd(nv)}</span></div>
      ${bv != null ? `<div class="cmpbar"><span>Baseline</span><span class="track"><span class="fill" style="display:block;width:${(bv / max) * 100}%;background:#59627a"></span></span><span>${fmtUsd(bv)}</span></div>` : ""}
    </div>`;
  };

  el.innerHTML = `
    <div class="glow"></div>
    <div class="spot-top">
      <span class="eyebrow"><span class="idx">01</span> Latest arrival</span>
      <span class="badge new">✦ New on Bedrock</span>
      <span class="chip">${fmtDate(e.date, { month: "short", day: "numeric", year: "numeric" })} · <b>${daysAgo(e.date, S.today)}</b></span>
      <div class="spot-nav">
        <div class="spot-dots" aria-hidden="true">${list.map((_, i) => `<i class="${i === S.spot ? "on" : ""}"></i>`).join("")}</div>
        <button class="icon-btn" data-spot="-1" aria-label="Previous new model">‹</button>
        <button class="icon-btn" data-spot="1" aria-label="Next new model">›</button>
      </div>
    </div>
    <div class="spot-body">
      <div class="spot-art">
        <div class="orbit o1"></div><div class="orbit o2"></div><div class="disc"></div>
        ${sigil(m, 112, { float: true })}
      </div>
      <div>
        <h2 class="spot-name">${nameHtml}</h2>
        <div class="spot-id"><span>${esc(e.id)}</span><button class="copy" data-copy="${esc(e.id)}">copy</button></div>
        <div class="spot-meta">
          <span class="chip">${vendorLogo(m.vendor, m.vendorName, 14)} <b>${esc(m.vendorName || e.vendorName)}</b></span>
          ${base ? `<span class="chip">vs <b>${esc(base.name)}</b></span>` : `<span class="chip">No baseline · <b>prices only</b></span>`}
          ${S.byId.has(e.id) ? verifyBadge(m) : ""}
          ${m.deprecation_date ? `<span class="badge dep">Deprecates ${esc(m.deprecation_date)}</span>` : ""}
        </div>
      </div>
    </div>
    <div class="readouts">
      ${["input", "output"].map((k) => `
        <div class="readout">
          <div class="readout-label"><span>${k} · per 1M</span>${delta(e[`${k}_pct`])}</div>
          <div class="readout-value">${fmtUsd(e[k])}</div>
          ${cmpBars(k)}
        </div>`).join("")}
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;position:relative">
      <button class="btn primary" data-model="${esc(e.id)}">Open model</button>
      <button class="btn" data-toggle-alert>View alert</button>
      <a class="btn" href="https://aws.amazon.com/bedrock/pricing/" target="_blank" rel="noopener">Confirm on AWS ↗</a>
    </div>
    <pre class="alert-pre" hidden>${esc(e.alert)}</pre>`;
}

// ------------------------------------------------------------------ tiles + health

function sparkline(values, w = 160, h = 26) {
  if (values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values);
  const sx = (i) => (i / (values.length - 1)) * w;
  const sy = (v) => h - 3 - ((v - min) / (max - min || 1)) * (h - 6);
  const d = values.map((v, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)} ${sy(v).toFixed(1)}`).join("");
  const last = values.length - 1;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}L${w} ${h}L0 ${h}Z" fill="rgba(154,216,248,.08)"/>
    <path d="${d}" fill="none" stroke="#6a7590" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
    <circle cx="${sx(last)}" cy="${sy(values[last])}" r="2.5" fill="#9ad8f8"/></svg>`;
}

function logoStack(vendors, max = 6) {
  const shown = vendors.slice(0, max);
  return `<span class="logo-stack">${shown.map((v) => `<span>${vendorLogo(v, S.vendorNames.get(v), 14)}</span>`).join("")}</span>${vendors.length > max ? `<span>+${vendors.length - max}</span>` : ""}`;
}

function renderTiles() {
  const { models, events, model_count_series: series } = S.data;
  const cutoff = new Date(dateOf(S.today) - 30 * DAY).toISOString().slice(0, 10);
  const recent = events.filter((e) => e.date > cutoff);
  const newOnes = recent.filter((e) => e.type === "new_model");
  const changes = recent.filter((e) => e.type === "price_change");
  const cuts = changes.filter((e) => e.input <= e.prev_input && e.output <= e.prev_output).length;
  const before = series.find(([d]) => d >= cutoff)?.[1];
  const growth = before != null ? models.length - before : null;
  const wl = S.data.config.watchlist;
  const missing = S.data.meta.missing_watchlist || [];
  const uniq = (arr) => [...new Set(arr)];

  const tiles = [
    { label: "Models tracked", value: models.length, foot: sparkline(series.map((s) => s[1])) + "" },
    { label: "New · 30 days", value: newOnes.length, foot: newOnes.length ? logoStack(uniq(newOnes.map((e) => e.vendor))) : "None this month" },
    { label: "Price moves · 30d", value: changes.length, foot: changes.length ? `${cuts} cut${cuts === 1 ? "" : "s"} · ${changes.length - cuts} other` : "No changes" },
    { label: "Vendors", value: S.data.vendors.length, foot: logoStack(S.data.vendors.map((v) => v.id), 7) },
    awsTile(),
    { label: "Watchlist", value: `${wl.length - missing.length}/${wl.length}`, foot: missing.length ? `<span class="badge dep">${missing.length} missing</span>` : "All present in file" },
  ];
  tiles[0].foot = `${growth != null ? `<span>${growth >= 0 ? "+" : ""}${growth} vs 30d ago</span>` : ""}`;
  $("#tiles").innerHTML = tiles
    .map((t, i) => `<div class="tile"><span class="tile-label">${t.label}</span><span class="tile-value">${t.value}</span>
      ${i === 0 ? sparkline(series.map((s) => s[1])) : ""}<span class="tile-foot">${t.foot}</span></div>`)
    .join("");
}

function awsTile() {
  const a = S.data.meta.aws_check || { status: "off" };
  const n = S.data.models.length;
  const count = (v) => S.data.models.filter((m) => m.verification === v).length;
  if (a.status === "off" || a.status == null) return { label: "AWS verified", value: "Off", foot: "AWS price check disabled" };
  const ver = count("verified"), mis = count("mismatch"), only = count("litellm_only");
  const foot = a.status === "unavailable"
    ? `<span class="vbadge warn">AWS unreachable</span> using last AWS prices`
    : `${mis ? `<span class="vbadge warn">${mis} differ</span>` : "0 differ"} · ${only} LiteLLM only`;
  return { label: "AWS verified", value: `${ver}/${n}`, foot };
}

function awsChip() {
  const a = S.data.meta.aws_check;
  if (!a || a.status === "off") return "";
  const dates = Object.values(a.published || {}).filter(Boolean).sort();
  const latest = dates.at(-1);
  if (a.status === "unavailable") return `<span class="chip" title="${esc(a.error || "")}"><span class="pulse warn" aria-hidden="true"></span>AWS check <b>unreachable</b></span>`;
  return `<span class="chip" title="Regions: ${esc((a.regions || []).join(", "))}">AWS list <b>${latest ? fmtDate(latest.slice(0, 10)) : "ok"}</b></span>`;
}

function renderHealth() {
  const { runs, meta, snapshot_count, snapshot_dates, config } = S.data;
  const st = runState();
  const fails = runs.filter((r) => r.status === "failed").length;
  const summary = meta.last_summary || {};
  $("#health").innerHTML = `
    <div class="health-row">
      <span class="eyebrow"><span class="idx">02</span> Pipeline</span>
      <span class="chip"><span class="pulse ${st.cls}" aria-hidden="true"></span><b>${st.label}</b></span>
      <span class="chip">Last success <b>${ago(meta.last_success_at)}</b></span>
      ${awsChip()}
      ${summary.skipped_no_price ? `<span class="chip" title="Entries with no input or output token price are skipped">Skipped <b>${summary.skipped_no_price}</b></span>` : ""}
    </div>
    <div class="health-bars" role="img" aria-label="Last ${runs.length} runs, ${fails} failed">
      ${runs.map((r) => `<i class="${r.status === "failed" ? "fail" : r.source === "backfill" ? "backfill" : ""}" title="${r.date} · ${r.status}${r.source === "backfill" ? " (history replay)" : ""}${r.reason ? ` · ${esc(r.reason)}` : ""}"></i>`).join("")}
    </div>
    <div class="health-legend">
      <span><i style="background:rgba(43,182,115,.55)"></i>Daily run ok</span>
      <span><i style="background:rgba(154,216,248,.22)"></i>History replay</span>
      <span><i style="background:var(--bad)"></i>Failed</span>
      <span>${snapshot_count} snapshots since ${fmtDate(snapshot_dates[0], { month: "short", day: "numeric", year: "numeric" })}</span>
      <span>Heartbeat after ${config.heartbeat_max_age_hours}h</span>
    </div>
    ${meta.last_status === "failed" && meta.last_failure ? `<p class="sub" style="margin-top:8px">⚠ ${esc(meta.last_failure.reason)}</p>` : ""}`;
}

// ------------------------------------------------------------------ lineups

const LINEUPS = [
  { vendor: "anthropic", title: "Claude lineup", logo: "claude", families: ["mythos", "fable", "opus", "sonnet", "haiku"], glow: "rgba(217,119,87,.25)", border: "rgba(217,119,87,.5)" },
  { vendor: "openai", title: "GPT lineup", logo: "openai", families: ["astra", "sol", "terra", "luna", null], glow: "rgba(26,158,110,.25)", border: "rgba(26,158,110,.55)" },
];
const FAMILY_BLURB = {
  mythos: "Frontier research tier", fable: "Long-form & creative", opus: "Most capable", sonnet: "Balanced workhorse", haiku: "Fast & light",
  astra: "Flagship", sol: "High capability", terra: "Everyday", luna: "Small & quick", null: "Core GPT",
};

function version(m) {
  const nums = (m.name.match(/\d+(\.\d+)?/g) || []).map(Number);
  return nums.length ? Math.max(...nums) : 0;
}

function renderLineups() {
  $("#lineups").innerHTML = LINEUPS.map((L, li) => {
    const ms = S.data.models.filter((m) => m.vendor === L.vendor);
    if (!ms.length) return "";
    const cards = L.families
      .map((fam) => {
        const group = ms.filter((m) => (m.family || null) === fam);
        if (!group.length) return "";
        const latest = [...group].sort((a, b) => version(b) - version(a) || (b.first_seen || "").localeCompare(a.first_seen || ""))[0];
        const famName = fam ? fam[0].toUpperCase() + fam.slice(1) : "GPT";
        return `<button class="lcard" data-model="${esc(latest.id)}" style="--lglow:${L.glow};--lc:${L.border}">
          ${sigil(latest, 56, { float: true })}
          <span class="lcard-fam">${famName}</span>
          <span class="lcard-model" title="${esc(latest.name)}">${esc(latest.name.replace(/^Claude /, ""))}</span><span class="lcard-blurb">${FAMILY_BLURB[fam]}</span>
          <span class="lcard-price">${fmtUsd(latest.input)} <small>in</small> ${fmtUsd(latest.output)} <small>out</small></span>
          <span class="lcard-count">${group.length} version${group.length > 1 ? "s" : ""}</span>
        </button>`;
      })
      .join("");
    return `<article class="panel">
      <div class="panel-head"><div class="panel-title">
        <span class="eyebrow"><span class="idx">0${li + 3}</span> <img src="assets/logos/${L.logo}.svg" width="14" height="14" alt=""> ${esc(S.vendorNames.get(L.vendor))}</span>
        <h2>${L.title}</h2>
        <p class="sub">Newest model in each family, with list price per 1M tokens.</p>
      </div></div>
      <div class="lineup-cards">${cards}</div>
    </article>`;
  }).join("");
}

// ------------------------------------------------------------------ filter deck

function renderDeck() {
  const counts = S.data.vendors.slice().sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const wl = S.data.config.watchlist.map((id) => S.byId.get(id)).filter(Boolean);
  const others = S.data.models.filter((m) => !m.watchlist).sort((a, b) => a.vendor.localeCompare(b.vendor) || a.name.localeCompare(b.name));
  $("#deck").innerHTML = `
    <div class="vchips">
      <button class="vchip" data-vendor="all" aria-pressed="${S.vendor === "all"}">All <span class="n">${S.data.models.length}</span></button>
      ${counts.map((v) => `<button class="vchip" data-vendor="${v.id}" aria-pressed="${S.vendor === v.id}">${vendorLogo(v.id, v.name, 16)}${esc(v.name)} <span class="n">${v.count}</span></button>`).join("")}
    </div>
    <div class="deck-right">
      <label class="field"><span aria-hidden="true">⌕</span><input id="q" type="search" placeholder="Search models" value="${esc(S.q)}" aria-label="Search models"><kbd>/</kbd></label>
      <label class="field">Baseline
        <select id="baseline" aria-label="Baseline model">
          <option value="auto">Auto · per spec rules</option>
          <optgroup label="Watchlist">${wl.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("")}</optgroup>
          <optgroup label="Any model">${others.map((m) => `<option value="${esc(m.id)}">${esc(m.vendorName)} · ${esc(m.name)}</option>`).join("")}</optgroup>
        </select>
      </label>
      <label class="toggle"><input type="checkbox" id="watchOnly" ${S.watchOnly ? "checked" : ""}> Watchlist only</label>
      <label class="toggle" title="Only models whose price comes from the AWS Price List"><input type="checkbox" id="awsOnly" ${S.awsOnly ? "checked" : ""}> AWS prices only</label>
    </div>`;
  $("#baseline").value = S.baseline;
}

// ------------------------------------------------------------------ landscape

function niceLogTicks(min, max) {
  const out = [];
  for (let e = Math.floor(Math.log10(min)); e <= Math.ceil(Math.log10(max)); e++) {
    for (const k of [1, 3]) {
      const v = k * 10 ** e;
      if (v >= min && v <= max) out.push(v);
    }
  }
  return out;
}
const tickLabel = (v) => (v >= 1 ? `$${v}` : `$${Number(v.toPrecision(1))}`);

function renderLandscape() {
  const el = $("#landscape");
  const all = S.data.models;
  const visible = new Set(filtered().map((m) => m.id));
  const base = S.baseline !== "auto" ? S.byId.get(S.baseline) : null;
  const shownVendors = ["anthropic", "openai", "amazon", "other"];

  if (!el.dataset.ready) {
    el.innerHTML = `
      <div class="panel-head">
        <div class="panel-title">
          <span class="eyebrow"><span class="idx">05</span> Price landscape</span>
          <h2>Input vs output price</h2>
          <p class="sub">Every tracked model, log scales. Lower-left is cheaper on both. Pick a baseline above to mark the region that beats it.</p>
        </div>
        <div class="panel-actions legend">${shownVendors.map(legendKey).join("")}</div>
      </div>
      <div class="chart" id="scatter"></div>`;
    el.dataset.ready = "1";
    new ResizeObserver(() => drawScatter()).observe($("#scatter"));
  }
  S._scatter = { all, visible, base };
  drawScatter();
}

function drawScatter() {
  const host = $("#scatter");
  if (!host || !S._scatter) return;
  const { all, visible, base } = S._scatter;
  const W = Math.max(320, host.clientWidth);
  const H = Math.round(Math.min(520, Math.max(320, W * 0.58)));
  const M = { l: 58, r: 22, t: 14, b: 46 };
  const ins = all.map((m) => m.input), outs = all.map((m) => m.output);
  const xd = [Math.min(...ins) / 1.7, Math.max(...ins) * 1.7];
  const yd = [Math.min(...outs) / 1.7, Math.max(...outs) * 1.7];
  const L = Math.log10;
  const sx = (v) => M.l + ((L(v) - L(xd[0])) / (L(xd[1]) - L(xd[0]))) * (W - M.l - M.r);
  const sy = (v) => H - M.b - ((L(v) - L(yd[0])) / (L(yd[1]) - L(yd[0]))) * (H - M.t - M.b);
  const xt = niceLogTicks(xd[0], xd[1]), yt = niceLogTicks(yd[0], yd[1]);

  let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Scatter of input versus output price per million tokens for ${all.length} models">`;
  svg += `<defs><clipPath id="plot"><rect x="${M.l}" y="${M.t}" width="${W - M.l - M.r}" height="${H - M.t - M.b}"/></clipPath></defs>`;
  svg += `<g class="grid">${xt.map((v) => `<line x1="${sx(v)}" x2="${sx(v)}" y1="${M.t}" y2="${H - M.b}"/>`).join("")}${yt.map((v) => `<line x1="${M.l}" x2="${W - M.r}" y1="${sy(v)}" y2="${sy(v)}"/>`).join("")}</g>`;

  // Output:input ratio guides.
  svg += `<g clip-path="url(#plot)">`;
  for (const k of [1, 5]) {
    const x0 = xd[0], x1 = xd[1];
    svg += `<line class="guide" x1="${sx(x0)}" y1="${sy(x0 * k)}" x2="${sx(x1)}" y2="${sy(x1 * k)}"/>`;
  }
  if (base) {
    const bx = sx(base.input), by = sy(base.output);
    svg += `<rect class="zone" x="${M.l}" y="${by}" width="${bx - M.l}" height="${H - M.b - by}"/>`;
    svg += `<line class="crosshair" x1="${bx}" x2="${bx}" y1="${M.t}" y2="${H - M.b}"/><line class="crosshair" x1="${M.l}" x2="${W - M.r}" y1="${by}" y2="${by}"/>`;
    svg += `<text class="zone-label" x="${M.l + 8}" y="${H - M.b - 8}">Cheaper on both</text>`;
  }
  svg += `</g>`;
  // Guide labels where the line leaves the plot on top.
  for (const k of [1, 5]) {
    const xTop = yd[1] / k;
    if (xTop < xd[1] && xTop > xd[0]) svg += `<text class="guide-label" x="${sx(xTop) - 4}" y="${M.t + 12}" text-anchor="end">out = ${k}× in</text>`;
  }

  svg += `<g class="axis">${xt.map((v) => `<text x="${sx(v)}" y="${H - M.b + 18}" text-anchor="middle">${tickLabel(v)}</text>`).join("")}
    ${yt.map((v) => `<text x="${M.l - 10}" y="${sy(v) + 3.5}" text-anchor="end">${tickLabel(v)}</text>`).join("")}</g>`;
  svg += `<text class="axis-title" x="${W - M.r}" y="${H - 6}" text-anchor="end">Input · USD per 1M →</text>`;
  svg += `<text class="axis-title" transform="translate(14 ${M.t}) rotate(90)" text-anchor="start">Output · USD per 1M →</text>`;

  // Dots: dimmed first, branded vendors last so they sit on top.
  const order = (m) => (visible.has(m.id) ? 10 : 0) + (VSTYLE[m.vendor] ? 1 : 0);
  const pts = [...all].sort((a, b) => order(a) - order(b));
  svg += `<g>`;
  for (const m of pts) {
    const s = vstyle(m.vendor);
    const x = sx(m.input), y = sy(m.output);
    const dim = !visible.has(m.id);
    if (m.watchlist && !dim) svg += `<circle cx="${x}" cy="${y}" r="10" fill="none" stroke="rgba(229,165,10,.55)" stroke-width="1"/>`;
    svg += `<g class="dot${dim ? " dim" : ""}" data-id="${esc(m.id)}">${markSvg(s.shape, x, y, 5.5, s.color)}</g>`;
  }
  svg += `</g>`;

  // Selective labels: the baseline, and the newest arrival.
  const newest = spotlightEvents()[0];
  const labelled = [base, newest && S.byId.get(newest.id)].filter((m, i, a) => m && visible.has(m.id) && a.indexOf(m) === i);
  for (const m of labelled) {
    const x = sx(m.input), y = sy(m.output);
    const right = x < W - 160;
    svg += `<text class="label" x="${x + (right ? 12 : -12)}" y="${y - 10}" text-anchor="${right ? "start" : "end"}">${esc(m.name)}${m === base ? " · baseline" : ""}</text>`;
  }
  svg += `<circle class="hover-ring" r="10" cx="-99" cy="-99"/></svg>`;
  host.innerHTML = svg;

  const svgEl = host.firstChild;
  const ring = $(".hover-ring", host);
  const candidates = all.filter((m) => visible.has(m.id)).map((m) => ({ m, x: sx(m.input), y: sy(m.output) }));
  const nearest = (ev) => {
    const r = svgEl.getBoundingClientRect();
    const px = ev.clientX - r.left, py = ev.clientY - r.top;
    let best = null, bd = 26 * 26;
    for (const c of candidates) {
      const d = (c.x - px) ** 2 + (c.y - py) ** 2;
      if (d < bd) { bd = d; best = c; }
    }
    return best;
  };
  svgEl.addEventListener("mousemove", (ev) => {
    const hit = nearest(ev);
    if (!hit) { hideTip(); ring.setAttribute("cx", -99); svgEl.style.cursor = ""; return; }
    ring.setAttribute("cx", hit.x); ring.setAttribute("cy", hit.y);
    svgEl.style.cursor = "pointer";
    showTip(modelTip(hit.m), ev.clientX, ev.clientY);
  });
  svgEl.addEventListener("mouseleave", () => { hideTip(); ring.setAttribute("cx", -99); });
  svgEl.addEventListener("click", (ev) => { const hit = nearest(ev); if (hit) openDrawer(hit.m.id); });
}

function modelTip(m) {
  const c = comparison(m);
  return `<div class="tt-head">${sigil(m, 30)}<div><div class="tt-name">${esc(m.name)}</div><div class="tt-id">${esc(m.id)}</div></div></div>
    <div class="tt-grid">
      <span class="k">Input</span><span>${fmtUsd(m.input)}</span>${c.base ? delta(c.inp) : "<span></span>"}
      <span class="k">Output</span><span>${fmtUsd(m.output)}</span>${c.base ? delta(c.out) : "<span></span>"}
      <span class="k">Out : in</span><span>${(m.output / m.input).toFixed(1)}×</span><span></span>
    </div>
    <div class="tt-foot">${verifyBadge(m)} ${c.base ? `vs ${esc(c.base.name)}` : "No baseline for this vendor"} · click for detail</div>`;
}

const tip = () => $("#tooltip");
function showTip(html, x, y) {
  const t = tip();
  t.innerHTML = html;
  t.hidden = false;
  const r = t.getBoundingClientRect();
  let left = x + 16, top = y + 16;
  if (left + r.width > innerWidth - 8) left = x - r.width - 16;
  if (top + r.height > innerHeight - 8) top = y - r.height - 16;
  t.style.left = `${Math.max(8, left)}px`;
  t.style.top = `${Math.max(8, top)}px`;
}
function hideTip() { tip().hidden = true; }

// ------------------------------------------------------------------ events

const TYPE = {
  new_model: { cls: "new", label: "✦ New" },
  price_change: { cls: "price", label: "⇅ Price" },
  removed: { cls: "removed", label: "✕ Removed" },
};

function eventMatches(e) {
  if (S.vendor !== "all" && e.vendor !== S.vendor) return false;
  if (S.watchOnly && !e.watchlist) return false;
  if (S.awsOnly && e.price_source !== "aws") return false;
  if (S.q) {
    const q = S.q.toLowerCase();
    if (!e.name.toLowerCase().includes(q) && !e.id.toLowerCase().includes(q)) return false;
  }
  return true;
}

function eventLine(e) {
  if (e.type === "new_model") {
    return `<span>${fmtUsd(e.input)} / ${fmtUsd(e.output)}</span>
      ${e.baseline ? `<span>in ${delta(e.input_pct)} out ${delta(e.output_pct)}</span><span class="arrow-r">vs ${esc(e.baseline_name)}</span>` : `<span class="arrow-r">no baseline</span>`}`;
  }
  if (e.type === "price_change") {
    return `<span>in ${fmtUsd(e.prev_input)} <span class="arrow-r">→</span> ${fmtUsd(e.input)}</span>${delta(pct(e.input, e.prev_input))}
      <span>out ${fmtUsd(e.prev_output)} <span class="arrow-r">→</span> ${fmtUsd(e.output)}</span>${delta(pct(e.output, e.prev_output))}`;
  }
  return `<span class="arrow-r">Last listed ${fmtUsd(e.input)} / ${fmtUsd(e.output)}${e.watchlist ? " · alert sent" : " · logged only"}</span>`;
}

function renderEvents() {
  const el = $("#events");
  const evs = S.data.events.filter(eventMatches);
  const count = (t) => evs.filter((e) => t === "all" || e.type === t).length;
  const list = evs.filter((e) => S.evType === "all" || e.type === S.evType);
  let lastDay = null;
  const items = list.map((e, i) => {
    const key = `${e.date}|${e.type}|${e.id}`;
    const open = S.openEvent === key;
    const day = e.date !== lastDay ? `<li class="fday">${fmtDate(e.date, { weekday: "short", month: "short", day: "numeric" })}</li>` : "";
    lastDay = e.date;
    const m = S.byId.get(e.id) || e;
    return `${day}<li class="fitem${open ? " open" : ""}" data-event="${esc(key)}" tabindex="0" role="button" aria-expanded="${open}">
      ${sigil(m, 36)}
      <div class="fitem-main">
        <div class="fitem-top"><span class="badge ${TYPE[e.type].cls}">${TYPE[e.type].label}</span><span class="fitem-name">${esc(e.name)}</span>${e.watchlist ? `<span class="badge watch">★ Watchlist</span>` : ""}${e.price_source === "aws" ? `<span class="vbadge ok" title="Price from the AWS Price List">AWS</span>` : ""}</div>
        <div class="fitem-line">${eventLine(e)}</div>
        ${open ? `<pre class="alert-pre">${esc(e.alert)}</pre>
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
            <button class="btn" data-copy="${esc(e.alert)}">Copy alert</button>
            ${S.byId.has(e.id) ? `<button class="btn" data-model="${esc(e.id)}">Open model</button>` : ""}
          </div>` : ""}
      </div>
    </li>`;
  }).join("");

  el.innerHTML = `
    <div class="panel-head">
      <div class="panel-title">
        <span class="eyebrow"><span class="idx">06</span> Event stream</span>
        <h2>What changed</h2>
        <p class="sub">The event log. Click an event to see the alert it sends.</p>
      </div>
    </div>
    <div class="tabs" role="tablist" style="margin-bottom:6px">
      ${[["all", "All"], ["new_model", "New"], ["price_change", "Price"], ["removed", "Removed"]].map(([t, l]) =>
        `<button role="tab" data-evtype="${t}" aria-selected="${S.evType === t}">${l} <span class="n">${count(t)}</span></button>`).join("")}
    </div>
    <ul class="feed">${items || `<li class="empty">No events match these filters.</li>`}</ul>`;
}

// ------------------------------------------------------------------ delta bars

function renderDelta() {
  const el = $("#delta");
  const base = S.baseline !== "auto" ? S.byId.get(S.baseline) : null;
  const ms = filtered();
  const rows = ms.map((m) => ({ m, c: comparison(m) })).filter((r) => r.c.inp != null || r.c.out != null);
  const noBase = ms.filter((m) => !rows.find((r) => r.m === m));
  rows.sort((a, b) => (a.c.inp ?? 0) - (b.c.inp ?? 0) || (a.c.out ?? 0) - (b.c.out ?? 0) || a.m.name.localeCompare(b.m.name));

  const maxPos = Math.max(25, ...rows.flatMap((r) => [r.c.inp ?? 0, r.c.out ?? 0]));
  const cap = Math.min(200, maxPos);
  const x0 = 100 / (100 + cap); // zero as a fraction of width
  const bar = (v) => {
    if (v == null) return `<div class="dbar"><span class="zero" style="left:${x0 * 100}%"></span><span class="val" style="left:calc(${x0 * 100}% + 6px)">n/a</span></div>`;
    let barHtml, labelPos;
    if (v < 0) {
      const start = x0 * (1 + v / 100);
      barHtml = `<span class="bar neg" style="left:${start * 100}%;width:${(x0 - start) * 100}%"></span>`;
      labelPos = start > 0.16 ? `right:calc(${(1 - start) * 100}% + 6px)` : `left:calc(${x0 * 100}% + 6px)`;
    } else {
      const clipped = v > cap;
      const end = x0 + (Math.min(v, cap) / cap) * (1 - x0);
      barHtml = v === 0 ? "" : `<span class="bar pos${clipped ? " clip" : ""}" style="left:${x0 * 100}%;width:${(end - x0) * 100}%"></span>`;
      labelPos = end < 0.84 ? `left:calc(${end * 100}% + ${clipped ? 12 : 6}px)` : `right:calc(${(1 - x0) * 100}% + 6px)`;
    }
    return `<div class="dbar"><span class="zero" style="left:${x0 * 100}%"></span>${barHtml}<span class="val" style="${labelPos}">${fmtPct(v)}</span></div>`;
  };

  const baseLabel = base ? `${esc(base.name)} · ${fmtUsd(base.input)} / ${fmtUsd(base.output)}` : "Auto: each model vs its rule-selected baseline";
  el.innerHTML = `
    <div class="panel-head">
      <div class="panel-title">
        <span class="eyebrow"><span class="idx">07</span> Baseline delta</span>
        <h2>Percent difference vs baseline</h2>
        <p class="sub">${baseLabel}. Input and output are never blended, because the input-to-output ratio varies by workload.${cap < maxPos ? ` Bars past +${cap}% are capped; the label shows the true value.` : ""}</p>
      </div>
      <div class="panel-actions dlegend"><span><i style="background:var(--cheaper)"></i>Cheaper</span><span><i style="background:var(--pricier)"></i>Pricier</span></div>
    </div>
    ${rows.length ? `<div class="dgrid">
      <div class="dh"><span>Model</span><span>${rows.length}</span></div>
      <div class="dh"><span>Input Δ</span><span>-100%</span></div>
      <div class="dh"><span>Output Δ</span><span>+${cap}%</span></div>
      <div class="dscroll">
      ${rows.map(({ m, c }) => `<div class="drow" data-model="${esc(m.id)}">
          <div class="dname">${sigil(m, 20)}<span>${esc(m.name)}</span>${base && m.id === base.id ? `<span class="bl">BASELINE</span>` : ""}${S.baseline === "auto" && c.base && c.base.id === m.id ? `<span class="bl">SELF</span>` : ""}</div>
          ${bar(c.inp)}${bar(c.out)}
        </div>`).join("")}
      </div>
    </div>` : `<div class="empty">No models in this view have a baseline.</div>`}
    ${noBase.length ? `<p class="dnote">${noBase.length} model${noBase.length > 1 ? "s" : ""} without a baseline (no watchlist model from ${[...new Set(noBase.map((m) => m.vendorName))].join(", ")}): prices only.</p>` : ""}`;
}

// ------------------------------------------------------------------ registry

const COLS = [
  { key: "watchlist", label: "★", get: (m) => (m.watchlist ? 1 : 0) },
  { key: "name", label: "Model", get: (m) => m.name },
  { key: "vendor", label: "Vendor", get: (m) => m.vendorName },
  { key: "input", label: "Input / 1M", r: true, get: (m) => m.input },
  { key: "output", label: "Output / 1M", r: true, get: (m) => m.output },
  { key: "verification", label: "Source", get: (m) => ({ verified: 0, mismatch: 1, litellm_only: 2 })[m.verification] ?? 2 },
  { key: "ratio", label: "Out:In", r: true, get: (m) => m.output / m.input },
  { key: "dinp", label: "Δ Input", r: true, get: (m) => comparison(m).inp ?? Infinity },
  { key: "dout", label: "Δ Output", r: true, get: (m) => comparison(m).out ?? Infinity },
  { key: "baseline", label: "Baseline", get: (m) => comparison(m).base?.name || "~" },
  { key: "first_seen", label: "First seen", get: (m) => m.first_seen || "" },
  { key: "deprecation_date", label: "Deprecation", get: (m) => m.deprecation_date || "~" },
];

function renderRegistry() {
  const el = $("#registry");
  const col = COLS.find((c) => c.key === S.sort.key) || COLS[1];
  const ms = filtered().sort((a, b) => {
    const va = col.get(a), vb = col.get(b);
    const r = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb));
    return r * S.sort.dir || a.name.localeCompare(b.name);
  });
  const all = S.data.models;
  const lmin = Math.log10(Math.min(...all.map((m) => m.input))), lmax = Math.log10(Math.max(...all.map((m) => m.output)));
  const meter = (v) => `<div class="meter"><i style="width:${Math.max(3, ((Math.log10(v) - lmin) / (lmax - lmin)) * 100)}%"></i></div>`;

  el.innerHTML = `
    <div class="panel-head">
      <div class="panel-title">
        <span class="eyebrow"><span class="idx">08</span> Model registry</span>
        <h2>All tracked models</h2>
        <p class="sub">${ms.length} of ${all.length} models on prefix <span class="mono">${esc(S.data.config.prefix)}</span>, from the ${fmtDate(S.today, { month: "short", day: "numeric", year: "numeric" })} snapshot. The table view of every chart on this page.</p>
      </div>
      <div class="panel-actions">
        <a class="btn" href="data/snapshot-latest.csv" download>Snapshot CSV</a>
        <a class="btn" href="data/events.csv" download>Event log CSV</a>
        <a class="btn" href="data/dashboard.json">JSON</a>
      </div>
    </div>
    <div class="table-wrap"><table class="reg">
      <thead><tr>${COLS.map((c) => `<th class="${c.r ? "r" : ""}" ${S.sort.key === c.key ? `aria-sort="${S.sort.dir > 0 ? "ascending" : "descending"}"` : ""}><button data-sort="${c.key}">${c.label}${S.sort.key === c.key ? (S.sort.dir > 0 ? " ↑" : " ↓") : ""}</button></th>`).join("")}</tr></thead>
      <tbody>${ms.map((m) => {
        const c = comparison(m);
        return `<tr data-model="${esc(m.id)}" tabindex="0">
          <td>${m.watchlist ? `<span class="star" title="Watchlist">★</span>` : ""}</td>
          <td><div class="mcell">${sigil(m, 30)}<div><div class="mname">${esc(m.name)}</div><div class="mid">${esc(m.id)}</div></div></div></td>
          <td><span class="vcell">${vendorLogo(m.vendor, m.vendorName, 16)}${esc(m.vendorName)}</span></td>
          <td class="r"><span class="price">${fmtUsd(m.input)}</span>${meter(m.input)}</td>
          <td class="r"><span class="price">${fmtUsd(m.output)}</span>${meter(m.output)}</td>
          <td>${verifyBadge(m)}</td>
          <td class="r small">${(m.output / m.input).toFixed(1)}×</td>
          <td class="r">${c.base ? delta(c.inp) : `<span class="muted">—</span>`}</td>
          <td class="r">${c.base ? delta(c.out) : `<span class="muted">—</span>`}</td>
          <td class="small">${c.base ? (c.base.id === m.id ? "itself" : esc(c.base.name)) : `<span class="muted">none</span>`}</td>
          <td class="small">${fmtDate(m.first_seen, { month: "short", day: "numeric", year: "2-digit" })}</td>
          <td>${m.deprecation_date ? `<span class="badge dep">${esc(m.deprecation_date)}</span>` : `<span class="muted">—</span>`}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="${COLS.length}" class="empty">No models match these filters.</td></tr>`}</tbody>
    </table></div>`;
}

// ------------------------------------------------------------------ drawer

function stepChart(points, idx, endDate) {
  const W = 220, H = 90, pad = { l: 4, r: 4, t: 14, b: 16 };
  const t0 = dateOf(points[0][0]).getTime();
  const t1 = Math.max(dateOf(endDate).getTime(), t0 + DAY);
  const max = Math.max(...points.map((p) => p[idx])) * 1.2 || 1;
  const sx = (iso) => pad.l + ((dateOf(iso).getTime() - t0) / (t1 - t0)) * (W - pad.l - pad.r);
  const sy = (v) => H - pad.b - (v / max) * (H - pad.t - pad.b);
  let d = `M${sx(points[0][0])} ${sy(points[0][idx])}`;
  for (let i = 1; i < points.length; i++) d += `H${sx(points[i][0])}V${sy(points[i][idx])}`;
  d += `H${sx(endDate)}`;
  const last = points.at(-1)[idx];
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Price history">
    <line x1="${pad.l}" x2="${W - pad.r}" y1="${H - pad.b}" y2="${H - pad.b}" stroke="rgba(148,163,190,.18)"/>
    <path d="${d}V${H - pad.b}H${sx(points[0][0])}Z" fill="rgba(123,108,255,.10)"/>
    <path d="${d}" fill="none" stroke="#7b6cff" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    <circle cx="${sx(endDate)}" cy="${sy(last)}" r="4" fill="#7b6cff" stroke="#0c111d" stroke-width="2"/>
    <text x="${pad.l}" y="${H - 3}" fill="#6a7590" font-size="9" font-family="Geist Mono, monospace">${fmtDate(points[0][0])}</text>
    <text x="${W - pad.r}" y="${H - 3}" fill="#6a7590" font-size="9" font-family="Geist Mono, monospace" text-anchor="end">${fmtDate(endDate)}</text>
  </svg>`;
}

let lastFocus = null;
function openDrawer(id) {
  const m = S.byId.get(id);
  if (!m) return;
  hideTip();
  lastFocus = document.activeElement;
  const c = comparison(m);
  const rule = baselineRule(m);
  const evs = S.data.events.filter((e) => e.id === id);
  const endDate = S.data.snapshot_dates[1];
  const changes = m.history.slice(1).map((p, i) => {
    const prev = m.history[i];
    return `<li><span class="mono">${p[0]}</span> · in ${fmtUsd(prev[1])} → ${fmtUsd(p[1])} · out ${fmtUsd(prev[2])} → ${fmtUsd(p[2])}</li>`;
  }).join("");

  const panel = $(".drawer-panel");
  panel.innerHTML = `
    <button class="icon-btn dr-close" data-close aria-label="Close">✕</button>
    <div class="dr-head">
      ${sigil(m, 84, { float: true })}
      <div>
        <span class="eyebrow">${vendorLogo(m.vendor, m.vendorName, 13)} ${esc(m.vendorName)}${m.family ? ` · ${esc(m.family)}` : ""}</span>
        <h3>${esc(m.name)}</h3>
        <div class="spot-id"><span>${esc(m.id)}</span><button class="copy" data-copy="${esc(m.id)}">copy</button></div>
        <div class="spot-meta">
          ${m.watchlist ? `<span class="badge watch">★ Watchlist</span>` : ""}
          ${m.deprecation_date ? `<span class="badge dep">Deprecates ${esc(m.deprecation_date)}</span>` : ""}
        </div>
      </div>
    </div>
    <div class="readouts">
      ${["input", "output"].map((k) => `<div class="readout">
        <div class="readout-label"><span>${k} · per 1M</span>${c.base ? delta(k === "input" ? c.inp : c.out) : ""}</div>
        <div class="readout-value">${fmtUsd(m[k])}</div>
        ${c.base ? `<div class="cmpbar"><span>Baseline</span><span></span><span>${fmtUsd(c.base[k])}</span></div>` : ""}
      </div>`).join("")}
    </div>
    <section class="dr-section">
      <h4>Price source</h4>
      <dl class="kv">
        <dt>Reported price</dt><dd>${verifyBadge(m)} ${m.verification === "litellm_only" ? "LiteLLM price file only. Not in the AWS Price List; confirm on the AWS pricing page." : "AWS Price List"}</dd>
        <dt>LiteLLM says</dt><dd class="mono">${fmtUsd(m.litellm_input)} / ${fmtUsd(m.litellm_output)}${m.verification === "mismatch" ? " (differs from AWS)" : ""}</dd>
      </dl>
    </section>
    <section class="dr-section">
      <h4>Baseline</h4>
      <dl class="kv">
        <dt>Compared against</dt><dd>${c.base ? `<a href="#" data-model="${esc(c.base.id)}">${esc(c.base.name)}</a> <span class="mono muted">${esc(c.base.id)}</span>` : "None"}</dd>
        <dt>Selected by</dt><dd>${S.baseline === "auto" ? `Rule ${rule.rule}: ${esc(rule.text)}` : "Your baseline choice in the filter bar"}</dd>
      </dl>
    </section>
    <section class="dr-section">
      <h4>Price history</h4>
      <div class="hist">
        <div class="box"><div class="box-t"><span>Input</span><span>${fmtUsd(m.input)}</span></div>${stepChart(m.history, 1, endDate)}</div>
        <div class="box"><div class="box-t"><span>Output</span><span>${fmtUsd(m.output)}</span></div>${stepChart(m.history, 2, endDate)}</div>
      </div>
      <p class="sub" style="margin-top:8px;font-size:12px">${changes ? "" : `No list-price change since first seen on ${fmtDate(m.first_seen, { month: "short", day: "numeric", year: "numeric" })}.`}</p>
      ${changes ? `<ul class="sub" style="font-size:12px;padding-left:18px;margin:6px 0 0">${changes}</ul>` : ""}
    </section>
    <section class="dr-section">
      <h4>Events</h4>
      ${evs.length ? evs.map((e) => `<div style="margin-bottom:12px"><div class="fitem-top" style="margin-bottom:6px"><span class="badge ${TYPE[e.type].cls}">${TYPE[e.type].label}</span><span class="mono" style="font-size:12px;color:var(--text-2)">${e.date}</span></div><pre class="alert-pre">${esc(e.alert)}</pre></div>`).join("")
        : `<p class="sub">No events. This model was already in the file when tracking began on ${fmtDate(S.data.meta.first_run_date, { month: "short", day: "numeric", year: "numeric" })}.</p>`}
    </section>
    <section class="dr-section">
      <h4>Details</h4>
      <dl class="kv">
        <dt>First seen</dt><dd>${m.first_seen || "—"}</dd>
        <dt>Deprecation</dt><dd>${m.deprecation_date || "None listed"}</dd>
        <dt>Output : input</dt><dd>${(m.output / m.input).toFixed(2)}×</dd>
        <dt>Sources</dt><dd>LiteLLM price file for detection; AWS Price List for the price where it lists the model. <a href="https://aws.amazon.com/bedrock/pricing/" target="_blank" rel="noopener" style="color:var(--accent-3)">Confirm on AWS ↗</a></dd>
      </dl>
    </section>`;
  history.replaceState(null, "", `${location.pathname}${location.search}#m=${encodeURIComponent(id)}`);
  const dr = $("#drawer");
  dr.classList.add("open");
  dr.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  panel.scrollTop = 0;
  panel.focus();
}
function closeDrawer() {
  const dr = $("#drawer");
  if (!dr.classList.contains("open")) return;
  dr.classList.remove("open");
  dr.setAttribute("aria-hidden", "true");
  history.replaceState(null, "", `${location.pathname}${location.search}`);
  document.body.style.overflow = "";
  lastFocus?.focus?.();
}

// ------------------------------------------------------------------ footer

function renderFooter() {
  const cfg = S.data.config;
  $("#footer").innerHTML = `
    <div>
      <h4>How it works</h4>
      <p>A daily scheduled pipeline fetches the LiteLLM price file, checks that it still holds at least 80% of the last good run's Bedrock entries, keeps Bedrock chat models on the <span class="mono">${esc(cfg.prefix)}</span> prefix, and diffs them against the last good snapshot. New models are compared with a baseline; every event is alerted and logged.</p>
      <p>Each price is then checked against AWS's public Price List. Where AWS lists the model, the AWS price is reported and marked <span class="vbadge ok">✓ AWS</span> (or <span class="vbadge warn">≠ AWS</span> when LiteLLM disagrees). Otherwise it is marked <span class="vbadge ll">LiteLLM</span>.</p>
      <p>A failed run never overwrites the last good snapshot.</p>
    </div>
    <div>
      <h4>Known limits</h4>
      <ul>
        <li><b>Lag.</b> LiteLLM has no update schedule; a model can appear days after AWS launches it.</li>
        <li><b>Accuracy.</b> The file is community-maintained. Prices marked LiteLLM are not in the AWS Price List; confirm them on the AWS pricing page before acting.</li>
        <li><b>List price only.</b> No negotiated, batch, caching or provisioned-throughput rates.</li>
        <li><b>Base tier only.</b> Long-context surcharges (above 200K input tokens) are not shown.</li>
        <li><b>Price is not cost.</b> A cheaper model can use more tokens for the same task.</li>
      </ul>
    </div>
    <div>
      <h4>Configuration</h4>
      <p>Prefix <span class="mono">${esc(cfg.prefix)}</span> · ${cfg.watchlist.length} watchlist models · ${Object.keys(cfg.baselines).length} baseline patterns.</p>
      <ul>${Object.entries(cfg.baselines).map(([p, t]) => `<li><span class="mono" style="font-size:12px">${esc(p)} → ${esc(S.byId.get(t)?.name || t)}</span></li>`).join("")}</ul>
      <p style="margin-top:8px"><a href="${esc(cfg.source_url)}" target="_blank" rel="noopener">LiteLLM price file ↗</a> · <a href="https://aws.amazon.com/bedrock/pricing/" target="_blank" rel="noopener">Amazon Bedrock pricing ↗</a></p>
    </div>
    <div class="fine">Built ${new Date(S.data.generated_at).toUTCString()}. Prices in USD per 1M tokens, on-demand list price. Vendor logos are trademarks of their owners, via Lobe Icons (MIT). The model critters and celestial glyphs are original artwork for this dashboard, not official vendor art.</div>`;
}

// ------------------------------------------------------------------ wiring

function wireGlobal() {
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    const cp = t.closest("[data-copy]");
    if (cp) { ev.stopPropagation(); copy(cp.dataset.copy, cp); return; }
    if (t.closest("[data-close]")) { closeDrawer(); return; }
    const spot = t.closest("[data-spot]");
    if (spot) {
      const n = spotlightEvents().length;
      S.spot = (S.spot + Number(spot.dataset.spot) + n) % n;
      renderSpotlight();
      return;
    }
    if (t.closest("[data-toggle-alert]")) {
      const pre = $("#spotlight .alert-pre");
      pre.hidden = !pre.hidden;
      t.closest("[data-toggle-alert]").textContent = pre.hidden ? "View alert" : "Hide alert";
      return;
    }
    const v = t.closest("[data-vendor]");
    if (v) {
      S.vendor = v.dataset.vendor === S.vendor && S.vendor !== "all" ? "all" : v.dataset.vendor;
      document.querySelectorAll(".vchip").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.vendor === S.vendor)));
      renderFiltered();
      return;
    }
    const et = t.closest("[data-evtype]");
    if (et) { S.evType = et.dataset.evtype; renderEvents(); return; }
    const sort = t.closest("[data-sort]");
    if (sort) {
      const k = sort.dataset.sort;
      S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : ["input", "output", "ratio", "dinp", "dout", "name", "vendor", "baseline"].includes(k) ? 1 : -1 };
      renderRegistry();
      return;
    }
    const model = t.closest("[data-model]");
    if (model && !t.closest("pre")) { ev.preventDefault(); openDrawer(model.dataset.model); return; }
    const evItem = t.closest("[data-event]");
    if (evItem && !t.closest("pre")) {
      S.openEvent = S.openEvent === evItem.dataset.event ? null : evItem.dataset.event;
      const scroll = $(".feed")?.scrollTop;
      renderEvents();
      if (scroll != null) $(".feed").scrollTop = scroll;
    }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeDrawer();
    if (ev.key === "/" && document.activeElement?.tagName !== "INPUT") { ev.preventDefault(); $("#q")?.focus(); }
    if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches?.("tr[data-model], .fitem")) { ev.preventDefault(); ev.target.click(); }
  });

  $("#deck").addEventListener("input", (ev) => {
    if (ev.target.id === "q") { S.q = ev.target.value.trim(); renderFiltered(); }
  });
  $("#deck").addEventListener("change", (ev) => {
    if (ev.target.id === "baseline") { S.baseline = ev.target.value; renderFiltered(); renderTicker(); }
    if (ev.target.id === "watchOnly") { S.watchOnly = ev.target.checked; renderFiltered(); }
    if (ev.target.id === "awsOnly") { S.awsOnly = ev.target.checked; renderFiltered(); }
  });
}

function reveal() {
  const els = document.querySelectorAll("main > *, .hero > *, .hero-side > *, .lineups > *, .grid-2 > *");
  if (!("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { rootMargin: "0px 0px -40px 0px" });
  els.forEach((el) => { if (el.id !== "deck") { el.classList.add("reveal"); io.observe(el); } });
}

boot().catch((err) => {
  console.error(err);
  $("#boot").innerHTML = `<span>Could not load price data</span><span style="text-transform:none;letter-spacing:0">${esc(err.message)}</span>`;
});
