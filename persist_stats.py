"""Persistent all-time traffic stats that survive app restarts.

Tracks cumulative rx/tx bytes across interface resets and process restarts
by persisting totals to stats.json in the app directory every 60 seconds.
"""
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


def _load():
    global _stored_rx, _stored_tx
    try:
        with open(_STATS_FILE) as f:
            data = json.load(f)
        _stored_rx = int(data["rx_bytes"])
        _stored_tx = int(data["tx_bytes"])
    except (FileNotFoundError, KeyError, json.JSONDecodeError, ValueError):
        pass  # first run: defaults stay


def _save_locked():
    """Write current totals to disk. Must be called with _lock held."""
    try:
        with open(_STATS_FILE, "w") as f:
            json.dump(
                {"rx_bytes": _stored_rx + _session_rx, "tx_bytes": _stored_tx + _session_tx},
                f,
            )
    except OSError:
        pass


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
    threading.Thread(target=_saver_loop, daemon=True, name="stats-saver").start()


def update(kernel_rx, kernel_tx):
    """Feed the latest raw kernel counter values; return (all_time_rx, all_time_tx).

    Handles counter resets (interface bounce) by detecting when the kernel
    value decreases and treating the new value as counting from zero.
    """
    global _session_rx, _session_tx, _last_kernel_rx, _last_kernel_tx, _stored_rx, _stored_tx
    with _lock:
        if kernel_rx is not None and _last_kernel_rx is not None:
            if kernel_rx >= _last_kernel_rx:
                _session_rx += kernel_rx - _last_kernel_rx
            else:
                # counter reset — fold session into stored and start fresh
                _stored_rx += _session_rx
                _session_rx = kernel_rx
            _last_kernel_rx = kernel_rx

        if kernel_tx is not None and _last_kernel_tx is not None:
            if kernel_tx >= _last_kernel_tx:
                _session_tx += kernel_tx - _last_kernel_tx
            else:
                _stored_tx += _session_tx
                _session_tx = kernel_tx
            _last_kernel_tx = kernel_tx

        return _stored_rx + _session_rx, _stored_tx + _session_tx
