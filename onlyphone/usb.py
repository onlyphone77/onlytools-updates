"""Énumération USB bas niveau (macOS / Windows / Linux), indépendante d'ADB ou d'Apple.

Permet de voir un appareil même sans débogage USB, en mode spécial (DFU, EDL, download)
ou avec un pilote en erreur. Un câble « charge seule » n'apparaît jamais ici.
"""
import glob
import json
import os
import plistlib
import re
import sys

from . import tools

# VID USB -> marque. "exclusive" : le fabricant ne produit (quasi) que des mobiles.
VENDORS = {
    0x05AC: ("Apple", False), 0x04E8: ("Samsung", False), 0x2717: ("Xiaomi", True),
    0x18D1: ("Google", False), 0x12D1: ("Huawei", False), 0x339B: ("Honor", True),
    0x22D9: ("Oppo / Realme / OnePlus", True), 0x2A70: ("OnePlus", True),
    0x22B8: ("Motorola", True), 0x2D95: ("Vivo", True), 0x2E04: ("Nokia (HMD)", True),
    0x1004: ("LG", False), 0x0FCE: ("Sony", False), 0x0BB4: ("HTC", False),
    0x17EF: ("Lenovo", False), 0x19D2: ("ZTE", False), 0x0B05: ("Asus", False),
    0x1EBF: ("Fairphone", True), 0x2AE5: ("Fairphone", True), 0x29A9: ("Nothing", True),
    0x1BBB: ("Alcatel / TCL", True), 0x0E8D: ("MediaTek", False), 0x05C6: ("Qualcomm", False),
    0x2970: ("Wiko", True), 0x1D45: ("Tecno / Infinix", True), 0x2B4C: ("Doro / Crosscall", False),
}

# Modes spéciaux identifiables par VID:PID
SPECIAL_MODES = {
    (0x05AC, 0x1280): "Mode récupération (Recovery)",
    (0x05AC, 0x1281): "Mode récupération (Recovery)",
    (0x05AC, 0x1282): "Mode récupération (Recovery)",
    (0x05AC, 0x1283): "Mode récupération (Recovery)",
    (0x05AC, 0x1222): "Mode DFU",
    (0x05AC, 0x1881): "Mode DFU / débogage Apple (Debug USB)",
    (0x05AC, 0x1227): "Mode DFU",
    (0x05C6, 0x9008): "Mode EDL Qualcomm (9008)",
    (0x0E8D, 0x0003): "Mode BROM MediaTek",
    (0x0E8D, 0x2000): "Mode Preloader MediaTek",
    (0x04E8, 0x685D): "Mode Download Samsung (Odin)",
    (0x18D1, 0x4EE0): "Mode Fastboot / Bootloader",
    (0x18D1, 0xD00D): "Mode Fastboot / Bootloader",
}

PHONE_WORDS = re.compile(
    r"iphone|ipad|ipod|android|phone|galaxy|mtp|ptp|adb|pixel|redmi|poco|xiaomi|\bmi\b|oneplus|moto|"
    r"huawei|honor|oppo|realme|vivo|nokia|fastboot|rndis|nothing|fairphone|recovery|dfu|qdloader|"
    r"preloader|gadget|mobile|tablet", re.I)


class UsbDevice(object):
    def __init__(self, vid, pid, serial="", product="", manufacturer="", driver_error=None, location=None):
        self.vid, self.pid, self.location = vid, pid, location
        self.serial = (serial or "").strip()
        self.product = (product or "").strip()
        self.manufacturer = (manufacturer or "").strip()
        self.driver_error = driver_error

    @property
    def brand(self):
        v = VENDORS.get(self.vid)
        return v[0] if v else None

    @property
    def special_mode(self):
        return SPECIAL_MODES.get((self.vid, self.pid))

    def is_mobile_candidate(self):
        if self.special_mode:
            return True
        v = VENDORS.get(self.vid)
        if not v:
            return bool(PHONE_WORDS.search(self.product))
        if self.vid == 0x05AC:  # Apple : ignorer claviers, caméras, puces internes…
            return bool(re.search(r"iphone|ipad|ipod", self.product, re.I))
        return v[1] or bool(PHONE_WORDS.search(self.product + " " + self.manufacturer))

    def to_dict(self):
        return {"vid": "%04x" % self.vid, "pid": "%04x" % self.pid, "product": self.product,
                "manufacturer": self.manufacturer, "driver_error": self.driver_error,
                "special_mode": self.special_mode}


