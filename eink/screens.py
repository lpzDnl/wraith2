from PIL import Image, ImageDraw, ImageFont


def _load_font(size):
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


TITLE_FONT = _load_font(16)
BODY_FONT = _load_font(11)
FOOTER_FONT = _load_font(11)


def _footer(snapshot):
    scan = "on" if snapshot.get("scanning_enabled") else "off"
    turbo = "on" if snapshot.get("turbo_enabled") else "off"
    gps = "lock" if snapshot.get("gps_lock") else "no"
    return f"S:{scan}  T:{turbo}  G:{gps}"


def _new_image(size):
    return Image.new("1", size, 255)


def _draw_screen(title, lines, snapshot, size):
    image = _new_image(size)
    draw = ImageDraw.Draw(image)
    width, height = size
    margin = 8
    y = margin

    draw.text((margin, y), title, font=TITLE_FONT, fill=0)
    y += 18
    draw.line((margin, y, width - margin, y), fill=0, width=1)
    y += 5

    for line in lines:
        draw.text((margin, y), line, font=BODY_FONT, fill=0)
        y += 12

    footer = _footer(snapshot)
    try:
        footer_box = draw.textbbox((0, 0), footer, font=FOOTER_FONT)
        footer_height = footer_box[3] - footer_box[1]
    except AttributeError:
        footer_height = draw.textsize(footer, font=FOOTER_FONT)[1]
    draw.line((margin, height - footer_height - 10, width - margin, height - footer_height - 10), fill=0, width=1)
    draw.text((margin, height - footer_height - 6), footer, font=FOOTER_FONT, fill=0)
    return image


def boot_screen(size, snapshot):
    return _draw_screen(
        "WRAITH",
        [
            "E-ink booting",
            f"Time {snapshot.get('local_time', '--:--:--')}",
            f"Date {snapshot.get('local_date', '---- -- --')}",
            "Starting display daemon",
        ],
        snapshot,
        size,
    )


def ready_screen(size, snapshot):
    gps = "lock" if snapshot.get("gps_lock") else "no lock"
    scanning = "enabled" if snapshot.get("scanning_enabled") else "disabled"
    return _draw_screen(
        "READY",
        [
            f"Time {snapshot.get('local_time', '--:--:--')}",
            f"Uptime {snapshot.get('uptime', '0s')}",
            f"Scanning {scanning}",
            f"GPS {gps}",
        ],
        snapshot,
        size,
    )


def rotating_screens(size, snapshot):
    core_status = _draw_screen(
        "WRAITH",
        [
            f"Time {snapshot.get('local_time', '--:--:--')}",
            f"Date {snapshot.get('local_date', '---- -- --')}",
            f"Uptime {snapshot.get('uptime', '0s')}",
            f"Scan {'enabled' if snapshot.get('scanning_enabled') else 'disabled'}",
            f"Turbo {'on' if snapshot.get('turbo_enabled') else 'off'}",
            f"GPS {'lock' if snapshot.get('gps_lock') else 'no lock'}",
        ],
        snapshot,
        size,
    )

    system_health = _draw_screen(
        "System Health",
        [
            f"CPU {snapshot.get('cpu_percent', '?')}%",
            f"RAM {snapshot.get('ram_percent', '?')}%",
            f"Disk {snapshot.get('disk_percent', '?')}%",
            f"Wi-Fi {snapshot.get('last_wifi_scan', 'never')}",
            f"BLE {snapshot.get('last_ble_scan', 'never')}",
        ],
        snapshot,
        size,
    )

    threat_summary = _draw_screen(
        "Threat Summary",
        [
            f"Wi-Fi dev {snapshot.get('wifi_devices', 0)}",
            f"BLE dev {snapshot.get('ble_devices', 0)}",
            f"New base {snapshot.get('new_baseline_count', 0)}",
            f"High risk {snapshot.get('high_risk_count', 0)}",
        ],
        snapshot,
        size,
    )

    return [core_status, system_health, threat_summary]
