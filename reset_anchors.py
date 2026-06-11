"""One-off script: reset period anchors to 0 so week/month/year totals == all-time.

Run on the Pi while the service is stopped:
    sudo systemctl stop networkswitcher
    python3 /opt/networkswitcher/reset_anchors.py
    sudo systemctl start networkswitcher
"""
import datetime
import json
import os

STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json")
STATS_TMP  = STATS_FILE + ".tmp"
STATS_BAK  = STATS_FILE + ".bak"


def current_period_keys():
    today = datetime.date.today()
    iso = today.isocalendar()
    return (
        f"{iso[0]}-W{iso[1]:02d}",
        f"{today.year}-{today.month:02d}",
        f"{today.year}",
    )


try:
    with open(STATS_FILE) as f:
        data = json.load(f)
    print(f"Loaded {STATS_FILE}: rx={data.get('rx_bytes')}, tx={data.get('tx_bytes')}")
except FileNotFoundError:
    data = {"rx_bytes": 0, "tx_bytes": 0}
    print("No stats.json found — creating fresh.")

wk, mk, yk = current_period_keys()
data["period_anchors"] = {
    "week_key": wk,  "week_rx": 0,  "week_tx": 0,
    "month_key": mk, "month_rx": 0, "month_tx": 0,
    "year_key": yk,  "year_rx": 0,  "year_tx": 0,
}

with open(STATS_TMP, "w") as f:
    json.dump(data, f, indent=2)
if os.path.exists(STATS_FILE):
    os.replace(STATS_FILE, STATS_BAK)
os.replace(STATS_TMP, STATS_FILE)

print(f"Anchors reset to 0. Keys: week={wk}, month={mk}, year={yk}")
