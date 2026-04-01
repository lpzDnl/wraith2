import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from glob import glob

from core.db import (
    get_baseline_ble_set,
    get_baseline_wifi_set,
    get_ble_rows,
    get_latest_baseline,
    get_recent_ble_observations,
    get_recent_wifi_observations,
    get_wifi_rows,
    init_db,
)
from core.gps_state import derive_gps_state, seconds_since
from core.risk import classify_ble, classify_wifi
from core.vendors import vendor_lookup_mac
from eink.display import EInkDisplay
from eink.screens import boot_screen, ready_screen, rotating_screens, startup_splash_screen


LOGGER = logging.getLogger("wraith-eink")
BOOT_SCREEN_SECONDS = 2
READY_SCREEN_SECONDS = 2
ROTATE_SECONDS = 12
APP_STARTED_MONOTONIC = time.monotonic()
APP_STARTED_AT = datetime.now(timezone.utc)
HEARTBEAT_PATH = "/tmp/wraith-eink-heartbeat"
GPS_BY_ID_GLOB = "/dev/serial/by-id/*"
GPS_TTY_GLOB = "/dev/ttyACM*"
GPS_READ_TIMEOUT_SECONDS = 1.0
GPS_READ_POLL_SECONDS = 0.1
UPS_SCRIPT_PATH = os.path.expanduser("~/UPS_HAT_C/UPS_HAT_C/INA219.py")
UPS_BATTERY_CURRENT_THRESHOLD = -0.05


def _configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s EINK: %(message)s",
    )


def _parse_timestamp(timestamp):
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seconds_since(timestamp):
    return seconds_since(timestamp)


def _timestamp_at_or_after(timestamp, threshold):
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return False
    return parsed >= threshold


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


def _write_heartbeat():
    timestamp = datetime.now(timezone.utc).isoformat()
    tmp_path = f"{HEARTBEAT_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(timestamp)
        handle.write("\n")
    os.replace(tmp_path, HEARTBEAT_PATH)


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


def _get_interface_ipv4(interface):
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", interface],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        fields = line.split()
        if "inet" not in fields:
            continue
        inet_index = fields.index("inet")
        if inet_index + 1 < len(fields):
            return fields[inet_index + 1].split("/", 1)[0]
    return None


def _ups_fallback_snapshot():
    return {
        "battery_percent": None,
        "battery_voltage": None,
        "battery_current": None,
        "battery_power": None,
        "power_state": "--",
    }


