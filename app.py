"""WiFi switcher web panel for the rpi3wifi bridge.

Serves a small control page (LAN-only) that lets you see the current upstream
WiFi, scan/add networks, and switch which network wlan0 connects to — without
SSH. Switching is performed in a background worker so the HTTP request returns
immediately and the page polls /api/status for progress.
"""
import threading
import time

from flask import Flask, jsonify, render_template, request

import config
import net
import persist_stats
import wifi

app = Flask(__name__)
persist_stats.init()

# --- background switch worker ----------------------------------------------

_lock = threading.Lock()
_action = {"busy": False, "step": "idle", "error": "", "target": "", "ts": 0}


def _set(**kw):
    _action.update(kw, ts=time.time())


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

        # re-enable the other saved networks so auto-fallback/return still works
        _set(step="finalizing")
        try:
            wifi.enable_all()
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
    all_rx, all_tx = persist_stats.update(rx, tx)
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
        "action": _action,
    })


@app.route("/api/networks/saved")
def api_saved():
    try:
        return jsonify(wifi.list_networks())
    except wifi.WifiError as e:
        return jsonify({"error": str(e)}), 500


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
    app.run(host=config.BIND_HOST, port=config.PORT, threaded=True)
