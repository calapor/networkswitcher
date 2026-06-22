"""WiFi switcher web panel for the rpi3wifi bridge.

Serves a small control page (LAN-only) that lets you see the current upstream
WiFi, scan/add networks, and switch which network wlan0 connects to — without
SSH. Switching is performed in a background worker so the HTTP request returns
immediately and the page polls /api/status for progress.
"""
import threading
import time

from flask import Flask, Response, jsonify, render_template, request

import config
import diag
import net
import netquality
import persist_stats
import settings
import wifi

app = Flask(__name__)
persist_stats.init()
netquality.init()

# --- background switch worker ----------------------------------------------

_lock = threading.Lock()
_action = {"busy": False, "step": "idle", "error": "", "target": "", "ts": 0}


def _set(**kw):
    _action.update(kw, ts=time.time())


def _apply_policy():
    """Push the current auto-connect settings down into wpa_supplicant."""
    s = settings.get()
    order = [n["ssid"] for n in settings.ordered_networks()]
    wifi.apply_policy(order, s["mode"], s["auto_connect"])


def _associate_and_dhcp(connect_fn):
    """Run connect_fn, then wait for association, drive DHCP and confirm an IP.

    Shared by the manual switch worker and auto-failover. Raises wifi.WifiError
    if any step fails; the caller is responsible for the surrounding UI state.
    """
    connect_fn()

    # wait for association
    deadline = time.time() + config.ASSOC_TIMEOUT
    while time.time() < deadline:
        if wifi.status().get("wpa_state") == "COMPLETED":
            break
        time.sleep(1)
    else:
        raise wifi.WifiError(
            "did not associate — check the password and that the network is in range"
        )

    # acquire an IP (nothing else runs DHCP on this Pi)
    _set(step="getting IP address")
    ok, msg = net.run_dhcp(config.IFACE)
    if not ok:
        raise wifi.WifiError(f"associated but DHCP failed: {msg}")

    # confirm we actually got an address
    deadline = time.time() + 10
    while time.time() < deadline and not net.iface_ip(config.IFACE):
        time.sleep(1)
    if not net.iface_ip(config.IFACE):
        raise wifi.WifiError("associated but no IP address was assigned")


def _switch_worker(connect_fn, target_label, delay=0):
    """Run a connect/add action, then drive DHCP and verify connectivity.

    If delay > 0, count down first so the user can (e.g.) enable a phone hotspot
    after pressing Connect — see the "phone hotspot" notes in the README.
    """
    try:
        _set(busy=True, step="starting", error="", target=target_label)

        # optional countdown before we touch the radio
        for remaining in range(delay, 0, -1):
            _set(step=f"starting in {remaining}s — enable your hotspot now")
            time.sleep(1)

        _set(step="associating")
        _associate_and_dhcp(connect_fn)

        # re-apply the auto-connect policy: priorities + which networks stay
        # enabled (so auto-fallback/return works, or stays manual-only, per the
        # user's config panel settings).
        _set(step="finalizing")
        try:
            _apply_policy()
        except wifi.WifiError:
            pass  # non-fatal

        _set(busy=False, step="connected")
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        _set(busy=False, step="failed", error=str(e))


def _start(connect_fn, target_label, delay=0):
    with _lock:
        if _action["busy"]:
            return False
        _set(busy=True, step="starting", error="", target=target_label)
    threading.Thread(
        target=_switch_worker, args=(connect_fn, target_label, delay), daemon=True
    ).start()
    return True


# --- auto-failover ----------------------------------------------------------

def _connect_and_verify(nid, ssid):
    """Switch to saved network nid, get an IP, and ping-test it.

    Returns True only if the network associates, gets an address, AND the
    internet becomes reachable (the "must be pingable" requirement). Any failure
    returns False so the caller can move on to the next candidate.
    """
    try:
        _set(step=f"trying {ssid}")
        _associate_and_dhcp(lambda: wifi.select_network(nid))
    except wifi.WifiError:
        return False
    # ping gate: give it a few seconds to actually carry traffic
    deadline = time.time() + 8
    while time.time() < deadline:
        if net.internet_ok():
            return True
        time.sleep(1)
    return False


