# Architecture

This document describes how the WiFi Switcher bridge is put together: the
network topology it lives in, the software components, the threads, and the
non-obvious design decisions (especially around keeping `wpa_supplicant` — and
therefore the panel — alive). For the *what* and *why*, see the
[README](../README.md).

---

## 1. Network topology

The Pi is a **routed NAT bridge** between an upstream WiFi and a wired mesh
router. It is *not* a layer-2 bridge — `eth0` and `wlan0` are separate IP
subnets and traffic is NAT'd between them.

```
 Internet
    │
 ((•)) upstream WiFi  e.g. brambles_d2 / a hotspot (172.20.10.x)
    │  associated by wpa_supplicant
 wlan0  (DHCP client lease from the upstream router; default route from lease)
    │
 ┌──┴─────────────────────────────────────────────┐
 │  rpi3wifi (Raspberry Pi 3)                       │
 │                                                  │
 │   net.ipv4.ip_forward = 1                        │
 │   iptables -t nat -A POSTROUTING -o wlan0 \      │
 │            -j MASQUERADE                          │
 │   (forward eth0 ⇄ wlan0)                          │
 │                                                  │
 │   Flask panel binds 192.168.2.1:8080  ◀── LAN-only
 └──┬─────────────────────────────────────────────┘
 eth0  192.168.2.1/24 (static)
    │
 ┌──┴──────────┐
 │ Orbi RBR50  │  WAN port = eth0; the mesh router does DHCP/NAT
 │ (mesh WAN)  │  for the house exactly as if wired to a modem.
 └──┬──────────┘
    │
 ((•)) house WiFi  brambles_d  ──▶ phones, laptops, TVs …
```

Key consequences of this layout:

- The mesh router treats `eth0` (`192.168.2.1`) as its internet uplink. Nothing
  in the house knows or cares that the real uplink is WiFi.
- The panel binds the **`eth0`** IP, so it is reachable from the house side even
  while `wlan0` is down — that's the whole point: you can fix a broken upstream
  from a browser.
- Because the upstream router does DHCP on the `wlan0` side, **this Pi has no
  DHCP daemon watching `wlan0`** — so after every switch the app must explicitly
  run a DHCP client to pull a lease (and the default route).

> The static `eth0`, `ip_forward`, and `MASQUERADE` plumbing is assumed to be
> already configured on the Pi (e.g. via `/etc/network/interfaces`,
> `dhcpcd.conf`, and a persisted iptables ruleset). This project controls
> **which WiFi `wlan0` joins** and reports/heals the link — it does not own the
> NAT/bridge configuration.

`wlan1` on this particular Pi is a separate interface managed by `ifupdown`
(`wpa-conf` in `/etc/network/interfaces.d/wlan1`) and is unrelated to the
bridge — leave it alone.

---

## 2. Software components

### Application (Python, copied to `/opt/networkswitcher`)

| Module | Responsibility |
|--------|----------------|
| `app.py` | Flask app, JSON API, and the three background threads (switch worker, failover worker, failover monitor). |
| `wifi.py` | Thin wrapper over `wpa_cli` for the **running** supplicant: `status`, `scan`/`scan_results`, `list_networks`, `add_network`, `select_network`, `forget`, and `apply_policy` (priorities + enable/disable + `save_config`). Decodes printf-escaped SSIDs. |
| `net.py` | Interface IP/carrier/byte counters (`/sys/class/net`), internet probe (TCP connect), ping latency, sized speed test, and DHCP client auto-detect + lease renewal. |
| `netquality.py` | Per-minute daemon thread sampling ping + speed; keeps only the latest sample in memory for the panel. |
| `persist_stats.py` | Durable byte accounting: all-time, per-period (day/week/month/year) and per-SSID, with counter-reset/misread guards, atomic writes + `.bak`, and dated weekly snapshots. Runs its own 30 s sampler thread so usage is recorded 24/7. |
| `diag.py` | Assembles the no-secrets diagnostic bundle shared by `/api/debug` and `netdebug`. |
| `config.py` | All tunables, read from environment variables (set in the systemd unit). |
| `settings.py` | Persists the auto-connect policy (`auto_connect`, `mode`, `order`) to `settings.json`; reconciles the saved ranking with the live network list. |
| `templates/` + `static/` | The single-page dashboard (status, usage charts via Chart.js, auto-connect, saved/nearby networks, add-network form). |

