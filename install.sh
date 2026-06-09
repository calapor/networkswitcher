#!/usr/bin/env bash
# Installer for the WiFi Switcher web panel. Run on the Pi as root:
#     sudo ./install.sh
set -euo pipefail

APP_DIR=/opt/networkswitcher
SERVICE=networkswitcher
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IFACE="${WIFI_IFACE:-wlan0}"
PORT="${PORT:-8080}"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo ./install.sh" >&2
  exit 1
fi

echo "==> Preflight checks"
if ! command -v wpa_cli >/dev/null; then
  echo "   ! wpa_cli not found. Install with: apt-get install -y wpasupplicant" >&2
  exit 1
fi

echo "==> Installing wlan0 supplicant launcher + self-healing watchdog"
for script in wifi-connect.sh wifi-watchdog.sh netdebug.sh; do
  install -m 0755 "$SRC_DIR/$script" "/usr/local/bin/${script%.sh}"
done
for unit in wifi-connect.service wifi-watchdog.service wifi-watchdog.timer; do
  cp "$SRC_DIR/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now wifi-connect.service
systemctl enable --now wifi-watchdog.timer

if wpa_cli -i "$IFACE" status >/dev/null 2>&1; then
  echo "   ok  wpa_cli can talk to wpa_supplicant on $IFACE"
else
  echo "   ! wpa_cli cannot reach wpa_supplicant on $IFACE." >&2
  echo "     Is wpa_supplicant running with ctrl_interface configured?" >&2
  exit 1
fi

DHCP_FOUND=""
for c in dhcpcd dhclient udhcpc; do
  if command -v "$c" >/dev/null; then DHCP_FOUND="$c"; break; fi
done
if [[ -n "$DHCP_FOUND" ]]; then
  echo "   ok  DHCP client detected: $DHCP_FOUND"
else
  echo "   ! No DHCP client found (dhcpcd/dhclient/udhcpc)." >&2
  echo "     Switching will associate but won't get an IP. Install one, e.g.:" >&2
  echo "       apt-get install -y isc-dhcp-client" >&2
fi

echo "==> Installing files to $APP_DIR"
mkdir -p "$APP_DIR"
for item in app.py wifi.py net.py config.py diag.py persist_stats.py requirements.txt templates static; do
  cp -r "$SRC_DIR/$item" "$APP_DIR/"
done

echo "==> Creating virtualenv + installing dependencies"
if [[ ! -d "$APP_DIR/venv" ]]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Installing systemd service"
cp "$SRC_DIR/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo "==> Firewall (defense in depth): drop inbound to :$PORT on $IFACE"
# The app already binds to the eth0 IP only; this is belt-and-suspenders.
if ! iptables -C INPUT -i "$IFACE" -p tcp --dport "$PORT" -j DROP 2>/dev/null; then
  iptables -I INPUT -i "$IFACE" -p tcp --dport "$PORT" -j DROP || true
fi
if command -v netfilter-persistent >/dev/null; then
  netfilter-persistent save || true
else
  echo "   (note) netfilter-persistent not installed; this rule won't survive reboot."
  echo "          That's fine — the eth0-only bind is the real protection."
fi

echo
echo "Done. Service status:"
systemctl --no-pager --full status "$SERVICE" | head -n 6 || true
echo
echo "Open the panel from a device on the house WiFi:"
echo "    http://${BIND_HOST:-192.168.2.1}:${PORT}"
