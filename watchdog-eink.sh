#!/bin/bash

set -u

SERVICE_NAME="wraith-eink.service"
HEARTBEAT_PATH="/tmp/wraith-eink-heartbeat"
STALE_THRESHOLD_SECONDS=90
LOGGER_TAG="wraith-eink-watchdog"

log_msg() {
    logger -t "$LOGGER_TAG" "$1"
}

restart_service() {
    log_msg "$1"
    sudo systemctl restart wraith-eink
}

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    restart_service "$SERVICE_NAME inactive; restarting"
    exit 0
fi

if [[ ! -f "$HEARTBEAT_PATH" ]]; then
    restart_service "heartbeat missing at $HEARTBEAT_PATH; restarting"
    exit 0
fi

heartbeat_ts="$(<"$HEARTBEAT_PATH")"

heartbeat_age="$(
python3 - "$heartbeat_ts" <<'PY'
from datetime import datetime, timezone
import sys

timestamp = sys.argv[1].strip()
if not timestamp:
    raise SystemExit(1)

try:
    parsed = datetime.fromisoformat(timestamp)
except ValueError:
    raise SystemExit(1)

if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)

age = max((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds(), 0.0)
print(int(age))
PY
)"

if [[ -z "$heartbeat_age" ]]; then
    restart_service "heartbeat unreadable at $HEARTBEAT_PATH; restarting"
    exit 0
fi

if (( heartbeat_age > STALE_THRESHOLD_SECONDS )); then
    restart_service "heartbeat stale (${heartbeat_age}s > ${STALE_THRESHOLD_SECONDS}s); restarting"
    exit 0
fi
