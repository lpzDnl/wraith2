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
TIME_FONT = _load_font(9)


def _footer(snapshot):
    scan = "on" if snapshot.get("scanning_enabled") else "off"
    turbo = "on" if snapshot.get("turbo_enabled") else "off"
    gps = "lock" if snapshot.get("gps_lock") else "no"
    return f"S:{scan}  T:{turbo}  G:{gps}"


def _new_image(size):
    return Image.new("1", size, 255)


def _measure_text(draw, text, font):
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    except AttributeError:
        return draw.textsize(text, font=font)


def _header_time(snapshot):
    local_time = snapshot.get("local_time", "--:--")
    return local_time[:5] if len(local_time) >= 5 else local_time


def _draw_header(draw, title, snapshot, size):
    width, _ = size
    margin = 8
    y = margin

    draw.text((margin, y), title, font=TITLE_FONT, fill=0)

    local_time = _header_time(snapshot)
    time_width, _ = _measure_text(draw, local_time, TIME_FONT)
    draw.text((width - margin - time_width, y + 2), local_time, font=TIME_FONT, fill=0)

    y += 18
    draw.line((margin, y, width - margin, y), fill=0, width=1)
    return y + 5


def _draw_screen(title, lines, snapshot, size):
    image = _new_image(size)
    draw = ImageDraw.Draw(image)
    margin = 8
    y = _draw_header(draw, title, snapshot, size)

    for line in lines:
        draw.text((margin, y), line, font=BODY_FONT, fill=0)
        y += 12

    _draw_footer(draw, snapshot, size)
    return image


def _format_coord(value):
    if value is None:
        return "--"
    return f"{value:.4f}"


def _format_metric(value, suffix=""):
    if value is None:
        return f"--{suffix}"
    if isinstance(value, float):
        return f"{value:.1f}{suffix}"
    return f"{value}{suffix}"


def _format_ip_line(label, value):
    return f"{label} {value or '--'}"


def _draw_footer(draw, snapshot, size):
    width, height = size
    margin = 8
    footer = _footer(snapshot)
    try:
        footer_box = draw.textbbox((0, 0), footer, font=FOOTER_FONT)
        footer_height = footer_box[3] - footer_box[1]
    except AttributeError:
        footer_height = draw.textsize(footer, font=FOOTER_FONT)[1]
    line_y = height - footer_height - 10
    draw.line((margin, line_y, width - margin, line_y), fill=0, width=1)
    draw.text((margin, height - footer_height - 6), footer, font=FOOTER_FONT, fill=0)


def _draw_bar(draw, x, y, width, height, value):
    draw.rectangle((x, y, x + width, y + height), outline=0, width=1)
    if value is None:
        return
    clamped = max(0.0, min(float(value), 100.0))
    fill_width = int((width - 2) * (clamped / 100.0))
    if fill_width > 0:
        draw.rectangle((x + 1, y + 1, x + 1 + fill_width, y + height - 1), fill=0)


def boot_screen(size, snapshot):
    return _draw_screen(
        "WRAITH",
        [
            "E-ink booting",
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
            f"Uptime {snapshot.get('uptime', '0s')}",
            f"Scanning {scanning}",
            f"GPS {gps}",
        ],
        snapshot,
        size,
    )


def rotating_screens(size, snapshot):
    width, height = size
    margin = 8

    system_screen = _new_image(size)
    draw = ImageDraw.Draw(system_screen)
    y = _draw_header(draw, "System", snapshot, size) + 2

    left_lines = [
        f"Date {snapshot.get('local_date', '---- -- --')}",
        f"Up   {snapshot.get('uptime', '0s')}",
        _format_ip_line("IP", snapshot.get("lan_ip")),
    ]
    left_y = y
    for line in left_lines:
        draw.text((margin, left_y), line, font=BODY_FONT, fill=0)
        left_y += 14

    right_x = (width // 2) + 4
    meter_y = y
    for label, value in (
        ("CPU", snapshot.get("cpu_percent")),
        ("RAM", snapshot.get("ram_percent")),
        ("DSK", snapshot.get("disk_percent")),
    ):
        draw.text((right_x, meter_y), f"{label} {_format_metric(value, '%')}", font=BODY_FONT, fill=0)
        _draw_bar(draw, right_x, meter_y + 12, width - right_x - margin, 8, value)
        meter_y += 20

    _draw_footer(draw, snapshot, size)

    collection_screen = _new_image(size)
    draw = ImageDraw.Draw(collection_screen)
    y = _draw_header(draw, "Collection", snapshot, size) + 2

    left_lines = [
        f"Scan {'on' if snapshot.get('scanning_enabled') else 'off'}",
        f"Turbo {'on' if snapshot.get('turbo_enabled') else 'off'}",
        f"WiFi {snapshot.get('wifi_devices', 0)}",
        f"BLE  {snapshot.get('ble_devices', 0)}",
    ]
    right_lines = [
        f"WiFi {snapshot.get('last_wifi_scan', 'never')}",
        f"BLE  {snapshot.get('last_ble_scan', 'never')}",
        f"New  {snapshot.get('new_baseline_count', 0)}",
        f"High {snapshot.get('high_risk_count', 0)}",
    ]

    left_y = y
    right_y = y
    right_x = (width // 2) + 4
    for line in left_lines:
        draw.text((margin, left_y), line, font=BODY_FONT, fill=0)
        left_y += 14
    for line in right_lines:
        draw.text((right_x, right_y), line, font=BODY_FONT, fill=0)
        right_y += 14

    _draw_footer(draw, snapshot, size)

    threat_screen = _new_image(size)
    draw = ImageDraw.Draw(threat_screen)
    y = margin
    _draw_header(draw, "Threat", snapshot, size)
    time_width, _ = _measure_text(draw, _header_time(snapshot), TIME_FONT)
    gps_x1 = width - margin - time_width - 6
    draw.ellipse((gps_x1 - 10, margin + 2, gps_x1, margin + 12), outline=0, fill=0 if snapshot.get("gps_lock") else 255, width=1)
    y = margin + 25

    severity_blocks = min(max(int(snapshot.get("high_risk_count", 0)), 0), 3)
    for index in range(3):
        x0 = margin + (index * 10)
        draw.rectangle((x0, y - 4, x0 + 6, y + 2), outline=0, fill=0 if index < severity_blocks else 255, width=1)
    y += 1

    left_lines = [
        f"High {snapshot.get('high_risk_count', 0)}",
        f"New  {snapshot.get('new_baseline_count', 0)}",
        f"GPS {snapshot.get('gps_state', 'NO_GPS')}",
        f"Sats {_format_metric(snapshot.get('satellites_seen'))}",
    ]
    right_lines = [
        f"Lat {_format_coord(snapshot.get('gps_lat'))}",
        f"Lon {_format_coord(snapshot.get('gps_lon'))}",
        f"Alt {_format_metric(snapshot.get('gps_alt'))}",
        f"Spd {_format_metric(snapshot.get('gps_speed'))}",
    ]

    left_x = margin
    right_x = (width // 2) + 4
    left_y = y
    right_y = y

    for line in left_lines:
        draw.text((left_x, left_y), line, font=BODY_FONT, fill=0)
        left_y += 14

    for line in right_lines:
        draw.text((right_x, right_y), line, font=BODY_FONT, fill=0)
        right_y += 12

    _draw_footer(draw, snapshot, size)

    return [system_screen, collection_screen, threat_screen]
