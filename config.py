"""Runtime configuration for the WiFi switcher.

All values can be overridden with environment variables (set in the systemd
unit). Defaults match the rpi3wifi bridge setup.
"""
import os

# WiFi interface that feeds the bridge (the one we switch).
IFACE = os.environ.get("WIFI_IFACE", "wlan0")

# Web server bind address. Default to the eth0 gateway IP so the panel is only
# offered on the house/LAN side and never on the upstream wifi (wlan0).
# For local development set BIND_HOST=127.0.0.1.
BIND_HOST = os.environ.get("BIND_HOST", "192.168.2.1")
PORT = int(os.environ.get("PORT", "8080"))

# wpa_supplicant config file (must have update_config=1 for save_config).
WPA_CONF = os.environ.get("WPA_CONF", "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf")

# Optional explicit DHCP command run after associating. Use {iface} as a
# placeholder, e.g. "dhclient -1 {iface}". If empty, net.run_dhcp auto-detects
# an available client (dhcpcd / dhclient / udhcpc).
DHCP_CMD = os.environ.get("DHCP_CMD", "")

# How long (seconds) to wait for association + DHCP when switching. Slow APs
# (e.g. phone hotspots) can take a while, so give association some headroom.
ASSOC_TIMEOUT = int(os.environ.get("ASSOC_TIMEOUT", "30"))
DHCP_TIMEOUT = int(os.environ.get("DHCP_TIMEOUT", "20"))

# Host:port used to test upstream internet reachability (TCP connect).
PROBE_HOST = os.environ.get("PROBE_HOST", "1.1.1.1")
PROBE_PORT = int(os.environ.get("PROBE_PORT", "53"))
