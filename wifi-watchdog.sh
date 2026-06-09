#!/bin/bash
# Self-heal: if wpa_supplicant's control socket is unreachable on wlan0, restart
# the launcher. Run periodically by wifi-watchdog.timer. With the hardened
# wifi-connect.sh a missing AP can't kill the supplicant, but this also recovers
# from an unexpected crash so the panel is never left stranded.
if ! wpa_cli -i wlan0 ping 2>/dev/null | grep -q PONG; then
  logger -t wifi-watchdog "wpa_supplicant unreachable on wlan0 — restarting wifi-connect.service"
  systemctl restart wifi-connect.service
fi
