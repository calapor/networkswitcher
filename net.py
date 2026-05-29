"""Network helpers: interface IP / carrier, internet probe, and DHCP renewal."""
import re
import shutil
import socket
import subprocess

import config


def iface_ip(iface):
    """Return the IPv4 address of an interface, or None."""
    try:
        out = subprocess.run(
            ["ip", "-4", "addr", "show", iface],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def iface_bytes(iface):
    """Return (rx_bytes, tx_bytes) for an interface since it came up, or (None, None)."""
    try:
        with open(f"/sys/class/net/{iface}/statistics/rx_bytes") as f:
            rx = int(f.read().strip())
        with open(f"/sys/class/net/{iface}/statistics/tx_bytes") as f:
            tx = int(f.read().strip())
        return rx, tx
    except (OSError, ValueError):
        return None, None


def iface_carrier(iface):
    """True if the interface has a carrier/link (operstate up)."""
    try:
        with open(f"/sys/class/net/{iface}/operstate") as f:
            return f.read().strip() == "up"
    except OSError:
        return False


def internet_ok():
    """Quick TCP-connect probe to the configured host:port."""
    try:
        with socket.create_connection((config.PROBE_HOST, config.PROBE_PORT), timeout=3):
            return True
    except OSError:
        return False


def _dhcp_command(iface):
    """Build the DHCP client command to run after associating.

    Honours config.DHCP_CMD if set, otherwise auto-detects an installed client.
    Returns a list of argv, or None if no client is available.
    """
    if config.DHCP_CMD:
        return config.DHCP_CMD.format(iface=iface).split()
    if shutil.which("dhcpcd"):
        # one-shot: (re)acquire a lease on this iface then exit
        return ["dhcpcd", "-n", iface]
    if shutil.which("dhclient"):
        return ["dhclient", "-1", iface]
    if shutil.which("udhcpc"):
        return ["udhcpc", "-i", iface, "-n", "-q"]
    return None


def run_dhcp(iface):
    """Acquire an IPv4 lease on the interface. Returns (ok, message)."""
    cmd = _dhcp_command(iface)
    if cmd is None:
        return False, "no DHCP client found (install dhcpcd/dhclient/udhcpc or set DHCP_CMD)"
    # Release any stale dhclient lease first so we re-DHCP cleanly.
    if cmd[0] == "dhclient" and shutil.which("dhclient"):
        subprocess.run(["dhclient", "-r", iface], capture_output=True, timeout=15)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=config.DHCP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"DHCP ({' '.join(cmd)}) timed out"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "DHCP failed").strip()
    return True, "ok"
