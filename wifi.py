"""Thin wrapper around `wpa_cli` for the running wpa_supplicant instance.

The Pi runs wpa_supplicant manually:
    wpa_supplicant -B -D nl80211 -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
with ctrl_interface=DIR=/var/run/wpa_supplicant and update_config=1, so wpa_cli
auto-discovers the control socket and save_config persists edits to disk.
"""
import subprocess

from config import IFACE


class WifiError(RuntimeError):
    pass


def _wpa(*args, timeout=15):
    try:
        proc = subprocess.run(
            ["wpa_cli", "-i", IFACE, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise WifiError("wpa_cli not found — is wpa_supplicant installed?")
    except subprocess.TimeoutExpired:
        raise WifiError(f"wpa_cli {args[0] if args else ''} timed out")
    out = proc.stdout.strip()
    if proc.returncode != 0 or out == "FAIL" or "Failed to connect" in (proc.stderr or ""):
        msg = (proc.stderr or out or "FAIL").strip()
        raise WifiError(f"wpa_cli {' '.join(args)} failed: {msg}")
    return out


def _ok(out):
    if out.strip() != "OK":
        raise WifiError(f"unexpected wpa_cli reply: {out!r}")


# --- status -----------------------------------------------------------------

def status():
    """Return parsed `wpa_cli status` as a dict (wpa_state, ssid, bssid, ...)."""
    out = _wpa("status")
    return dict(
        line.split("=", 1) for line in out.splitlines() if "=" in line
    )


# --- saved networks ---------------------------------------------------------

def list_networks():
    """Saved networks from the config: id, ssid, bssid, flags, current/disabled."""
    out = _wpa("list_networks")
    nets = []
    for line in out.splitlines()[1:]:  # skip header row
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        flags = parts[3] if len(parts) > 3 else ""
        nets.append({
            "id": int(parts[0]),
            "ssid": parts[1],
            "bssid": parts[2] if len(parts) > 2 else "",
            "current": "[CURRENT]" in flags,
            "disabled": "[DISABLED]" in flags,
        })
    return nets


# --- scanning ---------------------------------------------------------------

def _dbm_to_pct(dbm):
    if dbm <= -100:
        return 0
    if dbm >= -50:
        return 100
    return 2 * (dbm + 100)


def _security(flags):
    if "WPA3" in flags or "SAE" in flags:
        return "WPA3"
    if "WPA2" in flags or "RSN" in flags:
        return "WPA2"
    if "WPA" in flags:
        return "WPA"
    if "WEP" in flags:
        return "WEP"
    return "Open"


def scan_results():
    """Parse `wpa_cli scan_results`, deduped by SSID keeping strongest signal."""
    out = _wpa("scan_results")
    best = {}
    for line in out.splitlines()[1:]:  # skip header
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        bssid, _freq, signal, flags, ssid = parts[0], parts[1], parts[2], parts[3], parts[4]
        ssid = ssid.strip()
        if not ssid:
            continue  # hidden / no SSID broadcast
        try:
            dbm = int(signal)
        except ValueError:
            continue
        entry = {
            "ssid": ssid,
            "bssid": bssid,
            "signal": _dbm_to_pct(dbm),
            "dbm": dbm,
            "security": _security(flags),
        }
        if ssid not in best or dbm > best[ssid]["dbm"]:
            best[ssid] = entry
    return sorted(best.values(), key=lambda e: e["dbm"], reverse=True)


def trigger_scan():
    """Kick off a scan. Returns False if the radio is busy (ignored)."""
    try:
        _wpa("scan")
        return True
    except WifiError:
        # FAIL-BUSY is common when a scan is already in progress; not fatal.
        return False


# --- switching --------------------------------------------------------------

def select_network(nid):
    """Force-connect to a saved network now (disables the others for this run)."""
    _ok(_wpa("select_network", str(nid)))


def enable_all():
    """Re-enable every saved network so wpa_supplicant can auto-fall-back/return
    (e.g. back to brambles_d2 when it reappears) based on saved priorities."""
    for net in list_networks():
        _wpa("enable_network", str(net["id"]))


def _esc_check(value, field):
    if '"' in value or any(ord(c) < 32 for c in value):
        raise WifiError(f"invalid characters in {field}")


def add_network(ssid, psk=None, hidden=False):
    """Add and connect a new network, persisting it to the config.

    SSID is hex-encoded so any characters are safe. An empty/None psk creates an
    open network (key_mgmt NONE). Returns the new network id.
    """
    ssid = (ssid or "").strip()
    if not ssid:
        raise WifiError("SSID is required")
    psk = (psk or "").strip()

    nid = _wpa("add_network").splitlines()[-1].strip()
    if not nid.isdigit():
        raise WifiError(f"could not allocate network id: {nid!r}")
    try:
        # hex SSID (no quotes) avoids any escaping issues.
        _ok(_wpa("set_network", nid, "ssid", ssid.encode().hex()))
        if psk:
            if not (8 <= len(psk) <= 63):
                raise WifiError("WPA password must be 8–63 characters")
            _esc_check(psk, "password")
            _ok(_wpa("set_network", nid, "psk", f'"{psk}"'))
        else:
            _ok(_wpa("set_network", nid, "key_mgmt", "NONE"))
        if hidden:
            _ok(_wpa("set_network", nid, "scan_ssid", "1"))
        _ok(_wpa("enable_network", nid))
        _ok(_wpa("select_network", nid))
        _ok(_wpa("save_config"))
    except Exception:
        # roll back a half-created entry so we don't leave junk in the config
        try:
            _wpa("remove_network", nid)
        except WifiError:
            pass
        raise
    return int(nid)


def forget(nid):
    """Remove a saved network and persist the change."""
    _ok(_wpa("remove_network", str(nid)))
    _ok(_wpa("save_config"))
