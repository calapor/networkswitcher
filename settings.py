"""Persistent auto-connect settings for the WiFi switcher.

Stores the user's auto-connect policy in settings.json next to the app:
    {"auto_connect": bool, "mode": "order"|"signal", "order": [ssid, ...]}

`order` is the canonical preference ranking (most-preferred first), kept by SSID
so it survives even in "signal" mode — where wpa_supplicant's per-network
priorities are all flattened to 0 and so can't carry the ranking themselves.
"""
import json
import os
import threading

import wifi

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
_lock = threading.Lock()

_DEFAULTS = {"auto_connect": True, "mode": "order", "order": []}


def _load_locked():
    data = dict(_DEFAULTS)
    try:
        with open(_FILE) as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            data.update({k: stored[k] for k in _DEFAULTS if k in stored})
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass
    # normalise
    data["auto_connect"] = bool(data["auto_connect"])
    data["mode"] = "signal" if data["mode"] == "signal" else "order"
    data["order"] = [str(s) for s in data.get("order", []) if isinstance(s, str)]
    return data


def _save_locked(data):
    try:
        with open(_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def get():
    """Return the current settings dict (with defaults filled in)."""
    with _lock:
        return _load_locked()


def set(**kw):
    """Merge the given keys into the stored settings and return the result."""
    with _lock:
        data = _load_locked()
        for k in ("auto_connect", "mode", "order"):
            if k in kw:
                data[k] = kw[k]
        _save_locked(data)
        return data


def ordered_networks():
    """Saved networks in preference order, reconciled with the live config.

    Known SSIDs come first in the stored order; any networks not yet ranked
    (e.g. freshly added) are appended. The reconciled order is persisted so it
    stays stable, and returned alongside the network dicts from `wifi`.
    """
    nets = wifi.list_networks()
    with _lock:
        data = _load_locked()
        by_ssid = {n["ssid"]: n for n in nets}
        ranked = [s for s in data["order"] if s in by_ssid]
        extras = [n["ssid"] for n in nets if n["ssid"] not in ranked]
        data["order"] = ranked + extras
        _save_locked(data)
        order = data["order"]
    return [by_ssid[s] for s in order if s in by_ssid]