def _failover_candidates():
    """Saved networks currently visible, strongest signal first, excluding the
    network we're connected to now (it's the one that just failed)."""
    results = wifi.scan_results()  # already deduped per SSID, sorted by dBm desc
    dbm = {e["ssid"]: e["dbm"] for e in results}
    saved = wifi.list_networks()
    cands = [n for n in saved if n["ssid"] in dbm and not n["current"]]
    cands.sort(key=lambda n: dbm[n["ssid"]], reverse=True)
    return cands


def _start_failover():
    """Spawn the failover worker unless a switch/failover is already running."""
    with _lock:
        if _action["busy"]:
            return False
        _set(busy=True, step="connection lost — scanning for a working network",
             error="", target="auto-failover")
    threading.Thread(target=_failover_attempt, daemon=True).start()
    return True


def _failover_attempt():
    """Find and connect to the strongest saved network that is pingable."""
    try:
        wifi.trigger_scan()
        time.sleep(2.5)  # let the radio populate results (matches /api/networks/scan)

        for n in _failover_candidates():
            if _connect_and_verify(n["id"], n["ssid"]):
                try:
                    _apply_policy()
                except wifi.WifiError:
                    pass  # non-fatal
                _set(busy=False, step=f"connected to {n['ssid']}", target=n["ssid"])
                return

        # nothing worked — restore policy so wpa_supplicant keeps trying on its own
        try:
            _apply_policy()
        except wifi.WifiError:
            pass
        _set(busy=False, step="failover failed",
             error="no saved network is reachable right now")
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        _set(busy=False, step="failover failed", error=str(e))


def _failover_monitor():
    """Watch internet reachability; after a sustained outage, switch networks.

    Only acts when auto-connect is on, and never while another switch/failover is
    already running (the _start lock also guards against that). A few minutes of
    failed checks must accumulate first so brief blips don't cause churn.
    """
    fails = 0
    while True:
        time.sleep(config.FAILOVER_CHECK_INTERVAL)
        if _action["busy"]:
            continue
        try:
            if not settings.get()["auto_connect"]:
                fails = 0
                continue
        except Exception:  # noqa: BLE001 - settings unreadable; skip this tick
            continue
        if net.internet_ok():
            fails = 0
            continue
        fails += 1
        if fails < config.FAILOVER_FAILS:
            continue
        fails = 0
        _start_failover()


# --- routes -----------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", iface=config.IFACE)


@app.route("/api/status")
def api_status():
    try:
        st = wifi.status()
    except wifi.WifiError as e:
        return jsonify({"error": str(e), "action": _action}), 200
    rx, tx = net.iface_bytes(config.IFACE)
    all_rx, all_tx = persist_stats.update(rx, tx, st.get("ssid"))
    week_rx, week_tx, month_rx, month_tx, year_rx, year_tx = persist_stats.period_totals()
    return jsonify({
        "ssid": st.get("ssid", ""),
        "bssid": st.get("bssid", ""),
        "wpa_state": st.get("wpa_state", ""),
        "ip": net.iface_ip(config.IFACE),
        "carrier": net.iface_carrier(config.IFACE),
        "internet": net.internet_ok(),
        "eth0_up": net.iface_carrier("eth0"),
        "rx_bytes": rx,
        "tx_bytes": tx,
        "all_time_rx_bytes": all_rx,
        "all_time_tx_bytes": all_tx,
        "week_rx_bytes": week_rx,
        "week_tx_bytes": week_tx,
        "month_rx_bytes": month_rx,
        "month_tx_bytes": month_tx,
        "year_rx_bytes": year_rx,
        "year_tx_bytes": year_tx,
        "quality": netquality.latest(),
        "action": _action,
    })


@app.route("/api/history")
def api_history():
    return jsonify(persist_stats.get_history())


