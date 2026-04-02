# Wraith RF Detector

Passive Wi-Fi/BLE collection with local classification, SQLite-backed history, a local Web UI, and a Waveshare 2.13" e-ink status display.

## Current Architecture

Wraith currently has two main runtime paths:

- `ui/app.py` runs the collection backend and Web UI. It starts the runtime controller, GPS reader, and scan loop threads, logs Wi-Fi/BLE observations into SQLite, applies baseline/risk classification, and serves the operator UI.
- `eink/run.py` is a separate display process. It reads the current DB state plus live host/GPS state and renders the e-ink pages independently of the Web UI.

Data is persisted in `logs/data.db`. Baselines and observation history survive restarts. The e-ink display is a consumer of that state, not the source of collection.

## E-Ink Behavior

Current startup and rotation behavior:

1. Large `WRAITH` splash page
2. Boot page
3. Ready page
4. Rotating `System`, `Collection`, and `Threat` pages

Current page details:

- Boot page: `E-ink booting` and `Starting display daemon`
- Ready page: uptime, scanning state, GPS lock state, and the current preferred IP on the next line
- Rotating pages: time appears in the upper-right header; date is shown under the time on the rotating pages
- System page: current IP, `Up`, and CPU/RAM/disk utilization bars
- Collection page: scan/turbo state, Wi-Fi/BLE counts, last scan ages, `New`, and `High`
- Threat page: high/new counts, GPS state, satellites, and last known lat/lon with live alt/speed when available

Counter behavior on the e-ink rotating pages is session-scoped:

- `WiFi`, `BLE`, `New`, and `High` reset when the e-ink process restarts
- persisted observations, baselines, and history in SQLite do not reset on restart

## Web UI

The current deployment serves the Web UI with `gunicorn` on port `5000`.

- Access is via the active interface IP: LAN (`wlan0`) or USB (`usb0`/`usb1`)
- The UI is reachable at `http://<active-ip>:5000`
- Current operator workflow favors USB/direct access for the UI; Wi-Fi is mainly used for SSH and management

`ui/app.py` still includes a direct Flask entry point for local/manual runs, but the working deployment path is gunicorn on `:5000`.

## Operations

- SQLite initialization is handled in the backend and e-ink processes
- A DB cleanup timer exists in the deployed system
- The e-ink service is managed by systemd
- The backend/Web UI service is also managed by systemd in deployment
- The e-ink runner writes a heartbeat file used by `watchdog-eink.sh` for recovery

## Hardware / Runtime Notes

- Wi-Fi scans default to `wlan1`
- Web UI network URLs are derived from `wlan0` and `usb0`/`usb1`
- GPS fixes are stored per observation when available
- Live-only GPS fields used by the display include satellite count, altitude, and speed

## Dependency Maintenance

On the Wraith runtime box, install or refresh the project venv dependencies with:

```bash
cd /home/wraith/rf-detector
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Re-run the `pip install -r requirements.txt` step any time `requirements.txt` changes to recover or refresh the runtime environment.

Wraith uses the local Waveshare Python library path on the device:
`/home/wraith/e-Paper/RaspberryPi_JetsonNano/python/lib`

Do not `pip install` the full Waveshare e-Paper GitHub repository on Wraith. Install or refresh only the runtime packages in the venv with `pip install -r requirements.txt`.

The tracked runtime dependencies are `gunicorn`, `Pillow`, `spidev`, `gpiozero`, and `lgpio`.
