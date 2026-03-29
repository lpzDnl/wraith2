import os
import sys
import shutil
import subprocess
import threading
import time
from glob import glob
from datetime import datetime, timezone

from flask import Flask, jsonify, redirect, render_template, request, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import (
    create_baseline,
    db as open_db,
    get_baseline_ble_set,
    get_baseline_wifi_set,
    get_ble_rows,
    get_latest_baseline,
    get_recent_ble_observations,
    get_recent_wifi_observations,
    get_wifi_rows,
    init_db,
    log_ble,
    log_wifi,
)
from core.gps_state import derive_gps_state, seconds_since
from core.risk import band_label, classify_ble, classify_wifi, is_new
from core.vendors import vendor_lookup_mac
from scanners.ble import parse_ble_scan, run_ble_scan
from scanners.wifi import parse_iw_scan, run_wifi_scan

app = Flask(__name__)

VALID_FILTERS = {"all", "high", "new", "approaching"}
RECENT_OBSERVATION_LIMIT = 12
NORMAL_WIFI_SCAN_INTERVAL_SEC = 15
NORMAL_BLE_SCAN_INTERVAL_SEC = 10
TURBO_WIFI_SCAN_INTERVAL_SEC = 5
TURBO_BLE_SCAN_INTERVAL_SEC = 3
LAST_GPS = {
    "lat": None,
    "lon": None,
    "ts": None,
}
GPS_BY_ID_GLOB = "/dev/serial/by-id/*"
GPS_TTY_GLOB = "/dev/ttyACM*"
GPS_DISCOVERY_TIMEOUT_SECONDS = 2.0
GPS_DISCOVERY_POLL_SECONDS = 0.2
TRANSITION_MODES = {
    "PREPARE_MOBILE_PENDING",
    "SAFE_TO_UNPLUG_PHONE",
    "HEADLESS_WAITING_FOR_GPS",
    "RETURN_PREP_PENDING",
    "WEB_REATTACH_WAIT",
}
RUNTIME_LOCK = threading.Lock()
RUNTIME_STATE = {
    "mode": "WEB_ATTACHED_IDLE",
    "requested_mode": None,
    "phone_ui_expected": True,
    "gps_expected": False,
    "gps_connected": False,
    "gps_device": "",
    "gps_last_seen_ts": None,
    "gps_last_fix_ts": None,
    "gps_error": None,
    "transition_started_ts": None,
    "last_transition_ts": datetime.utcnow().isoformat(),
    "last_error": None,
    "operator_message": "Phone UI connected. Wraith is running normally.",
    "workflow_id": 0,
    "scanning_enabled": True,
    "turbo_enabled": False,
    "wifi_scan_interval_sec": NORMAL_WIFI_SCAN_INTERVAL_SEC,
    "ble_scan_interval_sec": NORMAL_BLE_SCAN_INTERVAL_SEC,
    "last_wifi_scan_ts": None,
    "last_ble_scan_ts": None,
    "wifi_scan_running": False,
    "ble_scan_running": False,
    "scan_loop_error": None,
}
_CONTROLLER_THREAD = None
_GPS_READER_THREAD = None
_SCAN_LOOP_THREAD = None
APP_STARTED_MONOTONIC = time.monotonic()


def _utcnow():
    return datetime.utcnow().isoformat()


def _seconds_since(timestamp):
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds(), 0.0)


def _format_gps_date(date_value: str):
    if not date_value or len(date_value) < 6:
        return None
    try:
        day = int(date_value[0:2])
        month = int(date_value[2:4])
        year = int(date_value[4:6])
    except ValueError:
        return None
    year += 2000 if year < 80 else 1900
    return f"{year:04d}-{month:02d}-{day:02d}"


