"""Persistent all-time traffic stats that survive app restarts.

Tracks cumulative rx/tx bytes across interface resets and process restarts
by persisting totals to stats.json in the app directory every 60 seconds.
"""
import datetime
import json
import os
import threading
import time

import config

# 54.9 GiB and 23.2 GiB as the out-of-box baseline (1024-based, matching fmtBytes).
_INITIAL_RX = round(54.9 * 1024 ** 3)
_INITIAL_TX = round(23.2 * 1024 ** 3)

_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json")

_lock = threading.Lock()
_stored_rx = _INITIAL_RX  # total loaded from disk at startup
_stored_tx = _INITIAL_TX
_session_rx = 0            # bytes accumulated since this process started
_session_tx = 0
_last_kernel_rx = None     # last raw kernel counter value seen
_last_kernel_tx = None

# Period anchor state — guarded by _lock
_day_key = None;    _day_anchor_rx = 0;    _day_anchor_tx = 0
_week_key = None;   _week_anchor_rx = 0;   _week_anchor_tx = 0
_month_key = None;  _month_anchor_rx = 0;  _month_anchor_tx = 0
_year_key = None;   _year_anchor_rx = 0;   _year_anchor_tx = 0

# Historical completed-period totals — guarded by _lock
_history: dict = {"days": {}, "weeks": {}, "months": {}, "years": {}}

# Cumulative bytes attributed to each SSID — guarded by _lock.
# The kernel counter is interface-wide, so traffic is credited to whichever
# network is connected when each delta is seen. On the first run with this
# feature the connected network is seeded with all existing all-time usage.
_by_ssid: dict = {}
_by_ssid_seeded = False
_last_ssid = None  # most recent non-empty SSID, used to attribute null-SSID traffic

# Per-SSID period anchors and completed-period history — guarded by _lock.
# Mirrors the global day/week/month/year tracking but keyed per network, so
# each network gets its own week/month/year breakdown for the line chart.
_ssid_anchors: dict = {}     # {ssid: {"day_key": .., "day_rx": .., ...}}
_history_by_ssid: dict = {}  # {ssid: {"days": {}, "weeks": {}, "months": {}, "years": {}}}

# (history-key, anchor-field-prefix) for each period unit.
_PERIOD_UNITS = (("days", "day"), ("weeks", "week"), ("months", "month"), ("years", "year"))


def _empty_history():
    return {"days": {}, "weeks": {}, "months": {}, "years": {}}


def _current_period_keys():
    today = datetime.date.today()
    iso = today.isocalendar()
    return (
        today.isoformat(),                  # day,  e.g. "2026-05-31"
        f"{iso[0]}-W{iso[1]:02d}",          # week, e.g. "2026-W22"
        f"{today.year}-{today.month:02d}",  # month
        f"{today.year}",                    # year
    )


def _init_ssid_anchors(ssid, rx, tx):
    """Start an SSID's period anchors at (rx, tx) for the current periods.

    Pass the network's existing cumulative for the seed network (so its
    historical usage isn't dated into the current week), or 0 for a genuinely
    new network (so its just-seen traffic counts in the current period)."""
    a = {}
    for (hk, unit), key in zip(_PERIOD_UNITS, _current_period_keys()):
        a[unit + "_key"], a[unit + "_rx"], a[unit + "_tx"] = key, rx, tx
    _ssid_anchors[ssid] = a
    _history_by_ssid.setdefault(ssid, _empty_history())


def _refresh_period_anchors(anchors, history, total_rx, total_tx):
    """Advance an SSID's day/week/month/year anchors, recording completed
    buckets into `history`. Both are plain dicts (per-SSID state)."""
    keys = _current_period_keys()
    for (hk, unit), key in zip(_PERIOD_UNITS, keys):
        if anchors.get(unit + "_key") != key:
            if anchors.get(unit + "_key") is not None:
                history[hk][anchors[unit + "_key"]] = {
                    "rx": total_rx - anchors.get(unit + "_rx", 0),
                    "tx": total_tx - anchors.get(unit + "_tx", 0),
                }
            anchors[unit + "_key"] = key
            anchors[unit + "_rx"] = total_rx
            anchors[unit + "_tx"] = total_tx


def _period_with_current(anchors, history, total_rx, total_tx):
    """Return an SSID's history dict with the in-progress period appended."""
    keys = _current_period_keys()
    out = {}
    for (hk, unit), key in zip(_PERIOD_UNITS, keys):
        if anchors.get(unit + "_key") == key:
            cur = {"rx": total_rx - anchors.get(unit + "_rx", 0),
                   "tx": total_tx - anchors.get(unit + "_tx", 0), "current": True}
        else:
            # boundary passed but not yet rolled over by an update; nothing
            # has accumulated for this network in the new period yet.
            cur = {"rx": 0, "tx": 0, "current": True}
        out[hk] = {**history.get(hk, {}), key: cur}
    return out


