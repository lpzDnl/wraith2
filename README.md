🛰️ Wraith RF Detector

Passive RF detection + threat classification + local UI + e-ink situational display
Built for real-world reconnaissance, not dashboards that just look cool.

🔥 Overview

Wraith is a lightweight RF detection platform designed to:

passively scan Wi-Fi and BLE environments
identify new, anomalous, or high-risk devices
correlate observations with GPS location
present data through:
a local web UI
a low-power e-ink display

This system is optimized for field deployment on Raspberry Pi-class hardware.

⚙️ Core Features
RF Collection
Continuous Wi-Fi + BLE scanning
Signal strength tracking
Device fingerprinting (vendor lookup + behavior)
Threat Classification
Baseline comparison (known vs unknown devices)
Risk scoring engine
Flags:
high risk
new baseline
anomalous behavior
GPS Integration
Latitude / Longitude display
GPS lock detection
Timestamp correlation
(Live-only fields)
altitude (not persisted)
speed (not persisted)
satellites seen (runtime only)
E-Ink Display (2.13” Waveshare V4)
3 rotating production screens:
System
Collection
Threat
Ultra low power, always-on situational awareness
Web UI
Local dashboard (Flask)
Live scan visibility
Control panel:
start/stop scanning
turbo mode
system shutdown
🧠 System Architecture
[ Wi-Fi / BLE Scanners ]
            │
            ▼
        [ Core DB ]
            │
 ┌──────────┴──────────┐
 ▼                     ▼
Web UI             E-Ink Renderer
(Flask)            (systemd service)
Backend handles collection + classification
UI and E-Ink are read-only consumers
GPS is injected at observation time
🖥️ E-Ink Display Layout
Screen 1 — System
Time / Date
Uptime
CPU / RAM / Disk usage
(Planned: usage bars)
Screen 2 — Collection
Scan state
Turbo mode
Wi-Fi / BLE counts
Last scan timestamps
Screen 3 — Threat
High-risk count
New devices detected
GPS lock status
Satellites seen
Lat / Lon
Alt / Speed (live only)
🚀 Installation
Requirements
Raspberry Pi (Zero 2 W / Pi 5 tested)
Waveshare 2.13” e-ink HAT V4
Python 3.10+
Setup
git clone https://github.com/yourusername/rf-detector.git
cd rf-detector

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
▶️ Running
Start Web UI
python ui/app.py

Access:

http://<device-ip>:5000
Start E-Ink Display
sudo systemctl start wraith-eink

Restart after updates:

sudo systemctl restart wraith-eink
🧪 Testing the Display (Hardware Sanity Check)
cd ~/e-Paper/RaspberryPi_JetsonNano/python/examples
export GPIOZERO_PIN_FACTORY=lgpio
python3 epd_2in13_V4_test.py

If this fails, your problem is hardware—not your code.

🧬 Data Model Notes
Wi-Fi and BLE observations are stored in SQLite
GPS fields stored per observation:
gps_lat
gps_lon
gps_fix_timestamp
Not Stored (by design)
altitude
speed
satellites seen

These are runtime-only values used for display.

⚠️ Known Limitations
GPS altitude/speed not yet integrated from source
Satellite count requires direct GPS parser integration
E-Ink layout constrained by resolution (UI must stay minimal)
No remote access security layer (local network only)
🔐 Security Notes
Designed for passive monitoring
Does not transmit RF signals
Web UI is local only (no auth layer yet)
🧱 Future Work
 GPS satellite count integration
 graphical bars for system metrics
 threat severity visualization
 remote access hardening
 alerting (Telegram / MQTT)
🧑‍💻 Developer Notes
Restart wraith-eink after any display change
Do not assume live reload (it doesn’t exist)
Keep e-ink rendering minimal and fast
Avoid unnecessary redraws (hardware limitation)
📜 License
TBD
💀 Final Note

Wraith is not a dashboard.
It’s becoming a portable RF intelligence tool.
