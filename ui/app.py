import os
import sys
import subprocess
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import (
    create_baseline,
    get_baseline_ble_set,
    get_baseline_wifi_set,
    get_ble_rows,
    get_latest_baseline,
    get_wifi_rows,
    init_db,
    log_ble,
    log_wifi,
)
from core.risk import band_label, classify_ble, classify_wifi, is_new
from core.vendors import vendor_lookup_mac
from scanners.ble import parse_ble_scan, run_ble_scan
from scanners.wifi import parse_iw_scan, run_wifi_scan

app = Flask(__name__)

VALID_FILTERS = {"all", "high", "new", "approaching"}
LAST_GPS = {
    "lat": None,
    "lon": None,
    "ts": None,
}


def _split_tags(tags):
    if not tags or tags == "-":
        return set()
    return {tag.strip() for tag in tags.split(",") if tag.strip()}


def _matches_filter(item, active_filter):
    if active_filter == "all":
        return True

    tags = _split_tags(item.get("tags"))
    status = (item.get("status") or "").lower()

    if active_filter == "high":
        return "high" in status or "suspicious" in tags
    if active_filter == "new":
        return "new-baseline" in tags
    if active_filter == "approaching":
        return bool(tags & {"approaching", "nearby", "very-close"})

    return True


def _gps_snapshot():
    if LAST_GPS["lat"] is None or LAST_GPS["lon"] is None or LAST_GPS["ts"] is None:
        return None
    return {
        "lat": LAST_GPS["lat"],
        "lon": LAST_GPS["lon"],
        "ts": LAST_GPS["ts"],
    }


@app.route("/")
def index():
    active_filter = request.args.get("filter", "all").lower()
    if active_filter not in VALID_FILTERS:
        active_filter = "all"

    baseline = get_latest_baseline()
    baseline_id = baseline[0] if baseline else None
    baseline_name = baseline[1] if baseline else "none"
    baseline_time = baseline[2] if baseline else "none"
    baseline_wifi_set = get_baseline_wifi_set(baseline_id) if baseline_id is not None else set()
    baseline_ble_set = get_baseline_ble_set(baseline_id) if baseline_id is not None else set()

    wifi_rows = get_wifi_rows()
    ble_rows = get_ble_rows()

    processed_wifi = []
    hidden_count = 0
    five_count = 0
    new_wifi_count = 0
    new_wifi_baseline_count = 0

    for row in wifi_rows:
        bssid, ssid, hidden, latest_signal_dbm, strongest_signal_dbm, freq_mhz, channel, security, seen_count, first_seen, last_seen = row
        vendor = vendor_lookup_mac(bssid)
        band = band_label(freq_mhz)
        status, score, tags = classify_wifi(hidden, latest_signal_dbm, vendor, first_seen, baseline_id, bssid, baseline_wifi_set)

        if hidden:
            hidden_count += 1
        if band == "5 GHz":
            five_count += 1
        if is_new(first_seen):
            new_wifi_count += 1
        if baseline_id is not None and bssid not in baseline_wifi_set:
            new_wifi_baseline_count += 1

        chan_or_freq = channel if channel else (freq_mhz if freq_mhz is not None else "?")

        processed_wifi.append({
            "bssid": bssid,
            "ssid": ssid,
            "hidden": "YES" if hidden else "NO",
            "latest": latest_signal_dbm,
            "strongest": strongest_signal_dbm,
            "band": band,
            "chan_or_freq": chan_or_freq,
            "security": security,
            "vendor": vendor,
            "seen_count": seen_count,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "status": status,
            "score": score,
            "tags": tags,
        })

    processed_wifi.sort(key=lambda x: (x["score"], x["latest"] if x["latest"] is not None else -999), reverse=True)
    processed_wifi = [item for item in processed_wifi if _matches_filter(item, active_filter)]

    processed_ble = []
    new_ble_count = 0
    new_ble_baseline_count = 0

    for row in ble_rows:
        address, name, latest_rssi, strongest_rssi, vendor, seen_count, first_seen, last_seen = row
        status, score, tags = classify_ble(name, vendor, latest_rssi, first_seen, baseline_id, address, baseline_ble_set)

        if is_new(first_seen):
            new_ble_count += 1
        if baseline_id is not None and address not in baseline_ble_set:
            new_ble_baseline_count += 1

        processed_ble.append({
            "address": address,
            "name": name,
            "latest": latest_rssi,
            "strongest": strongest_rssi,
            "vendor": vendor,
            "seen_count": seen_count,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "status": status,
            "score": score,
            "tags": tags,
        })

    processed_ble.sort(key=lambda x: (x["score"], x["latest"] if x["latest"] is not None else -999), reverse=True)
    processed_ble = [item for item in processed_ble if _matches_filter(item, active_filter)]

    baseline_info = {"name": baseline_name, "time": baseline_time}
    summary = {
        "wifi_devices": len(processed_wifi),
        "hidden_count": hidden_count,
        "five_count": five_count,
        "new_wifi_count": new_wifi_count,
        "new_wifi_baseline_count": new_wifi_baseline_count,
        "ble_devices": len(processed_ble),
        "new_ble_count": new_ble_count,
        "new_ble_baseline_count": new_ble_baseline_count,
    }

    return render_template(
        "index.html",
        processed_wifi=processed_wifi,
        processed_ble=processed_ble,
        summary=summary,
        baseline=baseline_info,
        active_filter=active_filter,
    )


@app.route("/gps_update", methods=["POST"])
def gps_update():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "invalid json"}), 400

    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"status": "error", "message": "lat/lon required"}), 400

    accuracy = payload.get("accuracy")
    if accuracy is not None:
        try:
            accuracy = float(accuracy)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "accuracy must be numeric"}), 400

    timestamp = payload.get("timestamp") or datetime.utcnow().isoformat()

    LAST_GPS["lat"] = lat
    LAST_GPS["lon"] = lon
    LAST_GPS["ts"] = timestamp
    if accuracy is not None:
        LAST_GPS["accuracy"] = accuracy
    else:
        LAST_GPS.pop("accuracy", None)

    return jsonify({"status": "ok"})


@app.route("/scan_wifi")
def scan_wifi():
    interface = os.environ.get("WRAITH_WIFI_IFACE", "wlan1")
    try:
        output = run_wifi_scan(interface)
    except subprocess.CalledProcessError as e:
        return f"<pre>Wi-Fi scan failed:\n{e.output}</pre>", 500
    except Exception as e:
        return f"<pre>Wi-Fi scan failed:\n{e}</pre>", 500

    parsed = parse_iw_scan(output)
    for item in parsed:
        wifi_item = dict(item)
        wifi_item["gps"] = _gps_snapshot()
        log_wifi(interface, wifi_item)

    return redirect(url_for("index", filter=request.args.get("filter", "all")))


@app.route("/scan_ble")
def scan_ble():
    try:
        output = run_ble_scan()
    except subprocess.CalledProcessError as e:
        output = e.output
    except Exception as e:
        return f"<pre>BLE scan failed:\n{e}</pre>", 500

    found = parse_ble_scan(output)
    for addr, info in found.items():
        log_ble(addr, info["name"], info["rssi"], gps=_gps_snapshot())

    return redirect(url_for("index", filter=request.args.get("filter", "all")))


@app.route("/capture_baseline")
def capture_baseline():
    create_baseline()
    return redirect(url_for("index", filter=request.args.get("filter", "all")))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
