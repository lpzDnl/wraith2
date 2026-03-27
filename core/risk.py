from datetime import datetime, timedelta


def band_label(freq_mhz):
    if freq_mhz is None:
        return "?"
    if 2400 <= freq_mhz < 2500:
        return "2.4 GHz"
    if 5000 <= freq_mhz < 6000:
        return "5 GHz"
    if 5925 <= freq_mhz < 7125:
        return "6 GHz"
    return str(freq_mhz)


def is_new(first_seen: str) -> bool:
    try:
        ts = datetime.fromisoformat(first_seen)
    except Exception:
        return False
    return datetime.utcnow() - ts < timedelta(minutes=15)


def classify_wifi(hidden, latest_signal_dbm, vendor, first_seen, baseline_id, bssid, baseline_wifi_set):
    score = 0
    tags = []

    if baseline_id is not None:
        if bssid in baseline_wifi_set:
            tags.append("known")
        else:
            score += 4
            tags.append("new-baseline")

    if hidden:
        score += 3
        tags.append("hidden")

    if latest_signal_dbm is not None and latest_signal_dbm >= -40:
        score += 3
        tags.append("very-close")
    elif latest_signal_dbm is not None and latest_signal_dbm >= -55:
        score += 1
        tags.append("nearby")

    if is_new(first_seen):
        score += 2
        tags.append("new")

    if vendor in {"Espressif", "Unknown"}:
        score += 1
        tags.append("watch")

    if hidden and latest_signal_dbm is not None and latest_signal_dbm >= -50:
        score += 3
        tags.append("hidden-strong")

    if score >= 6:
        label = "⚠️ high"
    elif score >= 3:
        label = "◔ medium"
    else:
        label = "ok"

    return label, score, ", ".join(tags) if tags else "-"


def classify_ble(name, vendor, latest_rssi, first_seen, baseline_id, address, baseline_ble_set):
    score = 0
    tags = []

    lname = (name or "").lower()

    if baseline_id is not None:
        if address in baseline_ble_set:
            tags.append("known")
        else:
            score += 4
            tags.append("new-baseline")

    if latest_rssi is not None and latest_rssi >= -45:
        score += 3
        tags.append("very-close")
    elif latest_rssi is not None and latest_rssi >= -60:
        score += 1
        tags.append("nearby")

    if is_new(first_seen):
        score += 2
        tags.append("new")

    if vendor in {"Apple", "Unknown"}:
        score += 1
        tags.append("watch")

    if "flipper" in lname:
        score += 4
        tags.append("flipper-like")

    if "cardputer" in lname or "m5stack" in lname:
        score += 3
        tags.append("m5stack-like")

    if "raspberry" in lname:
        score += 2
        tags.append("pi-like")

    if score >= 6:
        label = "⚠️ high"
    elif score >= 3:
        label = "◔ medium"
    else:
        label = "ok"

    return label, score, ", ".join(tags) if tags else "-"