def _format_gps_time(time_value: str):
    if not time_value or len(time_value) < 6:
        return None
    raw = time_value.split(".", 1)[0]
    raw = raw.ljust(6, "0")
    try:
        hour = int(raw[0:2])
        minute = int(raw[2:4])
        second = int(raw[4:6])
    except ValueError:
        return None
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _parse_nmea_coordinate(value: str, direction: str):
    if not value or not direction:
        return None
    try:
        numeric = float(value)
    except ValueError:
        return None

    degrees = int(numeric // 100)
    minutes = numeric - (degrees * 100)
    decimal = degrees + (minutes / 60.0)

    if direction in {"S", "W"}:
        decimal *= -1
    return round(decimal, 7)


def _build_fix_timestamp(gps_date: str, gps_time: str):
    if gps_date and gps_time:
        return f"{gps_date}T{gps_time}"
    return None


def _update_last_gps_locked(lat, lon, timestamp, gps_date=None, gps_time=None, accuracy=None):
    LAST_GPS["lat"] = lat
    LAST_GPS["lon"] = lon
    LAST_GPS["ts"] = timestamp
    LAST_GPS["date"] = gps_date
    LAST_GPS["time"] = gps_time
    if accuracy is not None:
        LAST_GPS["accuracy"] = accuracy
    elif "accuracy" in LAST_GPS:
        LAST_GPS.pop("accuracy", None)
    RUNTIME_STATE["gps_last_fix_ts"] = timestamp


def _set_mode_locked(mode, message):
    RUNTIME_STATE["mode"] = mode
    RUNTIME_STATE["operator_message"] = message
    RUNTIME_STATE["last_transition_ts"] = _utcnow()


def _update_scan_intervals_locked():
    if RUNTIME_STATE["turbo_enabled"]:
        RUNTIME_STATE["wifi_scan_interval_sec"] = TURBO_WIFI_SCAN_INTERVAL_SEC
        RUNTIME_STATE["ble_scan_interval_sec"] = TURBO_BLE_SCAN_INTERVAL_SEC
    else:
        RUNTIME_STATE["wifi_scan_interval_sec"] = NORMAL_WIFI_SCAN_INTERVAL_SEC
        RUNTIME_STATE["ble_scan_interval_sec"] = NORMAL_BLE_SCAN_INTERVAL_SEC


def _start_workflow_locked(requested_mode, mode, message, phone_ui_expected, gps_expected):
    RUNTIME_STATE["requested_mode"] = requested_mode
    RUNTIME_STATE["phone_ui_expected"] = phone_ui_expected
    RUNTIME_STATE["gps_expected"] = gps_expected
    RUNTIME_STATE["transition_started_ts"] = _utcnow()
    RUNTIME_STATE["workflow_id"] += 1
    _set_mode_locked(mode, message)


def _active_transition_locked():
    return RUNTIME_STATE["mode"] in TRANSITION_MODES


def _allowed_actions_locked():
    mode = RUNTIME_STATE["mode"]
    actions = []
    if mode in {"WEB_ATTACHED_IDLE", "WEB_ATTACHED_RECOVERED"}:
        actions.append("prepare_mobile")
    if mode in {"HEADLESS_WAITING_FOR_GPS", "MOBILE_RUNNING_WITH_GPS", "MOBILE_RUNNING_NO_GPS"}:
        actions.append("start_return")
    return actions


def _runtime_snapshot():
    with RUNTIME_LOCK:
        snapshot = dict(RUNTIME_STATE)
        snapshot["allowed_actions"] = _allowed_actions_locked()
        snapshot["active_transition"] = _active_transition_locked()
        snapshot["gps_state"] = derive_gps_state(
            snapshot.get("gps_last_fix_ts"),
            gps_connected=snapshot.get("gps_connected"),
            gps_device=snapshot.get("gps_device"),
            gps_error=snapshot.get("gps_error"),
        )
        return snapshot


def _render_prepare_response():
    if request.is_json:
        return jsonify(_runtime_snapshot())
    return redirect(url_for("index", filter=request.args.get("filter", "all")))


def _health_snapshot():
    reasons = []

    try:
        conn = open_db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            db_ok = True
        finally:
            conn.close()
    except Exception as e:
        db_ok = False
        reasons.append(f"db query failed: {e}")

    with RUNTIME_LOCK:
        last_wifi_scan_ts = RUNTIME_STATE.get("last_wifi_scan_ts")
        last_ble_scan_ts = RUNTIME_STATE.get("last_ble_scan_ts")
        gps_last_fix_ts = RUNTIME_STATE.get("gps_last_fix_ts")
        gps_connected = RUNTIME_STATE.get("gps_connected")
        gps_device = RUNTIME_STATE.get("gps_device")
        gps_error = RUNTIME_STATE.get("gps_error")
        wifi_scan_running = bool(RUNTIME_STATE.get("wifi_scan_running"))
        ble_scan_running = bool(RUNTIME_STATE.get("ble_scan_running"))

    wifi_scan_age = _seconds_since(last_wifi_scan_ts)
    ble_scan_age = _seconds_since(last_ble_scan_ts)
    gps_fix_age = _seconds_since(gps_last_fix_ts)
    gps_state = derive_gps_state(
        gps_last_fix_ts,
        gps_connected=gps_connected,
        gps_device=gps_device,
        gps_error=gps_error,
    )

    scanning_recent = any(
        age is not None and age <= 60 for age in (wifi_scan_age, ble_scan_age)
    )
    gps_recent = gps_fix_age is not None and gps_fix_age <= 120

    if not scanning_recent:
        reasons.append("no wifi or ble scan recorded in the last 60 seconds")
    if not gps_recent:
        reasons.append("no gps fix recorded in the last 120 seconds")

    ok = db_ok and scanning_recent
    return {
        "ok": ok,
        "db_ok": db_ok,
        "scanning_recent": scanning_recent,
        "gps_recent": gps_recent,
        "wifi_scan_running": wifi_scan_running,
        "ble_scan_running": ble_scan_running,
        "last_wifi_scan_ts": last_wifi_scan_ts,
        "last_ble_scan_ts": last_ble_scan_ts,
        "gps_last_fix_ts": gps_last_fix_ts,
        "gps_state": gps_state,
        "reasons": reasons,
    }


def _shutdown_system():
    time.sleep(1)
    try:
        subprocess.Popen(["sudo", "shutdown", "-h", "now"])
    except Exception as e:
        app.logger.exception("Failed to invoke shutdown: %s", e)


def _is_ublox_by_id_path(path: str):
    name = os.path.basename(path).lower()
    return "u-blox" in name or "ublox" in name


def _looks_like_gps_nmea_line(line: str):
    sentence = line.strip()
    if not sentence.startswith("$"):
        return False
    body = sentence[1:].split("*", 1)[0]
    kind = body.split(",", 1)[0]
    return kind in {"GPRMC", "GNRMC", "GPGGA", "GNGGA", "GPGSA", "GNGSA", "GPGSV", "GNGSV", "GPVTG", "GNVTG"}


def _probe_gps_stream(device_path: str, timeout_seconds=GPS_DISCOVERY_TIMEOUT_SECONDS):
    fd = None
    buffer = ""
    deadline = time.monotonic() + timeout_seconds
    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
        while time.monotonic() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                time.sleep(GPS_DISCOVERY_POLL_SECONDS)
                continue
            except OSError as e:
                return False, str(e)

            if not chunk:
                time.sleep(GPS_DISCOVERY_POLL_SECONDS)
                continue

            buffer += chunk.decode("ascii", errors="ignore")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if _looks_like_gps_nmea_line(line):
                    return True, None

        return False, "Timed out waiting for GPS/NMEA data"
    except OSError as e:
        return False, str(e)
    finally:
        if fd is not None:
            os.close(fd)


def _discover_gps_device():
    by_id_matches = []
    for path in sorted(glob(GPS_BY_ID_GLOB)):
        if not os.path.islink(path):
            continue
        if not _is_ublox_by_id_path(path):
            continue
        resolved = os.path.realpath(path)
        if not os.path.exists(resolved):
            continue
        by_id_matches.append(path)

    if len(by_id_matches) == 1:
        return by_id_matches[0], None

    fallback_errors = []
    if len(by_id_matches) > 1:
        fallback_errors.append(f"Multiple u-blox GPS devices found in /dev/serial/by-id: {', '.join(by_id_matches)}")

    for path in sorted(glob(GPS_TTY_GLOB)):
        if not os.path.exists(path):
            continue
        ok, error = _probe_gps_stream(path)
        if ok:
            return path, None
        if error:
            fallback_errors.append(f"{path}: {error}")

    if fallback_errors:
        return None, "; ".join(fallback_errors)
    return None, None


def _parse_gps_line(line: str):
    sentence = line.strip()
    if not sentence.startswith("$"):
        return None

    body = sentence[1:].split("*", 1)[0]
    parts = body.split(",")
    if len(parts) < 10:
        return None

    kind = parts[0]
    if kind not in {"GPRMC", "GNRMC"}:
        return None
    if parts[2] != "A":
        return None

    lat = _parse_nmea_coordinate(parts[3], parts[4])
    lon = _parse_nmea_coordinate(parts[5], parts[6])
    gps_date = _format_gps_date(parts[9])
    gps_time = _format_gps_time(parts[1])
    timestamp = _build_fix_timestamp(gps_date, gps_time)

    if lat is None or lon is None or timestamp is None:
        return None

    return {
        "lat": lat,
        "lon": lon,
        "ts": timestamp,
        "date": gps_date,
        "time": gps_time,
    }


def _gps_reader_loop():
    while True:
        device_path, discovery_error = _discover_gps_device()
        if not device_path:
            with RUNTIME_LOCK:
                RUNTIME_STATE["gps_connected"] = False
                RUNTIME_STATE["gps_device"] = ""
                RUNTIME_STATE["gps_error"] = discovery_error
            time.sleep(1)
            continue

        fd = None
        buffer = ""
        try:
            fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
            with RUNTIME_LOCK:
                RUNTIME_STATE["gps_connected"] = True
                RUNTIME_STATE["gps_device"] = device_path
                RUNTIME_STATE["gps_last_seen_ts"] = _utcnow()
                RUNTIME_STATE["gps_error"] = None

            while True:
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    time.sleep(0.2)
                    continue
                except OSError as e:
                    with RUNTIME_LOCK:
                        RUNTIME_STATE["gps_connected"] = False
                        RUNTIME_STATE["gps_error"] = str(e)
                    break

                if not chunk:
                    if not os.path.exists(device_path):
                        with RUNTIME_LOCK:
                            RUNTIME_STATE["gps_connected"] = False
                            RUNTIME_STATE["gps_error"] = "GPS device disconnected"
                        break
                    time.sleep(0.2)
                    continue

                buffer += chunk.decode("ascii", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    fix = _parse_gps_line(line)
                    if not fix:
                        continue
                    with RUNTIME_LOCK:
                        _update_last_gps_locked(
                            fix["lat"],
                            fix["lon"],
                            fix["ts"],
                            gps_date=fix["date"],
                            gps_time=fix["time"],
                        )
                        RUNTIME_STATE["gps_connected"] = True
                        RUNTIME_STATE["gps_device"] = device_path
                        RUNTIME_STATE["gps_last_seen_ts"] = _utcnow()
        except OSError as e:
            with RUNTIME_LOCK:
                RUNTIME_STATE["gps_connected"] = False
                RUNTIME_STATE["gps_device"] = ""
                RUNTIME_STATE["gps_error"] = str(e)
            time.sleep(1)
        except Exception as e:
            with RUNTIME_LOCK:
                RUNTIME_STATE["gps_connected"] = False
                RUNTIME_STATE["gps_error"] = str(e)
            time.sleep(1)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            with RUNTIME_LOCK:
                if not RUNTIME_STATE["gps_connected"]:
                    RUNTIME_STATE["gps_device"] = ""


def _ensure_gps_reader_started():
    global _GPS_READER_THREAD
    with RUNTIME_LOCK:
        if _GPS_READER_THREAD is not None and _GPS_READER_THREAD.is_alive():
            return
        _GPS_READER_THREAD = threading.Thread(target=_gps_reader_loop, name="wraith-gps-reader", daemon=True)
        _GPS_READER_THREAD.start()


def _advance_runtime_state():
    with RUNTIME_LOCK:
        gps_connected = RUNTIME_STATE["gps_connected"]
        gps_device = RUNTIME_STATE["gps_device"]

        if LAST_GPS["ts"] is not None:
            RUNTIME_STATE["gps_last_fix_ts"] = LAST_GPS["ts"]

        mode = RUNTIME_STATE["mode"]
        requested_mode = RUNTIME_STATE["requested_mode"]
        transition_started_ts = RUNTIME_STATE["transition_started_ts"]

        if requested_mode == "prepare_mobile":
            if mode == "PREPARE_MOBILE_PENDING":
                _set_mode_locked("SAFE_TO_UNPLUG_PHONE", "Preparation complete. You can unplug the phone now.")
            elif mode == "SAFE_TO_UNPLUG_PHONE":
                message = f"Waiting for GPS on {gps_device}. Plug in the USB GPS." if gps_device else "Waiting for GPS device. Plug in the USB GPS."
                _set_mode_locked("HEADLESS_WAITING_FOR_GPS", message)
            elif mode == "HEADLESS_WAITING_FOR_GPS" and gps_connected:
                _set_mode_locked("MOBILE_RUNNING_WITH_GPS", f"Mobile mode active. GPS detected on {gps_device}.")
                RUNTIME_STATE["requested_mode"] = None
                RUNTIME_STATE["transition_started_ts"] = None
            elif mode == "MOBILE_RUNNING_WITH_GPS" and not gps_connected:
                _set_mode_locked("MOBILE_RUNNING_NO_GPS", "Mobile mode is running, but GPS is unavailable.")
            elif mode == "MOBILE_RUNNING_NO_GPS" and gps_connected:
                _set_mode_locked("MOBILE_RUNNING_WITH_GPS", f"Mobile mode active. GPS detected on {gps_device}.")

        elif requested_mode == "return_web":
            if mode == "RETURN_PREP_PENDING":
                if gps_connected:
                    _set_mode_locked("RETURN_PREP_PENDING", "Unplug the GPS before reconnecting the phone.")
                else:
                    _set_mode_locked("WEB_REATTACH_WAIT", "GPS removed. Plug the phone back in to regain web UI access.")

        elif mode == "MOBILE_RUNNING_WITH_GPS" and not gps_connected:
            _set_mode_locked("MOBILE_RUNNING_NO_GPS", "Mobile mode is running, but GPS is unavailable.")
        elif mode == "MOBILE_RUNNING_NO_GPS" and gps_connected:
            _set_mode_locked("MOBILE_RUNNING_WITH_GPS", f"Mobile mode active. GPS detected on {gps_device}.")


def _controller_loop():
    while True:
        try:
            _advance_runtime_state()
        except Exception as e:
            with RUNTIME_LOCK:
                RUNTIME_STATE["last_error"] = str(e)
                _set_mode_locked("ERROR_NEEDS_OPERATOR", f"Runtime controller error: {e}")
        time.sleep(1)


def _ensure_runtime_controller_started():
    global _CONTROLLER_THREAD
    should_start_controller = False
    with RUNTIME_LOCK:
        if _CONTROLLER_THREAD is None or not _CONTROLLER_THREAD.is_alive():
            _CONTROLLER_THREAD = threading.Thread(target=_controller_loop, name="wraith-runtime-controller", daemon=True)
            should_start_controller = True
    if should_start_controller:
        _CONTROLLER_THREAD.start()
    _ensure_gps_reader_started()
    _ensure_scan_loop_started()


def _start_runtime_services_on_process_start():
    _ensure_runtime_controller_started()


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
    with RUNTIME_LOCK:
        if LAST_GPS["lat"] is None or LAST_GPS["lon"] is None or LAST_GPS["ts"] is None:
            return None
        snapshot = {
            "lat": LAST_GPS["lat"],
            "lon": LAST_GPS["lon"],
            "ts": LAST_GPS["ts"],
            "date": LAST_GPS.get("date"),
            "time": LAST_GPS.get("time"),
        }
        if "accuracy" in LAST_GPS:
            snapshot["accuracy"] = LAST_GPS["accuracy"]
        return snapshot


def _run_wifi_scan_once():
    with RUNTIME_LOCK:
        if RUNTIME_STATE["wifi_scan_running"]:
            return False, "Wi-Fi scan already running"
        RUNTIME_STATE["wifi_scan_running"] = True

    interface = os.environ.get("WRAITH_WIFI_IFACE", "wlan1")
    try:
        output = run_wifi_scan(interface)
        parsed = parse_iw_scan(output)
        for item in parsed:
            wifi_item = dict(item)
            wifi_item["gps"] = _gps_snapshot()
            log_wifi(interface, wifi_item)
        with RUNTIME_LOCK:
            RUNTIME_STATE["last_wifi_scan_ts"] = _utcnow()
            RUNTIME_STATE["scan_loop_error"] = None
        return True, None
    except subprocess.CalledProcessError as e:
        message = f"Wi-Fi scan failed: {e}"
        with RUNTIME_LOCK:
            RUNTIME_STATE["last_wifi_scan_ts"] = _utcnow()
            RUNTIME_STATE["scan_loop_error"] = message
        return False, message
    except Exception as e:
        message = f"Wi-Fi scan failed: {e}"
        with RUNTIME_LOCK:
            RUNTIME_STATE["last_wifi_scan_ts"] = _utcnow()
            RUNTIME_STATE["scan_loop_error"] = message
        return False, message
    finally:
        with RUNTIME_LOCK:
            RUNTIME_STATE["wifi_scan_running"] = False


def _run_ble_scan_once():
    with RUNTIME_LOCK:
        if RUNTIME_STATE["ble_scan_running"]:
            return False, "BLE scan already running"
        RUNTIME_STATE["ble_scan_running"] = True

    try:
        try:
            output = run_ble_scan()
        except subprocess.CalledProcessError as e:
            output = e.output
        found = parse_ble_scan(output)
        for addr, info in found.items():
            log_ble(addr, info["name"], info["rssi"], gps=_gps_snapshot())
        with RUNTIME_LOCK:
            RUNTIME_STATE["last_ble_scan_ts"] = _utcnow()
            RUNTIME_STATE["scan_loop_error"] = None
        return True, None
    except Exception as e:
        message = f"BLE scan failed: {e}"
        with RUNTIME_LOCK:
            RUNTIME_STATE["last_ble_scan_ts"] = _utcnow()
            RUNTIME_STATE["scan_loop_error"] = message
        return False, message
    finally:
        with RUNTIME_LOCK:
            RUNTIME_STATE["ble_scan_running"] = False


def _scan_loop():
    while True:
        try:
            should_run_wifi = False
            should_run_ble = False
            with RUNTIME_LOCK:
                scanning_enabled = RUNTIME_STATE["scanning_enabled"]
                wifi_interval = RUNTIME_STATE["wifi_scan_interval_sec"]
                ble_interval = RUNTIME_STATE["ble_scan_interval_sec"]
                wifi_running = RUNTIME_STATE["wifi_scan_running"]
                ble_running = RUNTIME_STATE["ble_scan_running"]
                last_wifi_scan_ts = RUNTIME_STATE["last_wifi_scan_ts"]
                last_ble_scan_ts = RUNTIME_STATE["last_ble_scan_ts"]

            if scanning_enabled:
                wifi_elapsed = _seconds_since(last_wifi_scan_ts)
                ble_elapsed = _seconds_since(last_ble_scan_ts)
                should_run_wifi = not wifi_running and (wifi_elapsed is None or wifi_elapsed >= wifi_interval)
                should_run_ble = not ble_running and (ble_elapsed is None or ble_elapsed >= ble_interval)

            if should_run_wifi:
                _run_wifi_scan_once()
            if should_run_ble:
                _run_ble_scan_once()
        except Exception as e:
            with RUNTIME_LOCK:
                RUNTIME_STATE["scan_loop_error"] = f"Scan loop error: {e}"
        time.sleep(1)


def _ensure_scan_loop_started():
    global _SCAN_LOOP_THREAD
    should_start_scan_loop = False
    with RUNTIME_LOCK:
        if _SCAN_LOOP_THREAD is None or not _SCAN_LOOP_THREAD.is_alive():
            _SCAN_LOOP_THREAD = threading.Thread(target=_scan_loop, name="wraith-scan-loop", daemon=True)
            should_start_scan_loop = True
    if should_start_scan_loop:
        _SCAN_LOOP_THREAD.start()


def _format_elapsed_compact(seconds):
    if seconds is None:
        return "never"
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_since(timestamp):
    elapsed = _seconds_since(timestamp)
    if elapsed is None:
        return "never"
    return f"{_format_elapsed_compact(elapsed)} ago"


def _cpu_usage_percent():
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):
        return None
    cpu_count = os.cpu_count() or 1
    return round((load1 / cpu_count) * 100.0, 1)


def _memory_usage_percent():
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            values = {}
            for line in handle:
                key, raw_value = line.split(":", 1)
                values[key] = int(raw_value.strip().split()[0])
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return None
        used = total - available
        return round((used / total) * 100.0, 1)
    except (OSError, ValueError):
        return None


def _disk_usage_percent(path="/"):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return round((usage.used / usage.total) * 100.0, 1)


def _build_threat_summary():
    baseline = get_latest_baseline()
    baseline_id = baseline[0] if baseline else None
    baseline_wifi_set = get_baseline_wifi_set(baseline_id) if baseline_id is not None else set()
    baseline_ble_set = get_baseline_ble_set(baseline_id) if baseline_id is not None else set()
    wifi_rows = get_wifi_rows()
    ble_rows = get_ble_rows()

    high_risk_count = 0
    new_baseline_count = 0

    for row in wifi_rows:
        bssid, ssid, hidden, latest_signal_dbm, strongest_signal_dbm, freq_mhz, channel, security, seen_count, first_seen, last_seen = row
        vendor = vendor_lookup_mac(bssid)
        status, score, tags = classify_wifi(hidden, latest_signal_dbm, vendor, first_seen, baseline_id, bssid, baseline_wifi_set)
        if score >= 6:
            high_risk_count += 1
        if "new-baseline" in tags:
            new_baseline_count += 1

    for row in ble_rows:
        address, name, latest_rssi, strongest_rssi, vendor, seen_count, first_seen, last_seen = row
        status, score, tags = classify_ble(name, vendor, latest_rssi, first_seen, baseline_id, address, baseline_ble_set)
        if score >= 6:
            high_risk_count += 1
        if "new-baseline" in tags:
            new_baseline_count += 1

    return {
        "wifi_devices": len(wifi_rows),
        "ble_devices": len(ble_rows),
        "new_baseline_count": new_baseline_count,
        "high_risk_count": high_risk_count,
    }


@app.route("/")
def index():
    _ensure_runtime_controller_started()
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
    recent_wifi_rows = get_recent_wifi_observations(RECENT_OBSERVATION_LIMIT)
    recent_ble_rows = get_recent_ble_observations(RECENT_OBSERVATION_LIMIT)

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
    recent_wifi = []
    for row in recent_wifi_rows:
        ts, bssid, ssid, signal_dbm, gps_lat, gps_lon, gps_fix_timestamp, gps_date, gps_time = row
        recent_wifi.append({
            "ts": ts,
            "bssid": bssid,
            "ssid": ssid,
            "signal_dbm": signal_dbm,
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "gps_fix_timestamp": gps_fix_timestamp,
            "gps_date": gps_date,
            "gps_time": gps_time,
        })

    recent_ble = []
    for row in recent_ble_rows:
        ts, address, name, rssi, gps_lat, gps_lon, gps_fix_timestamp, gps_date, gps_time = row
        recent_ble.append({
            "ts": ts,
            "address": address,
            "name": name,
            "rssi": rssi,
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "gps_fix_timestamp": gps_fix_timestamp,
            "gps_date": gps_date,
            "gps_time": gps_time,
        })

    return render_template(
        "index.html",
        processed_wifi=processed_wifi,
        processed_ble=processed_ble,
        recent_wifi=recent_wifi,
        recent_ble=recent_ble,
        summary=summary,
        baseline=baseline_info,
        active_filter=active_filter,
        runtime_state=_runtime_snapshot(),
        transition_modes=sorted(TRANSITION_MODES),
    )


@app.route("/gps_update", methods=["POST"])
def gps_update():
    _ensure_runtime_controller_started()
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

    with RUNTIME_LOCK:
        gps_date = timestamp.split("T", 1)[0] if "T" in timestamp else None
        gps_time = timestamp.split("T", 1)[1][:8] if "T" in timestamp else None
        _update_last_gps_locked(lat, lon, timestamp, gps_date=gps_date, gps_time=gps_time, accuracy=accuracy)

    return jsonify({"status": "ok"})


@app.route("/status")
def status():
    _ensure_runtime_controller_started()
    snapshot = _runtime_snapshot()
    if snapshot["mode"] == "WEB_REATTACH_WAIT" and snapshot["phone_ui_expected"]:
        with RUNTIME_LOCK:
            _set_mode_locked("WEB_ATTACHED_RECOVERED", "Phone UI access restored. Wraith is ready for web use.")
            RUNTIME_STATE["requested_mode"] = None
            RUNTIME_STATE["transition_started_ts"] = None
        snapshot = _runtime_snapshot()
    return jsonify(snapshot)


@app.route("/health")
def health():
    _ensure_runtime_controller_started()
    snapshot = _health_snapshot()
    return jsonify(snapshot), 200 if snapshot["ok"] else 503


@app.route("/start_scanning", methods=["POST"])
def start_scanning():
    _ensure_runtime_controller_started()
    with RUNTIME_LOCK:
        RUNTIME_STATE["scanning_enabled"] = True
        _update_scan_intervals_locked()
    return _render_prepare_response()


@app.route("/stop_scanning", methods=["POST"])
def stop_scanning():
    _ensure_runtime_controller_started()
    with RUNTIME_LOCK:
        RUNTIME_STATE["scanning_enabled"] = False
    return _render_prepare_response()


@app.route("/toggle_turbo", methods=["POST"])
def toggle_turbo():
    _ensure_runtime_controller_started()
    with RUNTIME_LOCK:
        RUNTIME_STATE["turbo_enabled"] = not RUNTIME_STATE["turbo_enabled"]
        _update_scan_intervals_locked()
    return _render_prepare_response()


@app.route("/shutdown", methods=["POST"])
def shutdown():
    _ensure_runtime_controller_started()
    threading.Thread(target=_shutdown_system, name="wraith-shutdown", daemon=True).start()
    if request.is_json:
        return jsonify({"status": "ok", "message": "shutdown requested"})
    return redirect(url_for("index", filter=request.args.get("filter", "all")))


@app.route("/prepare_mobile", methods=["POST"])
def prepare_mobile():
    _ensure_runtime_controller_started()
    with RUNTIME_LOCK:
        if RUNTIME_STATE["mode"] not in {"WEB_ATTACHED_IDLE", "WEB_ATTACHED_RECOVERED"}:
            snapshot = dict(RUNTIME_STATE)
            snapshot["allowed_actions"] = _allowed_actions_locked()
            snapshot["active_transition"] = _active_transition_locked()
            return jsonify(snapshot), 409
        _start_workflow_locked(
            "prepare_mobile",
            "PREPARE_MOBILE_PENDING",
            "Preparing mobile mode. Do not unplug the phone yet.",
            phone_ui_expected=False,
            gps_expected=True,
        )
    return _render_prepare_response()


@app.route("/start_return", methods=["POST"])
def start_return():
    _ensure_runtime_controller_started()
    with RUNTIME_LOCK:
        if RUNTIME_STATE["mode"] not in {"HEADLESS_WAITING_FOR_GPS", "MOBILE_RUNNING_WITH_GPS", "MOBILE_RUNNING_NO_GPS"}:
            snapshot = dict(RUNTIME_STATE)
            snapshot["allowed_actions"] = _allowed_actions_locked()
            snapshot["active_transition"] = _active_transition_locked()
            return jsonify(snapshot), 409
        _start_workflow_locked(
            "return_web",
            "RETURN_PREP_PENDING",
            "Starting return flow. Unplug the GPS before reconnecting the phone.",
            phone_ui_expected=True,
            gps_expected=False,
        )
    return _render_prepare_response()


@app.route("/scan_wifi")
def scan_wifi():
    _ensure_runtime_controller_started()
    ok, error = _run_wifi_scan_once()
    if not ok:
        return f"<pre>{error}</pre>", 500

    return redirect(url_for("index", filter=request.args.get("filter", "all")))


@app.route("/scan_ble")
def scan_ble():
    _ensure_runtime_controller_started()
    ok, error = _run_ble_scan_once()
    if not ok:
        return f"<pre>{error}</pre>", 500

    return redirect(url_for("index", filter=request.args.get("filter", "all")))


@app.route("/capture_baseline")
def capture_baseline():
    _ensure_runtime_controller_started()
    create_baseline()
    return redirect(url_for("index", filter=request.args.get("filter", "all")))


_start_runtime_services_on_process_start()


if __name__ == "__main__":
    init_db()
    _ensure_runtime_controller_started()
    app.run(host="0.0.0.0", port=5000)
