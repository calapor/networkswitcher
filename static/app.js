"use strict";

const $ = (id) => document.getElementById(id);

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `request failed (${r.status})`);
  return data;
}

// --- status -----------------------------------------------------------------

let pollTimer = null;

function fmtBytes(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i < 2 ? 0 : 1)} ${units[i]}`;
}

function renderStatus(s) {
  const dot = $("dot");
  const text = $("status-text");
  const action = s.action || {};

  if (s.error) {
    dot.className = "dot bad";
    text.textContent = "wpa_supplicant not reachable";
  } else if (s.internet) {
    dot.className = "dot ok";
    text.textContent = "Connected — internet OK";
  } else if (s.wpa_state === "COMPLETED") {
    dot.className = "dot warn";
    text.textContent = "Associated, but no internet";
  } else {
    dot.className = "dot bad";
    text.textContent = "Not connected to upstream WiFi";
  }

  $("cur-ssid").textContent = s.ssid || (s.error ? "—" : "(none)");
  $("cur-ip").textContent = s.ip || "—";
  $("cur-net").textContent = s.error ? "—" : s.internet ? "✅ reachable" : "❌ none";
  $("cur-eth").textContent = s.eth0_up ? "✅ linked" : "❌ down";
  $("cur-rx").textContent = s.error ? "—" : fmtBytes(s.rx_bytes);
  $("cur-tx").textContent = s.error ? "—" : fmtBytes(s.tx_bytes);
  $("all-rx").textContent   = s.error ? "—" : fmtBytes(s.all_time_rx_bytes);
  $("all-tx").textContent   = s.error ? "—" : fmtBytes(s.all_time_tx_bytes);

  const banner = $("action-banner");
  // A "failed" verdict can go stale: a slow AP may associate just after the
  // worker gave up, and the link comes up anyway. Hide it once we're online.
  if (action.step === "failed" && action.error && !s.internet) {
    banner.className = "banner err";
    banner.textContent = "";
    const msg = document.createElement("span");
    msg.className = "banner-msg";
    msg.textContent = `Switch to “${action.target}” failed: ${action.error}`;
    banner.appendChild(msg);
    const x = document.createElement("button");
    x.className = "dismiss";
    x.setAttribute("aria-label", "Dismiss");
    x.textContent = "×";
    x.onclick = dismissAction;
    banner.appendChild(x);
    banner.classList.remove("hidden");
  } else if (action.busy) {
    banner.className = "banner work";
    banner.textContent = `Switching to “${action.target}” — ${action.step}…`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }

  // poll faster while a switch is running
  const interval = action.busy ? 1500 : 4000;
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refreshStatus, interval);
}

async function dismissAction() {
  $("action-banner").classList.add("hidden");
  try {
    await postJSON("/api/action/dismiss");
  } catch (e) {
    /* hidden locally either way; next poll reflects server state */
  }
}

async function refreshStatus() {
  try {
    const s = await getJSON("/api/status");
    renderStatus(s);
    if (!(s.action && s.action.busy)) loadSaved();
  } catch (e) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(refreshStatus, 4000);
  }
}

// --- saved networks ---------------------------------------------------------

async function loadSaved() {
  const ul = $("saved");
  try {
    const nets = await getJSON("/api/networks/saved");
    if (nets.error) throw new Error(nets.error);
    if (!nets.length) {
      ul.innerHTML = '<li class="muted">No saved networks yet.</li>';
      return;
    }
    ul.innerHTML = "";
    for (const n of nets) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = n.ssid;
      li.appendChild(name);

      if (n.current) {
        const tag = document.createElement("span");
        tag.className = "tag current";
        tag.textContent = "● connected";
        li.appendChild(tag);
      } else {
        const connect = document.createElement("button");
        connect.className = "btn small";
        connect.textContent = "Connect";
        connect.onclick = () => doConnect(n.id, connect);
        li.appendChild(connect);
      }

      const forget = document.createElement("button");
      forget.className = "btn small danger";
      forget.textContent = "Forget";
      forget.onclick = () => doForget(n.id, n.ssid);
      li.appendChild(forget);

      ul.appendChild(li);
    }
  } catch (e) {
    ul.innerHTML = `<li class="muted">Could not load: ${e.message}</li>`;
  }
}

async function doConnect(id, btn) {
  const delay = Math.max(0, parseInt($("switch-delay").value, 10) || 0);
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    await postJSON("/api/connect", { id, delay });
    refreshStatus();
  } catch (e) {
    alert(e.message);
    if (btn) { btn.disabled = false; btn.textContent = "Connect"; }
  }
}

async function doForget(id, ssid) {
  if (!confirm(`Forget “${ssid}”?`)) return;
  try {
    await postJSON("/api/forget", { id });
    loadSaved();
  } catch (e) {
    alert(e.message);
  }
}

// --- scan -------------------------------------------------------------------

async function doScan() {
  const btn = $("scan-btn");
  const ul = $("scan");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  ul.innerHTML = '<li class="muted">Scanning…</li>';
  try {
    const results = await getJSON("/api/networks/scan");
    if (results.error) throw new Error(results.error);
    if (!results.length) {
      ul.innerHTML = '<li class="muted">No networks found.</li>';
      return;
    }
    ul.innerHTML = "";
    for (const ap of results) {
      const li = document.createElement("li");

      const sig = document.createElement("span");
      sig.className = "signal";
      sig.textContent = `${ap.signal}%`;
      li.appendChild(sig);

      const name = document.createElement("span");
      name.className = "name";
      name.textContent = ap.ssid;
      li.appendChild(name);

      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = ap.security;
      li.appendChild(tag);

      const use = document.createElement("button");
      use.className = "btn small";
      use.textContent = "Use";
      use.onclick = () => {
        $("add-ssid").value = ap.ssid;
        $("add-psk").value = "";
        $("add-hidden").checked = false;
        $("add-psk").focus();
        $("add-form").scrollIntoView({ behavior: "smooth", block: "center" });
      };
      li.appendChild(use);

      ul.appendChild(li);
    }
  } catch (e) {
    ul.innerHTML = `<li class="muted">Scan failed: ${e.message}</li>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Scan";
  }
}