### One-off maintenance scripts

| Script | Purpose |
|--------|---------|
| `fix_phantom.py` | Scrub phantom traffic the old counter-reset bug booked into `stats.json`. |
| `reset_anchors.py` | Reset period anchors so week/month/year totals equal all-time. |

### System glue (installed to `/usr/local/bin` + `/etc/systemd/system`)

| Unit / script | Role |
|---------------|------|
| `wifi-connect.sh` → `/usr/local/bin/wifi-connect` | Brings up `wlan0`, sets reg domain GB, and **daemonizes a single `wpa_supplicant -B`**, then backgrounds a ~60 s DHCP-on-association loop and exits immediately. |
| `wifi-connect.service` | `Type=forking` unit that runs the launcher at boot (the sole supplicant owner). |
| `wifi-watchdog.sh` → `/usr/local/bin/wifi-watchdog` | If `wpa_cli ping` ≠ `PONG`, restart `wifi-connect.service`. |
| `wifi-watchdog.service` + `.timer` | Run the watchdog every minute (self-healing). |
| `networkswitcher.service` | Runs the Flask panel as root, bound to `192.168.2.1:8080`, with config via `Environment=`. |
| `netdebug.sh` → `/usr/local/bin/netdebug` | CLI diagnostics: prefer the panel's `/api/debug`, fall back to running `diag.py`. |

---

## 3. Threads & runtime model

`app.py` runs Flask (`threaded=True`) plus several daemon threads:

1. **Switch worker** (`_switch_worker`, spawned per manual action) — optional
   countdown → `connect_fn()` → wait for association → run DHCP → confirm an IP →
   re-apply the auto-connect policy. A single `_action` dict + lock guards
   against concurrent switches; the UI polls `/api/status` for live progress.
2. **Failover worker** (`_failover_attempt`) — triggered by the monitor: scan,
   then try each visible saved network strongest-first, requiring it to
   associate, get an IP **and be pingable** before accepting it.
3. **Failover monitor** (`_failover_monitor`) — every `FAILOVER_CHECK_INTERVAL`
   seconds, probe the internet; after `FAILOVER_FAILS` consecutive failures
   (~2 min) and only when auto-connect is on, kick off a failover.
4. **Quality sampler** (`netquality`) — ping + speed once a minute.
5. **Stats sampler** (`persist_stats`) — kernel counters + current SSID every
   30 s, persisted so usage is tracked even with the dashboard closed.

All five share the Pi's single `wpa_supplicant`; the `_action` lock serialises
anything that drives a switch so they never collide.

---

## 4. The wpa_supplicant interaction model

`wpa_supplicant` runs **once**, launched manually by `wifi-connect`:

```
wpa_supplicant -B -D nl80211 -i wlan0 \
  -c /etc/wpa_supplicant/wpa_supplicant-wlan0.conf \
  -P /var/run/wpa_supplicant.pid
```

The conf must have `ctrl_interface=DIR=/var/run/wpa_supplicant` (creates the
socket `wpa_cli`/the panel auto-discover) and `update_config=1` (lets
`save_config` persist edits — priorities, enabled flags, added networks — back
to disk so they survive reboots).

The panel **never restarts** `wpa_supplicant` and never touches the NAT/bridge
rules. Every WiFi operation is a `wpa_cli` verb against the already-running
daemon. After associating, the app runs a DHCP client itself (because nothing
else does on `wlan0`) and the lease installs the default route.

The auto-connect policy maps to supplicant state in `apply_policy`:

- **List order mode** → each saved network gets a distinct descending
  `priority` matching its rank; unranked (just-added) networks sort to the
  bottom.
- **Strongest signal mode** → all priorities flattened to 0; supplicant just
  takes the strongest AP in range.
- **Auto-connect on** → every saved network `enable`d (roaming/fallback
  possible). **Off** → only the `[CURRENT]` network stays enabled, the rest are
  `disable`d, so the bridge never switches itself.

