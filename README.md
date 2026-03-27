📡 Wraith – Portable RF & Device Detection Platform

Wraith is a portable, Raspberry Pi–based RF detection system designed to identify and analyze nearby wireless devices and signals in real time.

It combines:

Wi-Fi scanning (2.4GHz + 5GHz)
Bluetooth/BLE detection
Optional GPS tagging
Baseline comparison and anomaly detection
A mobile-friendly web interface

Wraith is built to operate both:

Connected (via phone UI over USB)
Headless (mobile mode with GPS and logging)
🧠 Core Concepts
Always-On Backend

Wraith runs as a continuous backend service:

Starts on boot
Continues running without a phone attached
Survives USB device changes (phone ↔ GPS)

The web UI is optional and attaches when available.

Dual Interface Model
Connection	Purpose
Wi-Fi (192.168.50.x)	Admin / SSH / file sync
USB (10.189.193.1)	Phone UI access
Workflow Modes
📱 Web Mode
Phone connected via USB
Web UI available at:
http://10.189.193.1:5000
Manual scans, filtering, baseline control
🚶 Mobile Mode
Phone disconnected
Optional USB GPS (/dev/ttyACM0)
Continuous scanning + logging
No UI required
🔄 Mobile Transition System (Phase 1)

Wraith includes a backend-controlled workflow system for safe device transitions.

States:
WEB_ATTACHED_IDLE
PREPARE_MOBILE_PENDING
SAFE_TO_UNPLUG_PHONE
HEADLESS_WAITING_FOR_GPS
MOBILE_RUNNING_WITH_GPS
MOBILE_RUNNING_NO_GPS
RETURN_PREP_PENDING
WEB_REATTACH_WAIT
WEB_ATTACHED_RECOVERED
ERROR_NEEDS_OPERATOR

Transitions are:

backend-controlled
idempotent
independent of browser connection
🧩 Features
📡 Dual-band Wi-Fi scanning (monitor mode capable adapters)
📶 BLE device detection
🧠 Baseline tracking (known vs new devices)
⚠️ Risk classification (new / suspicious / signal proximity)
🧭 Optional GPS tagging (USB GPS dongle)
🔁 Hot-plug tolerant (phone ↔ GPS)
🌐 Lightweight web UI (Flask)
🔄 Live filtering (high / new / proximity)
🧱 Modular architecture (core / scanners / ui split)
🛠 Hardware
Required
Raspberry Pi (tested on Pi Zero 2 W / Pi 5)
Wi-Fi adapter supporting monitor mode (e.g. Alfa 8812AU)
Optional
USB GPS dongle (/dev/ttyACM0)
E-ink display (planned)
Battery / UPS for portability
🚀 Getting Started
Clone
git clone ~/rf-detector
cd wraith2
Install
python3 -m venv venv
source venv/bin/activate
pip install flask
Run
PYTHONPATH=. WRAITH_WIFI_IFACE=wlan1 python ui/app.py

Then open:

http://10.189.193.1:5000
⚙️ Deployment (Recommended)

Wraith is typically deployed using:

systemd service (auto-start on boot)
rsync from development system (Kali Pi)

Example:

rsync -av --delete \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  ~/rf-detector/ \
  wraith@192.168.50.254:~/rf-detector/
🔌 Workflow Example
Web → Mobile
Connect phone → open UI
Click Prepare Mobile
Wait for “Safe to unplug”
Unplug phone
Plug in GPS
Wraith continues headless
Mobile → Web
Stop return workflow (or unplug GPS)
Plug phone back in
Open UI at 10.189.193.1:5000
Resume control
📡 API (Phase 1)
GET /status

Returns current runtime state and device status

POST /prepare_mobile

Initiates mobile transition workflow

POST /start_return

Initiates return to web/UI mode

🧠 Architecture
rf-detector/
├── core/
│   ├── db.py
│   ├── risk.py
│   ├── vendors.py
│
├── scanners/
│   ├── wifi.py
│   ├── ble.py
│
├── ui/
│   ├── app.py
│   ├── templates/
│
└── venv/
⚠️ Limitations
Cannot detect passive/non-transmitting devices
GPS integration currently in-memory (no DB persistence yet)
BLE detection depends on adapter support
USB device naming (/dev/ttyACM0) assumed stable
🔮 Roadmap
Persistent GPS tracking per observation
Device movement analysis
Map visualization
Telegram / alert system
E-ink live status display
Automatic device classification improvements
Hardware button mode switching
⚖️ Disclaimer

Wraith is a detection and analysis tool.
It does not identify intent, decrypt traffic, or guarantee identification of surveillance devices.

Use responsibly and within applicable laws.

👤 Author

Built and actively developed by lpzDnl
Field-focused RF detection and home lab experimentation

💀 Final Note

Wraith is not a dashboard.
It’s becoming a portable RF intelligence tool.
