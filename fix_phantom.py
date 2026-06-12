"""One-off script: scrub phantom traffic that the old counter-reset bug booked.

A transient low read of the kernel byte counters during a wlan0 flap used to be
treated as a counter reset, and the counter climbing back to its true value was
then booked as one giant delta — crediting tens of GiB of "traffic" to whatever
network was connected (e.g. "Evanna's iPhone showing 216 GB in today"). The code
fix in persist_stats.py (_advance) stops this happening again; this script
repairs the already-corrupted stats.json.

For every network whose *today* delta (by_ssid total minus its day-anchor)
exceeds a believable ceiling, it clamps the network back to its day-anchor (so
today reads ~0) and subtracts the removed phantom from the global all-time
totals. Earlier completed-day history is left untouched.

Run on the Pi while the service is stopped (it rewrites stats.json every 30s):
    sudo systemctl stop networkswitcher
    python3 /opt/networkswitcher/fix_phantom.py
    sudo systemctl start networkswitcher
"""
import json
import os

STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json")
STATS_TMP  = STATS_FILE + ".tmp"
STATS_BAK  = STATS_FILE + ".bak"

GiB = 1024 ** 3
# Largest believable single-day download/upload for one network on this link.
# Anything above this in one day is the phantom-traffic bug, not real usage.
MAX_DAY_BYTES = 80 * GiB


def main():
    with open(STATS_FILE) as f:
        data = json.load(f)

    by_ssid = data.get("by_ssid", {})
    anchors = data.get("ssid_anchors", {})

    removed_rx = removed_tx = 0
    for ssid, b in by_ssid.items():
        a = anchors.get(ssid, {})
        day_rx = a.get("day_rx", 0)
        day_tx = a.get("day_tx", 0)
        today_rx = b["rx"] - day_rx
        today_tx = b["tx"] - day_tx
        if today_rx > MAX_DAY_BYTES or today_tx > MAX_DAY_BYTES:
            print(f"Scrubbing {ssid!r}: today was "
                  f"rx={today_rx / GiB:.2f}GiB tx={today_tx / GiB:.2f}GiB -> 0")
            removed_rx += max(0, today_rx)
            removed_tx += max(0, today_tx)
            b["rx"] = day_rx
            b["tx"] = day_tx

    if not removed_rx and not removed_tx:
        print("No phantom traffic found; nothing to do.")
        return

    data["rx_bytes"] = data.get("rx_bytes", 0) - removed_rx
    data["tx_bytes"] = data.get("tx_bytes", 0) - removed_tx
    print(f"Removed {removed_rx / GiB:.2f}GiB rx / {removed_tx / GiB:.2f}GiB tx "
          f"from all-time totals.")
    print(f"New all-time: rx={data['rx_bytes'] / GiB:.2f}GiB "
          f"tx={data['tx_bytes'] / GiB:.2f}GiB")

    with open(STATS_TMP, "w") as f:
        json.dump(data, f)
    if os.path.exists(STATS_FILE):
        os.replace(STATS_FILE, STATS_BAK)
    os.replace(STATS_TMP, STATS_FILE)
    print("Wrote corrected stats.json.")


if __name__ == "__main__":
    main()
