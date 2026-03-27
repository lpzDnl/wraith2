def vendor_lookup_mac(mac: str) -> str:
    if not mac:
        return "Unknown"

    oui = mac.upper()[0:8]
    known = {
        "60:CF:84": "ASUS",
        "88:A2:9E": "Raspberry Pi / Cypress",
        "DC:A6:32": "Raspberry Pi Trading",
        "B8:27:EB": "Raspberry Pi Trading",
        "28:CD:C1": "Apple",
        "F0:18:98": "Apple",
        "3C:2E:F9": "Apple",
        "CC:46:D6": "Apple",
        "E4:E0:C5": "Samsung",
        "1C:7B:21": "Samsung",
        "A4:C3:F0": "Google",
        "FC:A6:67": "Amazon",
        "AC:63:BE": "Espressif",
        "24:0A:C4": "Espressif",
        "7C:DF:A1": "Espressif",
        "EC:FA:BC": "Espressif",
        "50:C7:BF": "TP-Link",
        "F4:F2:6D": "Ubiquiti",
        "80:2A:A8": "Ubiquiti",
        "00:E0:4C": "Realtek",
        "00:C0:CA": "ALFA / Realtek",
    }
    return known.get(oui, "Unknown")
