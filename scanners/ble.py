import re
import subprocess


def run_ble_scan() -> str:
    return subprocess.check_output(
        ["timeout", "8", "bluetoothctl", "--timeout", "6", "scan", "on"],
        stderr=subprocess.STDOUT,
        text=True,
        timeout=12,
    )


def parse_ble_scan(text: str):
    found = {}
    for line in text.splitlines():
        if "Device " not in line:
            continue
        m = re.search(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)$", line)
        if not m:
            continue
        addr = m.group(1).lower()
        name = m.group(2).strip()
        found[addr] = {"name": name, "rssi": None}
    return found
