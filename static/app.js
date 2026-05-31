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
  $("all-rx").textContent = s.error ? "—" : fmtBytes(s.all_time_rx_bytes);
  $("all-tx").textContent = s.error ? "—" : fmtBytes(s.all_time_tx_bytes);

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

// --- init -------------------------------------------------------------------

$("scan-btn").addEventListener("click", doScan);
$("add-form").addEventListener("submit", doAdd);
refreshStatus();