def _load():
    global _stored_rx, _stored_tx, _by_ssid, _by_ssid_seeded
    global _ssid_anchors, _history_by_ssid
    global _day_key, _day_anchor_rx, _day_anchor_tx
    global _week_key, _week_anchor_rx, _week_anchor_tx
    global _month_key, _month_anchor_rx, _month_anchor_tx
    global _year_key, _year_anchor_rx, _year_anchor_tx
    try:
        with open(_STATS_FILE) as f:
            data = json.load(f)
        _stored_rx = int(data["rx_bytes"])
        _stored_tx = int(data["tx_bytes"])
        pa = data.get("period_anchors", {})
        _day_key        = pa.get("day_key")
        _day_anchor_rx  = int(pa.get("day_rx", 0))
        _day_anchor_tx  = int(pa.get("day_tx", 0))
        _week_key       = pa.get("week_key")
        _week_anchor_rx = int(pa.get("week_rx", 0))
        _week_anchor_tx = int(pa.get("week_tx", 0))
        _month_key       = pa.get("month_key")
        _month_anchor_rx = int(pa.get("month_rx", 0))
        _month_anchor_tx = int(pa.get("month_tx", 0))
        _year_key       = pa.get("year_key")
        _year_anchor_rx = int(pa.get("year_rx", 0))
        _year_anchor_tx = int(pa.get("year_tx", 0))
        hist = data.get("history", {})
        _history["days"]   = hist.get("days", {})
        _history["weeks"]  = hist.get("weeks", {})
        _history["months"] = hist.get("months", {})
        _history["years"]  = hist.get("years", {})
        if "by_ssid" in data:
            _by_ssid = dict(data["by_ssid"])
            _by_ssid_seeded = True
        _ssid_anchors = dict(data.get("ssid_anchors", {}))
        _history_by_ssid = {
            s: {**_empty_history(), **h} for s, h in data.get("history_by_ssid", {}).items()
        }
    except (FileNotFoundError, KeyError, json.JSONDecodeError, ValueError):
        pass  # first run: defaults stay


def _save_locked():
    """Write current totals to disk. Must be called with _lock held."""
    try:
        with open(_STATS_FILE, "w") as f:
            json.dump(
                {
                    "rx_bytes": _stored_rx + _session_rx,
                    "tx_bytes": _stored_tx + _session_tx,
                    "period_anchors": {
                        "day_key": _day_key,
                        "day_rx": _day_anchor_rx,
                        "day_tx": _day_anchor_tx,
                        "week_key": _week_key,
                        "week_rx": _week_anchor_rx,
                        "week_tx": _week_anchor_tx,
                        "month_key": _month_key,
                        "month_rx": _month_anchor_rx,
                        "month_tx": _month_anchor_tx,
                        "year_key": _year_key,
                        "year_rx": _year_anchor_rx,
                        "year_tx": _year_anchor_tx,
                    },
                    "history": {
                        "days":   _history["days"],
                        "weeks":  _history["weeks"],
                        "months": _history["months"],
                        "years":  _history["years"],
                    },
                    "by_ssid": _by_ssid,
                    "ssid_anchors": _ssid_anchors,
                    "history_by_ssid": _history_by_ssid,
                },
                f,
            )
    except OSError:
        pass


def _refresh_anchors_locked(total_rx, total_tx):
    """Set or advance period anchors. Must be called with _lock held."""
    global _day_key, _day_anchor_rx, _day_anchor_tx
    global _week_key, _week_anchor_rx, _week_anchor_tx
    global _month_key, _month_anchor_rx, _month_anchor_tx
    global _year_key, _year_anchor_rx, _year_anchor_tx
    dk, wk, mk, yk = _current_period_keys()
    if _day_key != dk:
        if _day_key is not None:
            _history["days"][_day_key] = {"rx": total_rx - _day_anchor_rx, "tx": total_tx - _day_anchor_tx}
        _day_key, _day_anchor_rx, _day_anchor_tx = dk, total_rx, total_tx
    if _week_key != wk:
        if _week_key is not None:
            _history["weeks"][_week_key] = {"rx": total_rx - _week_anchor_rx, "tx": total_tx - _week_anchor_tx}
        _week_key, _week_anchor_rx, _week_anchor_tx = wk, total_rx, total_tx
    if _month_key != mk:
        if _month_key is not None:
            _history["months"][_month_key] = {"rx": total_rx - _month_anchor_rx, "tx": total_tx - _month_anchor_tx}
        _month_key, _month_anchor_rx, _month_anchor_tx = mk, total_rx, total_tx
    if _year_key != yk:
        if _year_key is not None:
            _history["years"][_year_key] = {"rx": total_rx - _year_anchor_rx, "tx": total_tx - _year_anchor_tx}
        _year_key, _year_anchor_rx, _year_anchor_tx = yk, total_rx, total_tx


def _saver_loop():
    while True:
        time.sleep(60)
        with _lock:
            _save_locked()


def init():
    """Load stored totals and snapshot the current kernel counters.

    Call once at startup before the first API request.
    """
    global _last_kernel_rx, _last_kernel_tx
    _load()
    import net
    rx, tx = net.iface_bytes(config.IFACE)
    _last_kernel_rx = rx if rx is not None else 0
    _last_kernel_tx = tx if tx is not None else 0
    with _lock:
        _refresh_anchors_locked(_stored_rx + _session_rx, _stored_tx + _session_tx)
    threading.Thread(target=_saver_loop, daemon=True, name="stats-saver").start()