// --- add --------------------------------------------------------------------

async function doAdd(ev) {
  ev.preventDefault();
  const ssid = $("add-ssid").value.trim();
  const psk = $("add-psk").value;
  const hidden = $("add-hidden").checked;
  if (!ssid) return;
  const btn = ev.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await postJSON("/api/networks", { ssid, psk, hidden });
    $("add-psk").value = "";
    refreshStatus();
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
}

// --- history chart ----------------------------------------------------------

let _historyChart = null;
let _activeView = "week";
let _historyData = null;

function dayLabel(key) {
    const [y, m, d] = key.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
}

function weekLabel(key) {
    const [year, week] = key.split("-W").map(Number);
    const jan4 = new Date(year, 0, 4);
    const dow = jan4.getDay() || 7;
    const mon = new Date(jan4);
    mon.setDate(jan4.getDate() - dow + 1 + (week - 1) * 7);
    return mon.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function monthLabel(key) {
    const [year, month] = key.split("-").map(Number);
    return new Date(year, month - 1).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
}

// Each time view drills into its sub-period: a week shows days, a month shows
// weeks, a year shows months. `max` caps how many trailing buckets to plot.
const VIEWS = {
    week:  { source: "days",   max: 14, label: dayLabel },
    month: { source: "weeks",  max: 8,  label: weekLabel },
    year:  { source: "months", max: 12, label: monthLabel },
};

// Per-network line colours; "All networks" uses the foreground colour.
const NET_COLORS = ["#4f8cff", "#34c759", "#ff9f0a", "#bf5af2", "#5ac8fa", "#ff453a", "#ffd60a", "#ff6482"];
const COMMON_OPTS = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
        legend: { labels: { color: "#e8eaed", boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmtBytes(c.raw)}` } },
    },
    scales: {
        x: { ticks: { color: "#9aa0a6", font: { size: 11 } }, grid: { color: "#272b3455" } },
        y: {
            ticks: { color: "#9aa0a6", font: { size: 11 }, callback: (v) => fmtBytes(v) },
            grid: { color: "#272b3455" }, beginAtZero: true,
        },
    },
};
const STACKED_OPTS = {
    ...COMMON_OPTS,
    scales: {
        x: { ...COMMON_OPTS.scales.x, stacked: true },
        y: { ...COMMON_OPTS.scales.y, stacked: true },
    },
};

function drawChart(config) {
    const ctx = $("history-chart").getContext("2d");
    if (_historyChart) { _historyChart.destroy(); _historyChart = null; }
    _historyChart = new Chart(ctx, config);
}

// Networks sorted by all-time usage, biggest first.
function networksByUsage(data) {
    const n = data.networks || {};
    return Object.keys(n).sort((a, b) => (n[b].rx + n[b].tx) - (n[a].rx + n[a].tx));
}

// Time view: one line per network (total in+out per bucket) plus an "All" line.
function renderPeriodLines(data, view) {
    const cfg = VIEWS[view];
    const nh = data.networks_history || {};
    const nets = networksByUsage(data);

    const keySet = new Set();
    nets.forEach(n => Object.keys((nh[n] || {})[cfg.source] || {}).forEach(k => keySet.add(k)));
    const keys = [...keySet].sort().slice(-cfg.max);
    const labels = keys.map(cfg.label);

    const lineFor = (dict, color, label, width) => ({
        label, borderColor: color, backgroundColor: color,
        data: keys.map(k => { const v = dict[k]; return v ? v.rx + v.tx : 0; }),
        borderWidth: width, pointRadius: 2, tension: 0.25, fill: false,
    });

    // "All networks" is the sum of the per-network buckets, so it always equals
    // the lines below it rather than a separately-tracked interface counter.
    const allNet = {};
    nets.forEach(n => {
        const src = (nh[n] || {})[cfg.source] || {};
        for (const k in src) {
            (allNet[k] ||= { rx: 0, tx: 0 });
            allNet[k].rx += src[k].rx;
            allNet[k].tx += src[k].tx;
        }
    });

    const datasets = [lineFor(allNet, "#e8eaed", "All networks", 3)];
    nets.forEach((n, i) =>
        datasets.push(lineFor((nh[n] || {})[cfg.source] || {}, NET_COLORS[i % NET_COLORS.length], n, 2)));

    drawChart({ type: "line", data: { labels, datasets }, options: COMMON_OPTS });
}

// "By network" view: all-time total per network as one stacked bar,
// split into download (bottom) and upload (top).
function renderNetworkBars(data) {
    const nets = networksByUsage(data).slice(0, 12);
    const n = data.networks || {};
    drawChart({
        type: "bar",
        data: {
            labels: nets,
            datasets: [
                { label: "In (↓)", stack: "t", data: nets.map(s => n[s].rx), backgroundColor: "#4f8cff" },
                { label: "Out (↑)", stack: "t", data: nets.map(s => n[s].tx), backgroundColor: "#34c759" },
            ],
        },
        options: STACKED_OPTS,
    });
}

function renderHistoryChart(data, view) {
    if (view === "network") renderNetworkBars(data);
    else renderPeriodLines(data, view);
}

async function loadHistory() {
    try {
        _historyData = await getJSON("/api/history");
        renderHistoryChart(_historyData, _activeView);
    } catch (e) { /* ignore — chart stays blank */ }
}

function switchTab(view) {
    _activeView = view;
    document.querySelectorAll(".tab[data-period]").forEach(t => t.classList.toggle("active", t.dataset.period === view));
    if (_historyData) renderHistoryChart(_historyData, view);
}

// --- init -------------------------------------------------------------------

$("scan-btn").addEventListener("click", doScan);
$("add-form").addEventListener("submit", doAdd);
document.querySelectorAll(".tab[data-period]").forEach(t => t.addEventListener("click", () => switchTab(t.dataset.period)));
refreshStatus();
loadHistory();
setInterval(loadHistory, 300000);
