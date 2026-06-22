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

# --- per-minute connection-quality sampler ---------------------------------
# How often (seconds) to sample ping latency + download speed.
QUALITY_INTERVAL = int(os.environ.get("QUALITY_INTERVAL", "60"))
# Bytes to pull for the speed test each sample. Default ~150 KB/min (~6 MB/h);
# set to 0 to disable the speed test entirely (ping latency still runs). Keep
# small on metered hotspots — this traffic is downloaded on every sample.
SPEEDTEST_BYTES = int(os.environ.get("SPEEDTEST_BYTES", "150000"))
# Sized-download endpoint; {n} is replaced with SPEEDTEST_BYTES.
SPEEDTEST_URL = os.environ.get(
    "SPEEDTEST_URL", "https://speed.cloudflare.com/__down?bytes={n}"
)

# --- auto-failover ----------------------------------------------------------
# How often (seconds) the failover monitor checks internet reachability, and
# how many consecutive failures must pile up before it switches networks.
# 20s * 6 ≈ 2 minutes of downtime before we move to another saved network.
FAILOVER_CHECK_INTERVAL = int(os.environ.get("FAILOVER_CHECK_INTERVAL", "20"))
FAILOVER_FAILS = int(os.environ.get("FAILOVER_FAILS", "6"))
