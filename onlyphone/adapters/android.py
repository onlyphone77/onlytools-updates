"""Adaptateur Android via ADB — lecture seule.

Toutes les commandes shell passent par `shell()` qui n'autorise qu'une liste blanche
de commandes de lecture : aucune écriture, réinstallation, flash ou redémarrage possible.
"""
import re

from .. import tools, RECOGNIZED, PARTIAL, AUTH_REQUIRED, INACCESSIBLE

_ALLOWED = ("getprop", "dumpsys ", "cat /sys/class/power_supply/", "cat /proc/meminfo",
            "cat /proc/uptime", "logcat -d", "logcat -b ", "df ", "settings get ", "uptime",
            "ls /sys/class/power_supply", "service call iphonesubinfo ")
_FORBIDDEN = re.compile(r"[;&|<>`$]|\b(rm|reboot|setprop|wipe|dd|pm|am|svc|input|mv|cp|chmod|put|delete|"
                        r"format|flash|recovery|install|uninstall|clear|set|reset|unplug|enable|disable|kill)\b")

ADB_STATES = {
    "device": (RECOGNIZED, None),
    "unauthorized": (AUTH_REQUIRED, "Déverrouillez le téléphone et acceptez « Autoriser le débogage USB » (empreinte RSA)."),
    "offline": (INACCESSIBLE, "Communication ADB hors ligne : rebranchez le câble, changez de port ou de câble, redémarrez le service ADB."),
    "no permissions": (AUTH_REQUIRED, "Droits USB insuffisants sur l'ordinateur (règles udev / pilote)."),
    "recovery": (PARTIAL, "Téléphone en mode Recovery."),
    "sideload": (PARTIAL, "Téléphone en mode Sideload."),
    "bootloader": (PARTIAL, "Téléphone en mode Bootloader / Fastboot."),
    "rescue": (PARTIAL, "Téléphone en mode Rescue."),
    "authorizing": (AUTH_REQUIRED, "Autorisation en cours : validez sur le téléphone."),
    "connecting": (INACCESSIBLE, "Connexion ADB en cours…"),
}

BRAND_NAMES = {"samsung": "Samsung", "xiaomi": "Xiaomi", "redmi": "Redmi", "poco": "POCO", "google": "Google",
               "huawei": "Huawei", "honor": "Honor", "oppo": "Oppo", "oneplus": "OnePlus", "realme": "Realme",
               "motorola": "Motorola", "vivo": "Vivo", "nokia": "Nokia", "sony": "Sony", "lge": "LG",
               "asus": "Asus", "nothing": "Nothing", "fairphone": "Fairphone", "lenovo": "Lenovo", "zte": "ZTE"}

MARKET_NAME_PROPS = ("ro.product.marketname", "ro.product.vendor.marketname", "ro.config.marketing_name",
                     "ro.vendor.oplus.market.name", "ro.oppo.market.name", "ro.product.odm.marketname")

BMS_CODES = {"1": "unknown", "2": "good", "3": "overheat", "4": "dead", "5": "over_voltage", "6": "failure", "7": "cold"}
STATUS_CODES = {"1": "inconnu", "2": "en charge", "3": "décharge", "4": "pas en charge", "5": "pleine"}


def adb_path():
    return tools.find_tool("adb")


