"""One-shot network diagnostics bundle for the rpi3wifi bridge.

Collects the read-only state needed to diagnose an upstream-WiFi problem into a
single plain-text report — the same set of checks you'd otherwise run by hand
over SSH. Shared by the panel's `GET /api/debug` route and the `netdebug` CLI so
both emit an identical report.

Deliberately contains NO secrets: it uses `wpa_cli list_networks` (which never
prints PSKs) and only greps the `ctrl_interface` line out of the wpa_supplicant
config — it never dumps the config (which holds `psk=` lines).
"""
import subprocess
import time

import config
import net

# (heading, shell command). Commands are fixed strings with only the trusted
# IFACE / WPA_CONF config values interpolated. wpa_cli calls get a short timeout
# so a dead/unreachable supplicant can't hang the report.
_IFACE = config.IFACE
_WPA = f"timeout 8 wpa_cli -i {_IFACE}"

_SECTIONS = [
    ("clock / uptime", "date; uptime"),
    ("wpa_supplicant processes", "pgrep -a wpa_supplicant || echo '(none running)'"),
    ("control sockets", f"ls -la /var/run/wpa_supplicant/ 2>&1"),
    ("wifi-connect.service", "systemctl is-active wifi-connect.service; "
        "systemctl status wifi-connect.service --no-pager -l 2>&1 | head -25"),
    ("wifi-watchdog.timer", "systemctl is-active wifi-watchdog.timer 2>&1; "
        "systemctl list-timers wifi-watchdog.timer --no-pager 2>&1 | head -3"),
    ("networkswitcher.service", "systemctl is-active networkswitcher.service 2>&1"),
    ("stock units (should be 'masked')",
        "systemctl is-enabled wpa_supplicant.service wpa_supplicant@wlan0.service 2>&1"),
    ("wifi-connect log (last 60)",
        "journalctl -u wifi-connect.service -n 60 --no-pager 2>&1"),
    ("watchdog log (last 20)",
        "journalctl -t wifi-watchdog -n 20 --no-pager 2>&1 || echo '(no watchdog log)'"),
    ("conf ctrl_interface (no secrets)",
        f"grep -n ctrl_interface {config.WPA_CONF} 2>&1 || echo '(conf not found)'"),
    ("wpa_cli status", f"{_WPA} status 2>&1"),
    ("wpa_cli saved networks (no PSKs)", f"{_WPA} list_networks 2>&1"),
    ("wpa_cli scan results", f"{_WPA} scan_results 2>&1"),
    ("links", "ip -br link 2>&1"),
    ("addresses", "ip -br addr 2>&1"),
    ("routes", "ip route 2>&1"),
    ("regulatory domain", "iw reg get 2>&1 | head -5"),
    ("rfkill", "rfkill list 2>&1"),
    ("wifi kernel messages", "dmesg 2>/dev/null | grep -iE 'wlan|brcmf|cfg80211' | tail -n 20 || "
        "echo '(dmesg unreadable without root)'"),
]


def _run(cmd):
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=20,
        )
        out = (proc.stdout + proc.stderr).strip()
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "(timed out)"
    except Exception as e:  # noqa: BLE001 - diagnostics must never crash the caller
        return f"(error: {e})"


def report():
    """Return the full diagnostic bundle as plain text."""
    lines = [
        "=== rpi3wifi network diagnostics ===",
        f"generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"iface: {_IFACE}    panel: {config.BIND_HOST}:{config.PORT}",
        "",
    ]
    for heading, cmd in _SECTIONS:
        lines.append(f"--- {heading} ---")
        lines.append(_run(cmd))
        lines.append("")
    # Internet probe via the app's own helper (matches what the panel reports).
    lines.append(f"--- internet probe ({config.PROBE_HOST}:{config.PROBE_PORT}) ---")
    lines.append("reachable" if net.internet_ok() else "no internet")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