def _run_ups_script():
    if not os.path.isfile(UPS_SCRIPT_PATH):
        return None
    try:
        return subprocess.run(
            ["python3", UPS_SCRIPT_PATH],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _parse_ups_metrics(output):
    patterns = {
        "battery_voltage": r"Load Voltage:\s*([-+]?\d+(?:\.\d+)?)\s*V",
        "battery_current": r"Current:\s*([-+]?\d+(?:\.\d+)?)\s*A",
        "battery_power": r"Power:\s*([-+]?\d+(?:\.\d+)?)\s*W",
        "battery_percent": r"Percent:\s*([-+]?\d+(?:\.\d+)?)\s*%",
    }
    metrics = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if not match:
            return None
        try:
            metrics[key] = float(match.group(1))
        except ValueError:
            return None
    return metrics


def _get_ups_snapshot():
    result = _run_ups_script()
    if result is None or result.returncode != 0:
        return _ups_fallback_snapshot()

    metrics = _parse_ups_metrics(result.stdout)
    if metrics is None:
        return _ups_fallback_snapshot()

    battery_current = metrics["battery_current"]
    power_state = "Battery" if battery_current <= UPS_BATTERY_CURRENT_THRESHOLD else "Charging"
    return {
        **metrics,
        "power_state": power_state,
    }


def _build_threat_summary():
    baseline = get_latest_baseline()
    baseline_id = baseline[0] if baseline else None
    baseline_wifi_set = get_baseline_wifi_set(baseline_id) if baseline_id is not None else set()
    baseline_ble_set = get_baseline_ble_set(baseline_id) if baseline_id is not None else set()
    wifi_rows = get_wifi_rows()
    ble_rows = get_ble_rows()

    high_risk_count = 0
    new_baseline_count = 0

    wifi_device_count = 0
    ble_device_count = 0

    for row in wifi_rows:
        bssid, ssid, hidden, latest_signal_dbm, strongest_signal_dbm, freq_mhz, channel, security, seen_count, first_seen, last_seen = row
        vendor = vendor_lookup_mac(bssid)
        status, score, tags = classify_wifi(hidden, latest_signal_dbm, vendor, first_seen, baseline_id, bssid, baseline_wifi_set)
        seen_this_session = _timestamp_at_or_after(last_seen, APP_STARTED_AT)
        if seen_this_session:
            wifi_device_count += 1
        if score >= 6 and seen_this_session:
            high_risk_count += 1
        if "new-baseline" in tags and seen_this_session:
            new_baseline_count += 1

    for row in ble_rows:
        address, name, latest_rssi, strongest_rssi, vendor, seen_count, first_seen, last_seen = row
        status, score, tags = classify_ble(name, vendor, latest_rssi, first_seen, baseline_id, address, baseline_ble_set)
        seen_this_session = _timestamp_at_or_after(last_seen, APP_STARTED_AT)
        if seen_this_session:
            ble_device_count += 1
        if score >= 6 and seen_this_session:
            high_risk_count += 1
        if "new-baseline" in tags and seen_this_session:
            new_baseline_count += 1

    return {
        "wifi_devices": wifi_device_count,
        "ble_devices": ble_device_count,
        "new_baseline_count": new_baseline_count,
        "high_risk_count": high_risk_count,
    }


def _parse_nmea_coordinate(value, direction):
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


def _is_ublox_by_id_path(path):
    name = os.path.basename(path).lower()
    return "u-blox" in name or "ublox" in name


def _discover_gps_device():
    preferred = [path for path in glob(GPS_BY_ID_GLOB) if os.path.exists(path)]
    preferred.sort(key=lambda path: (not _is_ublox_by_id_path(path), path))
    if preferred:
        return preferred[0]

    fallback = [path for path in sorted(glob(GPS_TTY_GLOB)) if os.path.exists(path)]
    if fallback:
        return fallback[0]
    return None


def _update_live_gps_from_line(line, snapshot):
    sentence = line.strip()
    if not sentence.startswith("$"):
        return

    body = sentence[1:].split("*", 1)[0]
    parts = body.split(",")
    if not parts:
        return

    kind = parts[0]

    if kind in {"GPGSV", "GNGSV"} and len(parts) > 3:
        try:
            if parts[3]:
                snapshot["satellites_seen"] = int(parts[3])
        except ValueError:
            pass
        return

    if kind in {"GPGGA", "GNGGA"} and len(parts) > 9:
        try:
            if parts[7]:
                snapshot["satellites_seen"] = int(parts[7])
        except ValueError:
            pass
        try:
            if parts[9]:
                snapshot["gps_alt"] = float(parts[9])
        except ValueError:
            pass
        lat = _parse_nmea_coordinate(parts[2], parts[3]) if len(parts) > 4 else None
        lon = _parse_nmea_coordinate(parts[4], parts[5]) if len(parts) > 5 else None
        if lat is not None and lon is not None:
            snapshot["gps_lat_live"] = lat
            snapshot["gps_lon_live"] = lon
        return

    if kind in {"GPRMC", "GNRMC"} and len(parts) > 7:
        try:
            if parts[7]:
                snapshot["gps_speed"] = float(parts[7])
        except ValueError:
            pass
        return

    if kind in {"GPVTG", "GNVTG"} and len(parts) > 7:
        try:
            if parts[7]:
                snapshot["gps_speed"] = float(parts[7])
        except ValueError:
            pass


def _collect_live_gps_snapshot():
    snapshot = {
        "gps_connected": False,
        "gps_device": None,
        "gps_error": None,
        "satellites_seen": None,
        "gps_alt": None,
        "gps_speed": None,
        "gps_lat_live": None,
        "gps_lon_live": None,
    }

    device_path = _discover_gps_device()
    if not device_path:
        snapshot["gps_error"] = "GPS device not found"
        return snapshot
    snapshot["gps_connected"] = True
    snapshot["gps_device"] = device_path

    deadline = time.monotonic() + GPS_READ_TIMEOUT_SECONDS
    buffer = ""

    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        snapshot["gps_connected"] = False
        snapshot["gps_error"] = str(exc)
        return snapshot

    try:
        while time.monotonic() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                time.sleep(GPS_READ_POLL_SECONDS)
                continue
            except OSError as exc:
                snapshot["gps_connected"] = False
                snapshot["gps_error"] = str(exc)
                break

            if not chunk:
                time.sleep(GPS_READ_POLL_SECONDS)
                continue

            buffer += chunk.decode("ascii", errors="ignore")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                _update_live_gps_from_line(line, snapshot)
    finally:
        os.close(fd)

    return snapshot


def _latest_observation(rows):
    if not rows:
        return None
    return rows[0]


def _latest_gps_observation():
    candidates = []

    for row in get_recent_wifi_observations(500):
        ts, bssid, ssid, signal_dbm, gps_lat, gps_lon, gps_fix_timestamp, gps_date, gps_time = row
        if gps_lat is None or gps_lon is None:
            continue
        candidates.append({
            "ts": ts,
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "gps_fix_timestamp": gps_fix_timestamp or ts,
            "gps_alt": None,
            "gps_speed": None,
        })

    for row in get_recent_ble_observations(500):
        ts, address, name, rssi, gps_lat, gps_lon, gps_fix_timestamp, gps_date, gps_time = row
        if gps_lat is None or gps_lon is None:
            continue
        candidates.append({
            "ts": ts,
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "gps_fix_timestamp": gps_fix_timestamp or ts,
            "gps_alt": None,
            "gps_speed": None,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda item: _parse_timestamp(item["ts"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return candidates[0]


def _build_snapshot():
    local_now = datetime.now().astimezone()
    recent_wifi = _latest_observation(get_recent_wifi_observations(1))
    recent_ble = _latest_observation(get_recent_ble_observations(1))
    last_wifi_scan_ts = recent_wifi[0] if recent_wifi else None
    last_ble_scan_ts = recent_ble[0] if recent_ble else None
    latest_gps = _latest_gps_observation()
    live_gps = _collect_live_gps_snapshot()
    gps_fix_timestamp = latest_gps.get("gps_fix_timestamp") if latest_gps else None
    gps_lock_age = _seconds_since(gps_fix_timestamp)
    gps_lock = (gps_lock_age is not None and gps_lock_age <= 60) or bool(live_gps.get("satellites_seen"))
    gps_state = derive_gps_state(
        gps_fix_timestamp,
        gps_connected=live_gps.get("gps_connected"),
        gps_device=live_gps.get("gps_device"),
        gps_error=live_gps.get("gps_error"),
    )

    recent_scan_age = min(
        [age for age in (_seconds_since(last_wifi_scan_ts), _seconds_since(last_ble_scan_ts)) if age is not None],
        default=None,
    )
    scanning_enabled = recent_scan_age is None or recent_scan_age <= 60

    summary = _build_threat_summary()
    ups = _get_ups_snapshot()
    lan_ip = _get_interface_ipv4("wlan0")
    usb_ip = _get_interface_ipv4("usb1") or _get_interface_ipv4("usb0")
    preferred_ip = lan_ip or usb_ip or "--"
    return {
        "local_time": local_now.strftime("%H:%M:%S"),
        "local_date": local_now.strftime("%Y-%m-%d"),
        "uptime": _format_elapsed_compact(time.monotonic() - APP_STARTED_MONOTONIC),
        "scanning_enabled": scanning_enabled,
        "turbo_enabled": False,
        "gps_lock": gps_lock,
        "gps_state": gps_state,
        "gps_lat": latest_gps.get("gps_lat") if latest_gps else None,
        "gps_lon": latest_gps.get("gps_lon") if latest_gps else None,
        "gps_fix_timestamp": gps_fix_timestamp,
        "gps_alt": live_gps.get("gps_alt"),
        "gps_speed": live_gps.get("gps_speed"),
        "satellites_seen": live_gps.get("satellites_seen"),
        "cpu_percent": _cpu_usage_percent(),
        "ram_percent": _memory_usage_percent(),
        "disk_percent": _disk_usage_percent("/"),
        "lan_ip": lan_ip,
        "usb_ip": usb_ip,
        "preferred_ip": preferred_ip,
        "last_wifi_scan": _format_since(last_wifi_scan_ts),
        "last_ble_scan": _format_since(last_ble_scan_ts),
        "wifi_devices": summary["wifi_devices"],
        "ble_devices": summary["ble_devices"],
        "new_baseline_count": summary["new_baseline_count"],
        "high_risk_count": summary["high_risk_count"],
        **ups,
    }


def main():
    _configure_logging()
    LOGGER.info("runner starting")
    init_db()

    try:
        LOGGER.info("initializing display")
        display = EInkDisplay()
        display.initialize()
        LOGGER.info("display initialized")
    except Exception:
        LOGGER.exception("display initialization failed")
        return 1

    try:
        snapshot = _build_snapshot()
        LOGGER.info("rendering startup splash screen")
        display.render(startup_splash_screen(display.size, snapshot))
        _write_heartbeat()
        LOGGER.info("startup splash screen rendered")
        time.sleep(BOOT_SCREEN_SECONDS)

        snapshot = _build_snapshot()
        LOGGER.info("rendering boot screen")
        display.render(boot_screen(display.size, snapshot))
        _write_heartbeat()
        LOGGER.info("boot screen rendered")
        time.sleep(BOOT_SCREEN_SECONDS)

        snapshot = _build_snapshot()
        LOGGER.info("rendering ready screen")
        display.render(ready_screen(display.size, snapshot))
        _write_heartbeat()
        LOGGER.info("ready screen rendered")
        time.sleep(READY_SCREEN_SECONDS)

        screen_index = 0
        while True:
            snapshot = _build_snapshot()
            screens = rotating_screens(display.size, snapshot)
            LOGGER.info("rendering rotating screen %s", (screen_index % len(screens)) + 1)
            display.render(screens[screen_index % len(screens)])
            _write_heartbeat()
            LOGGER.info("rotating screen %s rendered", (screen_index % len(screens)) + 1)
            screen_index += 1
            time.sleep(ROTATE_SECONDS)
    except KeyboardInterrupt:
        LOGGER.info("runner interrupted")
        return 0
    except Exception:
        LOGGER.exception("runner stopped unexpectedly")
        return 1
    finally:
        try:
            LOGGER.info("putting display to sleep")
            display.sleep()
        except Exception:
            LOGGER.exception("failed to put display to sleep")


if __name__ == "__main__":
    raise SystemExit(main())