The canonical ranking lives in `settings.json` keyed by SSID (so it survives
even in signal mode, where supplicant priorities can't carry it).

---

## 5. Boot-race & supplicant survival

The single most important reliability property: **the control socket must never
disappear**, because if it does, the panel can't switch networks and you're
locked out of the only recovery path.

Two historical failure modes shaped the current design:

- **Boot race (fixed 2026-06-08).** The stock Debian units
  `wpa_supplicant.service` and `wpa_supplicant@wlan0.service` were enabled
  alongside `wifi-connect.service`; on unlucky boots they killed each other and
  left no supplicant. **Fix:** mask both stock units so `wifi-connect.service`
  is the sole launcher.

  ```bash
  sudo systemctl disable --now wpa_supplicant.service wpa_supplicant@wlan0.service
  sudo systemctl mask wpa_supplicant.service wpa_supplicant@wlan0.service
  ```

- **Foreground-loop timeout (fixed 2026-06-09).** The old launcher blocked in a
  foreground association poll; with `Type=forking` + a start timeout, when no
  saved AP was in range it never exited, systemd hit the timeout and SIGTERM'd
  the whole cgroup — **killing the `-B` supplicant**. So "no network nearby"
  escalated to "supplicant destroyed". **Fixes:** (1) the launcher backgrounds
  its DHCP loop and `exit 0`s immediately, so the timeout can't fire; (2) the
  **wifi-watchdog** timer restarts the launcher if the socket goes away; (3) the
  **diagnostic bundle** (`/api/debug` + `netdebug`) is reachable on `eth0` even
  when wlan0 is down.

Health check if WiFi flakiness recurs: `pgrep -a wpa_supplicant` should return
**exactly one** process (the `wifi-connect -B` instance), and the stock
`wpa_supplicant*` units should still be masked.

---

## 6. Data-usage accounting

`persist_stats.py` turns the interface-wide kernel byte counters
(`/sys/class/net/wlan0/statistics/{rx,tx}_bytes`) into durable, per-network,
per-period totals:

- **Reset/misread guards (`_advance`)** — a counter going backwards (device
  reset or a transient low read during a wlan0 flap) folds the session into the
  stored total and re-baselines, crediting 0; a forward jump larger than
  `_MAX_SAMPLE_DELTA` (4 GiB) is treated as a misread and dropped. This is what
  stops the "216 GB in one day" phantom spikes.
- **Period anchors** — day/week/month/year totals are computed as
  `total − anchor`; crossing a calendar boundary closes the completed bucket
  into `history` and re-anchors. Anchors roll for *every* known network each
  cycle so idle networks still close out their periods.
- **Per-SSID attribution** — the kernel counter is interface-wide, so each
  delta is credited to the currently-connected SSID (falling back to the last
  seen SSID across blips). Each cycle the connected network also absorbs any
  unattributed remainder so per-network totals always reconcile to the
  interface-wide all-time total (the lump is added to anchors so it doesn't date
  into the current period).
- **Durability** — atomic temp-file write + rename, with the previous file kept
  as `.bak` and a dated weekly snapshot (`stats.YYYY-Www.json`, 8 retained).
  `stats.json` and `settings.json` are gitignored (per-deployment state).

---

## 7. HTTP API

All JSON unless noted. The panel polls `/api/status` continuously.

| Method & path | Purpose |
|---------------|---------|
| `GET /` | The dashboard page. |
| `GET /api/status` | Live link state, IP, internet, eth0 link, rx/tx + all-time/period totals, latest quality sample, and the current `action`. |
| `GET /api/history` | Completed + in-progress period totals (global and per-network) for charting. |
| `GET /api/debug` | Plain-text diagnostics bundle (no secrets). |
| `GET /api/networks/saved` | Saved networks (id, ssid, current/disabled). |
| `GET /api/networks/scan` | Trigger a scan and return deduped results. |
| `GET /api/config` | Auto-connect settings + saved networks in preference order. |
| `POST /api/config` | Set `auto_connect` and/or `mode` (`order`\|`signal`). |
| `POST /api/networks/order` | Set the preference ranking from a list of network ids. |
| `POST /api/connect` | Switch to a saved network id (optional `delay`). |
| `POST /api/networks` | Add + connect a new network (`ssid`, `psk`, `hidden`). |
| `POST /api/forget` | Remove a saved network id. |
| `POST /api/action/dismiss` | Clear a finished action banner (no-op while busy). |
</content>