# ---------- macOS ----------
def _walk_ioreg(node, out):
    if isinstance(node, dict):
        # appareils uniquement (les interfaces USB ont aussi idVendor mais pas bDeviceClass)
        if "idVendor" in node and "idProduct" in node and ("bDeviceClass" in node or "USB Product Name" in node) \
                and "bInterfaceClass" not in node:
            out.append(UsbDevice(
                int(node["idVendor"]), int(node["idProduct"]),
                node.get("USB Serial Number") or node.get("kUSBSerialNumberString") or "",
                node.get("USB Product Name") or node.get("kUSBProductString") or "",
                node.get("USB Vendor Name") or node.get("kUSBVendorString") or "", location=node.get("locationID")))
        for child in node.get("IORegistryEntryChildren", []) or []:
            _walk_ioreg(child, out)
    elif isinstance(node, list):
        for n in node:
            _walk_ioreg(n, out)


def parse_ioreg_plist(data):
    out, seen = [], set()
    _walk_ioreg(plistlib.loads(data), out)
    res = []
    for d in out:  # dédoublonnage (un appareil peut apparaître dans plusieurs plans)
        k = (d.vid, d.pid, d.serial, d.product)
        if k not in seen:
            seen.add(k)
            res.append(d)
    return res


def _scan_macos():
    import subprocess
    try:
        # classe IOUSBHostDevice tous plans confondus : certains modes (DFU « Debug USB ») n'apparaissent pas dans le plan IOUSB
        data = subprocess.run(["ioreg", "-l", "-w0", "-a", "-r", "-c", "IOUSBHostDevice"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=8).stdout
        return parse_ioreg_plist(data) if data else []
    except Exception:
        return []


# ---------- Windows ----------
_PS = ("Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -like 'USB\\VID_*' } | "
       "Select-Object PNPDeviceID,Name,Manufacturer,ConfigManagerErrorCode | ConvertTo-Json -Compress")
_WIN_ID = re.compile(r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(&MI_[0-9A-F]{2})?\\(.*)", re.I)


def parse_windows_pnp(text):
    try:
        items = json.loads(text or "[]")
    except ValueError:
        return []
    if isinstance(items, dict):
        items = [items]
    parents, child_errors = {}, {}
    for it in items:
        m = _WIN_ID.match(it.get("PNPDeviceID") or "")
        if not m:
            continue
        vid, pid, iface, inst = int(m.group(1), 16), int(m.group(2), 16), m.group(3), m.group(4)
        code = it.get("ConfigManagerErrorCode") or 0
        err = ("Pilote en erreur (code Windows %s)" % code) if code else None
        if iface:  # interface d'un appareil composite (MTP, ADB…)
            if err:
                child_errors[(vid, pid)] = err
            continue
        serial = "" if "&" in inst else inst  # "&" = identifiant généré par Windows, pas un n° de série
        parents[(vid, pid, inst)] = UsbDevice(vid, pid, serial, it.get("Name") or "",
                                              it.get("Manufacturer") or "", err)
    for d in parents.values():
        d.driver_error = d.driver_error or child_errors.get((d.vid, d.pid))
    return list(parents.values())


def _scan_windows():
    r = tools.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS], timeout=15)
    return parse_windows_pnp(r.out) if r.ok else []


# ---------- Linux ----------
def _scan_linux():
    out = []
    for d in glob.glob("/sys/bus/usb/devices/*"):
        def rd(n):
            try:
                with open(os.path.join(d, n)) as f:
                    return f.read().strip()
            except OSError:
                return ""
        v, p = rd("idVendor"), rd("idProduct")
        if v and p:
            out.append(UsbDevice(int(v, 16), int(p, 16), rd("serial"), rd("product"), rd("manufacturer")))
    return out


def scan():
    """Retourne la liste des périphériques USB candidats (smartphones/tablettes)."""
    if sys.platform == "darwin":
        devs = _scan_macos()
    elif os.name == "nt":
        devs = _scan_windows()
    else:
        devs = _scan_linux()
    return [d for d in devs if d.is_mobile_candidate()]


def norm_serial(s):
    return re.sub(r"[^0-9A-Za-z]", "", s or "").upper()