# ---------- Parseurs purs (testables) ----------
def parse_devices(text):
    """`adb devices -l` -> liste de dicts {serial, state, model, product, device}."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        m = re.match(r"(\S+)\s+(no permissions|\S+)(.*)$", line)
        if not m:
            continue
        d = {"serial": m.group(1), "state": m.group(2)}
        for k, v in re.findall(r"(\w+):(\S+)", m.group(3)):
            d[k] = v
        out.append(d)
    return out


def parse_getprop(text):
    return dict(re.findall(r"^\[([^\]]+)\]:\s*\[(.*)\]\s*$", text or "", re.M))


def identity_from_props(p):
    brand_raw = (p.get("ro.product.brand") or p.get("ro.product.vendor.brand") or "").strip()
    manu = (p.get("ro.product.manufacturer") or "").strip()
    brand = BRAND_NAMES.get(brand_raw.lower(), brand_raw.capitalize() if brand_raw else None)
    model_code = (p.get("ro.product.model") or "").strip()
    market = next((p[k].strip() for k in MARKET_NAME_PROPS if p.get(k, "").strip()), "")
    skin = None
    if p.get("ro.miui.ui.version.name"):
        skin = "MIUI/HyperOS " + p["ro.miui.ui.version.name"]
    elif p.get("ro.build.version.emui"):
        skin = p["ro.build.version.emui"]
    elif p.get("ro.build.version.oneui"):
        skin = "One UI %s" % p["ro.build.version.oneui"]
    rel = p.get("ro.build.version.release")
    return {
        "platform": "android", "brand": brand, "manufacturer": manu.capitalize() if manu else None,
        "model": market or model_code or None, "model_code": model_code or None,
        "os": "Android", "os_version": rel,
        "os_detail": ", ".join(x for x in ("API " + p["ro.build.version.sdk"] if p.get("ro.build.version.sdk") else "",
                                            skin or "", "patch " + p["ro.build.version.security_patch"]
                                            if p.get("ro.build.version.security_patch") else "") if x),
        "build": p.get("ro.build.display.id"),
    }


def parse_parcel_string(text):
    """Sortie de `service call iphonesubinfo N` -> chaîne (ex. IMEI) ou None."""
    chunks = "".join(re.findall(r"'(.*?)'", text or ""))
    digits = re.sub(r"[^0-9A-Fa-f]", "", chunks.replace(".", ""))
    return digits if len(digits) in (14, 15) else None


def parse_kv(text):
    """Lignes `clé: valeur` (dumpsys battery, etc.)."""
    d = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return d


def parse_sysfs_dump(text):
    """Sortie de `cat` de fichiers sysfs précédés de leur nom (format nom=valeur)."""
    return {k: v for k, v in re.findall(r"^(\w+)=(-?\d+)\s*$", text or "", re.M)}


def battery_raw(dumpsys_text, sysfs, brand=None):
    b = parse_kv(dumpsys_text)
    raw = {"platform": "android", "brand": brand, "sources": []}
    if "level" in b:
        try:
            scale = float(b.get("scale") or 100) or 100
            raw["level"] = float(b["level"]) * 100.0 / scale
            raw["sources"].append("dumpsys battery")
        except ValueError:
            pass
    raw["status"] = STATUS_CODES.get(b.get("status"), b.get("status"))
    plugged = [n for n in ("ac", "usb", "wireless", "dock") if b.get(n + " powered") == "true"]
    raw["plugged"] = ", ".join(plugged).upper() if plugged else "non branché (alimentation)"
    if b.get("temperature", "").lstrip("-").isdigit():
        raw["temp_c"] = int(b["temperature"]) / 10.0
    if b.get("voltage", "").isdigit():
        mv = int(b["voltage"])
        raw["voltage_v"] = mv / 1000.0 if mv > 100 else float(mv)
    raw["bms_health"] = BMS_CODES.get(b.get("health"), (b.get("health") or "").lower() or None)
    for key in ("cycle count", "battery cycle count", "cycle_count"):
        if b.get(key, "").isdigit():
            raw["cycles"] = int(b[key])
    # Samsung : ASOC = état de santé calculé par le BMS Samsung
    if b.get("msavedbatteryasoc", "").isdigit():
        raw["vendor_health_pct"] = int(b["msavedbatteryasoc"])
        raw["vendor_health_source"] = "Valeur ASOC fournie par Samsung (dumpsys battery)."
    s = sysfs or {}
    if "cycles" not in raw:
        for k in ("cycle_count", "battery_cycle"):
            if s.get(k, "").isdigit() and int(s[k]) > 0:
                raw["cycles"] = int(s[k])
                break
    try:
        full, design = int(s.get("charge_full", 0)), int(s.get("charge_full_design", 0))
        if full > 0 and design > 0:
            div = 1000.0 if design > 100000 else 1.0  # µAh -> mAh
            raw["full_mah"], raw["design_mah"] = full / div, design / div
            raw["sources"].append("sysfs power_supply")
    except ValueError:
        pass
    return raw


SYSFS_FILES = ("cycle_count", "battery_cycle", "charge_full", "charge_full_design")


class AndroidAdapter(object):
    def __init__(self, serial):
        self.serial = serial

    def _adb(self, args, timeout=15):
        exe = adb_path()
        if not exe:
            return tools.Result(127, "", "adb introuvable", missing=True)
        return tools.run([exe, "-s", self.serial] + args, timeout=timeout)

    def shell(self, cmd, timeout=20):
        """Commande shell en lecture seule (liste blanche)."""
        if not cmd.startswith(_ALLOWED) or _FORBIDDEN.search(cmd.split(" ", 1)[1] if " " in cmd else ""):
            raise PermissionError("Commande refusée (lecture seule) : %s" % cmd)
        return self._adb(["shell", cmd], timeout=timeout)

    def props(self):
        r = self.shell("getprop")
        return parse_getprop(r.out) if r.ok else {}

    def identity(self):
        p = self.props()
        ident = identity_from_props(p) if p else {}
        ident["details"] = self.details(p) if p else {}
        ident["_props"] = p
        return ident

    def details(self, props):
        d = {"serial_number": props.get("ro.serialno") or props.get("ro.boot.serialno")}
        imei = None
        for n in ("1", "4"):  # getDeviceId selon versions ; refusé par Android 10+ sans privilège
            r = self.shell("service call iphonesubinfo " + n, timeout=5)
            imei = parse_parcel_string(r.out) if r.ok else None
            if imei:
                break
        d["imei"] = imei
        if not imei:
            d["imei_note"] = "IMEI protégé par Android 10+ : composer *#06# sur le téléphone."
        m = self.shell("cat /proc/meminfo", timeout=5)
        mt = re.search(r"MemTotal:\s+(\d+)", m.out or "")
        if mt:
            d["ram"] = "%.0f Go" % (int(mt.group(1)) / 1024.0 / 1024.0 + 0.4)
        lock = props.get("ro.boot.flash.locked") or props.get("ro.boot.verifiedbootstate")
        if lock:
            d["bootloader"] = {"1": "verrouillé", "0": "déverrouillé", "green": "verrouillé (green)",
                               "orange": "déverrouillé (orange)", "yellow": "clé personnalisée"}.get(lock, lock)
        return {k: v for k, v in d.items() if v}

    def battery(self, brand=None):
        r = self.shell("dumpsys battery")
        if not r.ok:
            return None
        sysfs = {}
        for name in SYSFS_FILES:
            s = self.shell("cat /sys/class/power_supply/battery/" + name, timeout=5)
            if s.ok and s.out.strip().lstrip("-").isdigit():
                sysfs[name] = s.out.strip()
        return battery_raw(r.out, sysfs, brand)


def list_devices():
    exe = adb_path()
    if not exe:
        return None
    r = tools.run([exe, "devices", "-l"], timeout=10)
    return parse_devices(r.out) if r.code == 0 else []