@app.route("/api/debug")
def api_debug():
    """Plain-text diagnostics bundle. Served on eth0 (like the whole panel), so
    it stays reachable even when wlan0 / the supplicant is down — the UI links
    here on error so the report can be pasted for help. Contains no PSKs."""
    return Response(diag.report(), mimetype="text/plain; charset=utf-8")


@app.route("/api/networks/saved")
def api_saved():
    try:
        return jsonify(wifi.list_networks())
    except wifi.WifiError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config")
def api_config():
    """Auto-connect settings plus the saved networks in preference order."""
    s = settings.get()
    try:
        nets = settings.ordered_networks()
    except wifi.WifiError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"auto_connect": s["auto_connect"], "mode": s["mode"], "networks": nets})


@app.route("/api/config", methods=["POST"])
def api_config_set():
    data = request.get_json(silent=True) or {}
    kw = {}
    if "auto_connect" in data:
        kw["auto_connect"] = bool(data["auto_connect"])
    if "mode" in data:
        if data["mode"] not in ("order", "signal"):
            return jsonify({"error": "mode must be 'order' or 'signal'"}), 400
        kw["mode"] = data["mode"]
    settings.set(**kw)
    try:
        _apply_policy()
    except wifi.WifiError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/networks/order", methods=["POST"])
def api_order():
    """Set the saved-network preference ranking from a list of network ids."""
    data = request.get_json(silent=True) or {}
    ids = data.get("order")
    if not isinstance(ids, list):
        return jsonify({"error": "order must be a list of network ids"}), 400
    try:
        by_id = {n["id"]: n["ssid"] for n in wifi.list_networks()}
        order_ssids = [by_id[int(i)] for i in ids if int(i) in by_id]
        settings.set(order=order_ssids)
        _apply_policy()
    except (TypeError, ValueError):
        return jsonify({"error": "order must contain network ids"}), 400
    except wifi.WifiError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/networks/scan")
def api_scan():
    try:
        wifi.trigger_scan()
        # give the radio a moment to populate results
        time.sleep(2.5)
        return jsonify(wifi.scan_results())
    except wifi.WifiError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connect", methods=["POST"])
def api_connect():
    data = request.get_json(silent=True) or {}
    try:
        nid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"error": "missing network id"}), 400
    try:
        delay = max(0, min(120, int(data.get("delay", 0))))
    except (TypeError, ValueError):
        delay = 0
    # resolve a friendly label for the UI
    label = next((n["ssid"] for n in wifi.list_networks() if n["id"] == nid), str(nid))
    if not _start(lambda: wifi.select_network(nid), label, delay):
        return jsonify({"error": "another switch is already in progress"}), 409
    return jsonify({"ok": True})


@app.route("/api/networks", methods=["POST"])
def api_add():
    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    psk = data.get("psk") or ""
    hidden = bool(data.get("hidden"))
    if not ssid:
        return jsonify({"error": "SSID is required"}), 400
    if not _start(lambda: wifi.add_network(ssid, psk, hidden), ssid):
        return jsonify({"error": "another switch is already in progress"}), 409
    return jsonify({"ok": True})


@app.route("/api/forget", methods=["POST"])
def api_forget():
    data = request.get_json(silent=True) or {}
    try:
        nid = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"error": "missing network id"}), 400
    try:
        wifi.forget(nid)
        # drop it from the ranking and re-assert priorities on what remains
        _apply_policy()
    except wifi.WifiError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/action/dismiss", methods=["POST"])
def api_action_dismiss():
    """Clear a finished action banner (e.g. a stale failure). No-op while busy."""
    with _lock:
        if not _action["busy"]:
            _set(step="idle", error="", target="")
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Make wpa_supplicant reflect the saved auto-connect policy on boot.
    try:
        _apply_policy()
    except wifi.WifiError:
        pass  # supplicant may not be reachable yet; the watchdog will recover it
    threading.Thread(target=_failover_monitor, daemon=True, name="failover").start()
    app.run(host=config.BIND_HOST, port=config.PORT, threaded=True)