def update(kernel_rx, kernel_tx, ssid=None):
    """Feed the latest raw kernel counter values; return (all_time_rx, all_time_tx).

    Handles counter resets (interface bounce) by detecting when the kernel
    value decreases and treating the new value as counting from zero. When
    ``ssid`` is given, this cycle's traffic is credited to that network.
    """
    global _session_rx, _session_tx, _last_kernel_rx, _last_kernel_tx, _stored_rx, _stored_tx
    global _by_ssid_seeded, _last_ssid
    with _lock:
        delta_rx = delta_tx = 0
        if kernel_rx is not None and _last_kernel_rx is not None:
            if kernel_rx >= _last_kernel_rx:
                delta_rx = kernel_rx - _last_kernel_rx
                _session_rx += delta_rx
            else:
                # counter reset — fold session into stored and start fresh
                _stored_rx += _session_rx
                _session_rx = kernel_rx
                delta_rx = kernel_rx
            _last_kernel_rx = kernel_rx

        if kernel_tx is not None and _last_kernel_tx is not None:
            if kernel_tx >= _last_kernel_tx:
                delta_tx = kernel_tx - _last_kernel_tx
                _session_tx += delta_tx
            else:
                _stored_tx += _session_tx
                _session_tx = kernel_tx
                delta_tx = kernel_tx
            _last_kernel_tx = kernel_tx

        total_rx = _stored_rx + _session_rx
        total_tx = _stored_tx + _session_tx
        _refresh_anchors_locked(total_rx, total_tx)

        # Credit this cycle's traffic to the connected network. When the SSID
        # is briefly unknown (disconnect blips, probe gaps, reconnects) fall
        # back to the last network seen so interface bytes are never dropped on
        # the floor — otherwise they'd inflate "all networks" with no owner.
        effective_ssid = ssid or _last_ssid
        if effective_ssid:
            if not _by_ssid_seeded:
                # First run with this feature: attribute all existing usage to
                # whatever network is connected right now, but don't date that
                # historical lump into the current week/month/year.
                _by_ssid[effective_ssid] = {"rx": total_rx, "tx": total_tx}
                _by_ssid_seeded = True
                _init_ssid_anchors(effective_ssid, total_rx, total_tx)
            elif effective_ssid not in _by_ssid:
                # New network: count its traffic from zero so this cycle lands
                # in the current period.
                _by_ssid[effective_ssid] = {"rx": delta_rx, "tx": delta_tx}
                _init_ssid_anchors(effective_ssid, 0, 0)
            else:
                b = _by_ssid[effective_ssid]
                b["rx"] += delta_rx
                b["tx"] += delta_tx
        if ssid:
            _last_ssid = ssid

        # Roll period anchors for every known network each cycle so idle
        # networks still close out their week/month/year on the calendar
        # boundary (their unchanged total records a correct, possibly-zero
        # bucket) rather than only when next connected.
        for s, b in _by_ssid.items():
            anchors = _ssid_anchors.setdefault(s, {})
            hist = _history_by_ssid.setdefault(s, _empty_history())
            _refresh_period_anchors(anchors, hist, b["rx"], b["tx"])

        return total_rx, total_tx


def period_totals():
    """Return (week_rx, week_tx, month_rx, month_tx, year_rx, year_tx)."""
    with _lock:
        total_rx = _stored_rx + _session_rx
        total_tx = _stored_tx + _session_tx
        return (
            total_rx - _week_anchor_rx,  total_tx - _week_anchor_tx,
            total_rx - _month_anchor_rx, total_tx - _month_anchor_tx,
            total_rx - _year_anchor_rx,  total_tx - _year_anchor_tx,
        )


def get_history():
    """Return completed and in-progress period totals for charting."""
    with _lock:
        total_rx = _stored_rx + _session_rx
        total_tx = _stored_tx + _session_tx
        dk, wk, mk, yk = _current_period_keys()
        return {
            "days":   {**_history["days"],
                       dk: {"rx": total_rx - _day_anchor_rx,   "tx": total_tx - _day_anchor_tx,   "current": True}},
            "weeks":  {**_history["weeks"],
                       wk: {"rx": total_rx - _week_anchor_rx,  "tx": total_tx - _week_anchor_tx,  "current": True}},
            "months": {**_history["months"],
                       mk: {"rx": total_rx - _month_anchor_rx, "tx": total_tx - _month_anchor_tx, "current": True}},
            "years":  {**_history["years"],
                       yk: {"rx": total_rx - _year_anchor_rx,  "tx": total_tx - _year_anchor_tx,  "current": True}},
            "networks": {s: dict(v) for s, v in _by_ssid.items()},
            "networks_history": {
                s: _period_with_current(
                    _ssid_anchors.get(s, {}),
                    _history_by_ssid.get(s, _empty_history()),
                    b["rx"], b["tx"],
                )
                for s, b in _by_ssid.items()
            },
        }
