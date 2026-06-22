"""Per-minute connection-quality sampler.

Runs a background daemon thread (like persist_stats) that measures ping latency
and a light download speed once every config.QUALITY_INTERVAL seconds, keeping
only the most recent sample in memory. The web panel reads latest() to show
"ping / speed ~1 minute ago" without doing any probing in the request path.
"""
import threading
import time

import config
import net

_lock = threading.Lock()
_latest = {"ping_ms": None, "speed_mbps": None, "ts": 0}


def latest():
    """Most recent sample plus its age in seconds (age None if never sampled)."""
    with _lock:
        snap = dict(_latest)
    snap["age_sec"] = (time.time() - snap["ts"]) if snap["ts"] else None
    return snap


def _sample_once():
    ping = net.ping_ms()
    # Skip the download when the link is clearly down — a guaranteed-failed
    # transfer wastes time and tells us nothing new.
    speed = net.speedtest_mbps() if ping is not None else None
    with _lock:
        _latest.update(ping_ms=ping, speed_mbps=speed, ts=time.time())


def _loop():
    while True:
        time.sleep(config.QUALITY_INTERVAL)
        try:
            _sample_once()
        except Exception:  # noqa: BLE001 - never let a probe error kill the thread
            pass


def init():
    """Start the sampler thread. Call once at startup."""
    threading.Thread(target=_loop, daemon=True, name="net-quality").start()
