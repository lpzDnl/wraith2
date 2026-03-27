import os
import sqlite3
from datetime import datetime

from core.vendors import vendor_lookup_mac

BASE_DIR = os.path.expanduser("~/rf-detector")
DB_PATH = os.path.join(BASE_DIR, "logs", "data.db")


def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wifi_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            interface TEXT NOT NULL,
            bssid TEXT NOT NULL,
            ssid TEXT,
            hidden INTEGER NOT NULL DEFAULT 0,
            signal_dbm REAL,
            freq_mhz INTEGER,
            channel TEXT,
            security TEXT,
            raw TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wifi_devices (
            bssid TEXT PRIMARY KEY,
            ssid TEXT,
            hidden INTEGER NOT NULL DEFAULT 0,
            latest_signal_dbm REAL,
            strongest_signal_dbm REAL,
            freq_mhz INTEGER,
            channel TEXT,
            security TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            interface TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ble_devices (
            address TEXT PRIMARY KEY,
            name TEXT,
            latest_rssi REAL,
            strongest_rssi REAL,
            vendor TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS baseline_wifi (
            baseline_id INTEGER NOT NULL,
            bssid TEXT NOT NULL,
            ssid TEXT,
            hidden INTEGER NOT NULL DEFAULT 0,
            freq_mhz INTEGER,
            channel TEXT,
            security TEXT,
            vendor TEXT,
            PRIMARY KEY (baseline_id, bssid)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS baseline_ble (
            baseline_id INTEGER NOT NULL,
            address TEXT NOT NULL,
            name TEXT,
            vendor TEXT,
            PRIMARY KEY (baseline_id, address)
        )
    """)

    conn.commit()
    conn.close()


def get_latest_baseline():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, created_at FROM baselines ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row


def wifi_in_baseline(bssid: str, baseline_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM baseline_wifi WHERE baseline_id = ? AND bssid = ?", (baseline_id, bssid))
    row = cur.fetchone()
    conn.close()
    return row is not None


def ble_in_baseline(address: str, baseline_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM baseline_ble WHERE baseline_id = ? AND address = ?", (baseline_id, address))
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_baseline_wifi_set(baseline_id: int) -> set[str]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT bssid FROM baseline_wifi WHERE baseline_id = ?", (baseline_id,))
    rows = cur.fetchall()
    conn.close()
    return {row[0] for row in rows}


def get_baseline_ble_set(baseline_id: int) -> set[str]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT address FROM baseline_ble WHERE baseline_id = ?", (baseline_id,))
    rows = cur.fetchall()
    conn.close()
    return {row[0] for row in rows}


def log_wifi(interface: str, item: dict):
    now = datetime.utcnow().isoformat()

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO wifi_observations (
            ts, interface, bssid, ssid, hidden, signal_dbm, freq_mhz, channel, security, raw
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        interface,
        item["bssid"],
        item["ssid"],
        item["hidden"],
        item["signal_dbm"],
        item["freq_mhz"],
        item["channel"],
        item["security"],
        item["raw"]
    ))

    cur.execute("SELECT bssid, strongest_signal_dbm, seen_count FROM wifi_devices WHERE bssid = ?", (item["bssid"],))
    row = cur.fetchone()

    if row is None:
        cur.execute("""
            INSERT INTO wifi_devices (
                bssid, ssid, hidden, latest_signal_dbm, strongest_signal_dbm,
                freq_mhz, channel, security, first_seen, last_seen, seen_count, interface
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["bssid"], item["ssid"], item["hidden"], item["signal_dbm"], item["signal_dbm"],
            item["freq_mhz"], item["channel"], item["security"], now, now, 1, interface
        ))
    else:
        strongest = row[1]
        seen_count = row[2] + 1
        if strongest is None:
            new_strongest = item["signal_dbm"]
        elif item["signal_dbm"] is None:
            new_strongest = strongest
        else:
            new_strongest = max(strongest, item["signal_dbm"])

        cur.execute("""
            UPDATE wifi_devices
            SET ssid = ?, hidden = ?, latest_signal_dbm = ?, strongest_signal_dbm = ?,
                freq_mhz = ?, channel = ?, security = ?, last_seen = ?, seen_count = ?, interface = ?
            WHERE bssid = ?
        """, (
            item["ssid"], item["hidden"], item["signal_dbm"], new_strongest,
            item["freq_mhz"], item["channel"], item["security"], now, seen_count, interface, item["bssid"]
        ))

    conn.commit()
    conn.close()


def log_ble(address: str, name: str, rssi, gps=None):
    now = datetime.utcnow().isoformat()
    vendor = vendor_lookup_mac(address)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT strongest_rssi, seen_count FROM ble_devices WHERE address = ?", (address,))
    row = cur.fetchone()

    if row is None:
        cur.execute("""
            INSERT INTO ble_devices (
                address, name, latest_rssi, strongest_rssi, vendor, first_seen, last_seen, seen_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            address.lower(), name or "", rssi, rssi, vendor, now, now, 1
        ))
    else:
        strongest = row[0]
        seen_count = row[1] + 1
        if strongest is None:
            new_strongest = rssi
        elif rssi is None:
            new_strongest = strongest
        else:
            new_strongest = max(strongest, rssi)

        cur.execute("""
            UPDATE ble_devices
            SET name = ?, latest_rssi = ?, strongest_rssi = ?, vendor = ?, last_seen = ?, seen_count = ?
            WHERE address = ?
        """, (
            name or "", rssi, new_strongest, vendor, now, seen_count, address.lower()
        ))

    conn.commit()
    conn.close()


def create_baseline():
    now = datetime.utcnow().isoformat()
    name = f"baseline-{now}"

    conn = db()
    cur = conn.cursor()

    cur.execute("INSERT INTO baselines (name, created_at) VALUES (?, ?)", (name, now))
    baseline_id = cur.lastrowid

    cur.execute("SELECT bssid, ssid, hidden, freq_mhz, channel, security FROM wifi_devices")
    wifi_rows = cur.fetchall()
    for row in wifi_rows:
        bssid, ssid, hidden, freq_mhz, channel, security = row
        vendor = vendor_lookup_mac(bssid)
        cur.execute("""
            INSERT INTO baseline_wifi (
                baseline_id, bssid, ssid, hidden, freq_mhz, channel, security, vendor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            baseline_id, bssid, ssid, hidden, freq_mhz, channel, security, vendor
        ))

    cur.execute("SELECT address, name, vendor FROM ble_devices")
    ble_rows = cur.fetchall()
    for row in ble_rows:
        address, name, vendor = row
        cur.execute("""
            INSERT INTO baseline_ble (
                baseline_id, address, name, vendor
            ) VALUES (?, ?, ?, ?)
        """, (
            baseline_id, address, name, vendor
        ))

    conn.commit()
    conn.close()


def get_wifi_rows():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            bssid,
            COALESCE(NULLIF(ssid, ''), '[hidden]'),
            hidden,
            latest_signal_dbm,
            strongest_signal_dbm,
            freq_mhz,
            CASE WHEN channel != '' THEN channel ELSE '' END,
            security,
            seen_count,
            first_seen,
            last_seen
        FROM wifi_devices
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_ble_rows():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT address, COALESCE(NULLIF(name, ''), '[unknown]'), latest_rssi, strongest_rssi, vendor,
               seen_count, first_seen, last_seen
        FROM ble_devices
    """)
    rows = cur.fetchall()
    conn.close()
    return rows
