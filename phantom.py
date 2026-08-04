"""Phantom device profile management for network identity spoofing."""
import json
import os
import re
import subprocess

_DIR = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_DIR, "phantoms.json")
_EXAMPLE = os.path.join(_DIR, "phantoms.json.example")

_FALLBACK = [
    {
        "id": "default",
        "name": "Default (Raspberry Pi)",
        "hostname": "rpi3wifi",
        "mac": "",
        "manufacturer": "Raspberry Pi Foundation",
    }
]


def load():
    """Load phantom device profiles, falling back to the example then a hardcoded default."""
    for path in (_FILE, _EXAMPLE):
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
    return _FALLBACK


def get(device_id):
    """Return a single phantom profile by id, or None."""
    for d in load():
        if d.get("id") == device_id:
            return d
    return None


def current_mac(iface):
    """Read current MAC address of an interface from ip link show."""
    try:
        out = subprocess.run(
            ["ip", "link", "show", iface],
            capture_output=True, text=True, timeout=5,
        ).stdout
        m = re.search(r"link/ether ([0-9a-fA-F:]{17})", out)
        return m.group(1).lower() if m else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def permanent_mac(iface):
    """Read factory/permanent MAC via ethtool -P (returns lowercase or None)."""
    try:
        out = subprocess.run(
            ["ethtool", "-P", iface],
            capture_output=True, text=True, timeout=5,
        ).stdout
        m = re.search(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", out)
        return m.group(1).lower() if m else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
