from datetime import datetime, timezone


GPS_STATE_LOCKED = "LOCKED"
GPS_STATE_STALE = "STALE"
GPS_STATE_NO_FIX = "NO_FIX"
GPS_STATE_NO_GPS = "NO_GPS"
GPS_LOCK_MAX_AGE_SECONDS = 60


def _parse_timestamp(timestamp):
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def seconds_since(timestamp, now=None):
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return max((current - parsed).total_seconds(), 0.0)


def derive_gps_state(gps_last_fix_ts, gps_connected=False, gps_device=None, gps_error=None, lock_max_age_seconds=GPS_LOCK_MAX_AGE_SECONDS):
    gps_fix_age = seconds_since(gps_last_fix_ts)
    if gps_fix_age is not None:
        if gps_fix_age <= lock_max_age_seconds:
            return GPS_STATE_LOCKED
        return GPS_STATE_STALE

    gps_available = bool(gps_connected and gps_device and not gps_error)
    if gps_available:
        return GPS_STATE_NO_FIX
    return GPS_STATE_NO_GPS
