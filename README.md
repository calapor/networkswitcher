# WiFi Switcher

A tiny web panel for a headless Raspberry Pi that bridges an **upstream WiFi**
to **Ethernet**. It lets you see the current upstream connection and switch /
scan / add the WiFi that `wlan0` connects to — from a browser, no SSH required.

## The setup it's built for

```
  upstream WiFi (brambles_d2, ...)          house WiFi (brambles_d)
            (  (•))                                 (  (•))
              │                                        │
        wlan0 │   ┌──────────────┐   eth0        ┌───────────┐
              └──▶│   rpi3wifi    │──────────────▶│  Orbi     │──▶ house devices
                  │  (this bridge)│ 192.168.2.1   │  RBR50    │
                  └──────────────┘                └───────────┘
```

- `wlan0` associates to an upstream WiFi to get internet (managed by
  `wpa_supplicant`, with NAT `MASQUERADE -o wlan0`).
- `eth0` is a static gateway (`192.168.2.1`) plugged into the Orbi, which
  rebroadcasts to the house.

When the upstream network drops, `wlan0` is left unassociated and the whole
house loses internet. This panel fixes that without SSH.

**Why it stays reachable during an outage:** the web server binds to the
`eth0` side (`192.168.2.1`), which is independent of `wlan0`. So even when the
upstream WiFi is down, you can still open the panel from the house network and
switch to a working network.

## How it works

The panel drives the **already-running** `wpa_supplicant` via `wpa_cli`
(`scan`, `select_network`, `add_network`, `save_config`, …). It does **not**
restart wpa_supplicant or touch the NAT/bridge rules. After associating it runs
a DHCP client on `wlan0` to fetch an IP from the upstream router (this Pi has no
DHCP daemon watching `wlan0`, which is why a switch needs to trigger DHCP
explicitly), then verifies internet reachability.

Switching happens in a background worker; the page polls `/api/status` and shows
live progress and any failure.

## Install (on the Pi)

```bash
git clone https://github.com/calapor/networkswitcher networkswitcher
cd networkswitcher
sudo ./install.sh
```

The installer runs preflight checks (confirms `wpa_cli` reaches
`wpa_supplicant`, detects the DHCP client), copies to `/opt/networkswitcher`,
creates a virtualenv, and installs + starts the `networkswitcher` systemd
service.

Then open from any device on the house WiFi:

```
http://192.168.2.1:8080
```

## Deploying updates

**Option A — git pull on the Pi (recommended)**

```bash
# On your Mac: commit and push
git add -A && git commit -m "your message"
git push

# On the Pi: pull and reinstall
ssh pi@192.168.2.1
cd ~/networkswitcher
git pull
sudo ./install.sh
```

**Option B — scp changed files directly (no git needed on Pi)**

```bash
PI=pi@192.168.2.1
scp app.py persist_stats.py templates/index.html static/app.js install.sh \
    $PI:/opt/networkswitcher/
ssh $PI "sudo systemctl restart networkswitcher"
```

Check it came up after either option:

```bash
ssh pi@192.168.2.1 "sudo systemctl status networkswitcher"
```

## Configuration

Settings are environment variables (set in `networkswitcher.service`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `WIFI_IFACE` | `wlan0` | Upstream interface to switch |
| `BIND_HOST` | `192.168.2.1` | Bind address (eth0/LAN side only) |
| `PORT` | `8080` | Web port |
| `WPA_CONF` | `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf` | Config for `save_config` |
| `DHCP_CMD` | _(auto)_ | Explicit DHCP command, e.g. `dhclient -1 {iface}` |
| `ASSOC_TIMEOUT` / `DHCP_TIMEOUT` | `30` / `20` | Switch timeouts (s) |
| `PROBE_HOST` / `PROBE_PORT` | `1.1.1.1` / `53` | Internet reachability probe |

If the wrong DHCP client is auto-picked, set `DHCP_CMD` in the service file and
`sudo systemctl restart networkswitcher`.

## Recovery & fallback

- `brambles_d2` should keep the highest `priority` in
  `wpa_supplicant-wlan0.conf`; after any manual switch the app re-enables all
  saved networks so `wpa_supplicant` auto-returns to it when it reappears.
- If a switch fails (wrong password / out of range) the panel stays up on
  `eth0` — just pick another network.

## Switching to a phone hotspot

A phone hotspot is awkward because **the phone that becomes the hotspot can never
load this panel**: reaching `192.168.2.1` requires being on the house WiFi
(`brambles_d`), but turning on Personal Hotspot drops the phone off `brambles_d` and
moves it to the *upstream* side (`172.20.10.x`), behind the bridge's NAT, where the
panel isn't served. Three ways to handle it:

- **Auto-fallback (cleanest, no clicks).** Save the hotspot once. After any switch the
  app re-enables every saved network, so `wpa_supplicant` will **auto-roam to the
  hotspot on its own** whenever `brambles_d2` drops and the hotspot is up and in range.
  Just turn the hotspot on and wait ~15–30s — no panel interaction needed.
- **Delay / countdown.** In *Saved networks*, set a **Delay (s)**, press **Connect**,
  then enable the hotspot during the countdown; the bridge switches when it ends. You
  won't see the result on the phone — rejoin `brambles_d` (now fed by the hotspot)
  afterward to confirm.
- **Second device.** Drive the panel from a laptop or another phone on `brambles_d`. The
  bridge keeps `brambles_d` alive while it swaps its upstream, so that device stays
  connected the whole time — the simplest manual path.

## Security

- LAN-only by design: the server binds to the `eth0` IP, so it is never offered
  on the upstream WiFi. `install.sh` also adds an `iptables` DROP on `wlan0:PORT`
  as defense in depth.
- No authentication (per requirements). Anyone on the house network can use it.

## Local development

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
BIND_HOST=127.0.0.1 ./venv/bin/python app.py
```

`wpa_cli`/`ip`/DHCP calls will fail off-Pi (the UI shows the errors), but the
page, scan/add forms, and API shape are all exercisable.
