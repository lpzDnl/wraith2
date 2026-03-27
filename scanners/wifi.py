import re
import subprocess


def parse_iw_scan(text: str):
    results = []
    current = None

    def finalize(item):
        if item and item.get("bssid"):
            if not item.get("ssid"):
                item["hidden"] = 1
            results.append(item)

    for line in text.splitlines():
        stripped = line.strip()

        bss_match = re.match(r"^BSS\s+([0-9a-fA-F:]{17})", stripped)
        if bss_match:
            finalize(current)
            current = {
                "bssid": bss_match.group(1).lower(),
                "ssid": "",
                "hidden": 0,
                "signal_dbm": None,
                "freq_mhz": None,
                "channel": "",
                "security": "OPEN",
                "raw": stripped + "\n",
            }
            continue

        if current is None:
            continue

        current["raw"] += stripped + "\n"

        if stripped.startswith("SSID:"):
            current["ssid"] = stripped.replace("SSID:", "", 1).strip()
        elif stripped.startswith("signal:"):
            m = re.search(r"(-?\d+(?:\.\d+)?)", stripped)
            if m:
                current["signal_dbm"] = float(m.group(1))
        elif stripped.startswith("freq:"):
            m = re.search(r"(\d+)", stripped)
            if m:
                current["freq_mhz"] = int(m.group(1))
        elif "DS Parameter set: channel" in stripped:
            current["channel"] = stripped.split()[-1]
        elif stripped.startswith("RSN:"):
            current["security"] = "WPA2/WPA3"
        elif stripped.startswith("WPA:"):
            current["security"] = "WPA"
        elif "Privacy" in stripped and current["security"] == "OPEN":
            current["security"] = "WEP/UNKNOWN"

    finalize(current)
    return results


def run_wifi_scan(interface: str) -> str:
    return subprocess.check_output(
        ["sudo", "iw", "dev", interface, "scan"],
        stderr=subprocess.STDOUT,
        text=True,
        timeout=45,
    )
