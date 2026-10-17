"""Adaptateur iPhone / iPad — lecture seule, sans jailbreak.

Utilise libimobiledevice (open source, LGPL) qui parle le protocole lockdown utilisé par
le Finder / iTunes. Nécessite que l'utilisateur déverrouille l'appareil et touche « Se fier ».
Seules les commandes de lecture sont appelées (ideviceinfo, idevicediagnostics ioregentry).
Sans libimobiledevice, l'appareil est seulement reconnu au niveau USB (nom générique).
"""
import plistlib
import re

from .. import tools, RECOGNIZED, PARTIAL, AUTH_REQUIRED, INACCESSIBLE

PRODUCT_TYPES = {
    "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8", "iPhone10,2": "iPhone 8 Plus", "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X", "iPhone10,6": "iPhone X", "iPhone11,2": "iPhone XS", "iPhone11,4": "iPhone XS Max",
    "iPhone11,6": "iPhone XS Max", "iPhone11,8": "iPhone XR", "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max", "iPhone12,8": "iPhone SE (2e gén.)", "iPhone13,1": "iPhone 12 mini",
    "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13", "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max", "iPhone14,6": "iPhone SE (3e gén.)", "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max", "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,5": "iPhone 16e",
}

_AUTH_PATTERNS = re.compile(r"pair|trust|passcode|password protected|lockdown|invalid host id|user denied", re.I)


def available():
    from . import ios_pmd3
    return ios_pmd3.AVAILABLE or bool(tools.find_tool("ideviceinfo") and tools.find_tool("idevice_id"))


def list_udids():
    from . import ios_pmd3
    if ios_pmd3.AVAILABLE:
        return ios_pmd3.list_udids()
    exe = tools.find_tool("idevice_id")
    if not exe:
        return None
    r = tools.run([exe, "-l"], timeout=8)
    return [l.strip() for l in r.out.splitlines() if l.strip()] if r.code == 0 else []


def parse_info(text):
    d = {}
    for line in (text or "").splitlines():
        if ": " in line and not line.startswith(" "):
            k, v = line.split(": ", 1)
            d[k.strip()] = v.strip()
    return d


def identity_from_info(info):
    pt = info.get("ProductType", "")
    name = PRODUCT_TYPES.get(pt) or (info.get("DeviceClass", "Appareil Apple") + (" (%s)" % pt if pt else ""))
    cls = info.get("DeviceClass", "")
    return {"platform": "ios", "brand": "Apple", "manufacturer": "Apple", "model": name, "model_code": pt or None,
            "os": "iPadOS" if cls == "iPad" else "iOS", "os_version": info.get("ProductVersion"),
            "os_detail": ("build " + info["BuildVersion"]) if info.get("BuildVersion") else "",
            "build": info.get("BuildVersion"), "device_name_hidden": True}


ORIGIN = {"M": "Neuf (vente au détail)", "F": "Reconditionné par Apple", "N": "Appareil de remplacement (échange SAV Apple)",
          "P": "Neuf personnalisé (gravure)"}


def origin_from_model_number(mn):
    if not mn:
        return None
    return ORIGIN.get(mn[0].upper(), "Préfixe « %s » non répertorié" % mn[0])


def details_from_info(info):
    """Champs d'atelier (type 3uTools) réellement fournis par lockdown."""
    mn, reg = info.get("ModelNumber"), info.get("RegionInfo")
    return {k: v for k, v in (
        ("serial_number", info.get("SerialNumber")), ("imei", info.get("InternationalMobileEquipmentIdentity")),
        ("imei2", info.get("InternationalMobileEquipmentIdentity2")), ("meid", info.get("MobileEquipmentIdentifier")),
        ("model_number", (mn + (" " + reg if reg else "")) if mn else None),
        ("origin", origin_from_model_number(mn)), ("hardware_model", info.get("HardwareModel")), ("activation", info.get("ActivationState")),
        ("baseband", info.get("BasebandVersion")), ("wifi_mac", info.get("WiFiAddress")),
        ("bt_mac", info.get("BluetoothAddress")),
        ("passcode", ("oui" if info["PasswordProtected"] else "non") if "PasswordProtected" in info else None),
    ) if v}


# Puces Apple (mode DFU/Recovery : CPID lisible dans la chaîne USB, sans autorisation)
CPID = {"8010": "A10 (iPhone 7)", "8015": "A11 (iPhone 8 / X)", "8020": "A12 (iPhone XS / XR)",
        "8030": "A13 (iPhone 11 / SE 2)", "8101": "A14 (iPhone 12)", "8110": "A15 (iPhone 13 / 14 / SE 3)",
        "8120": "A16 (iPhone 14 Pro / 15)", "8130": "A17 Pro (iPhone 15 Pro)"}


def parse_irecv_serial(s):
    """Chaîne série USB en DFU/Recovery : 'CPID:8030 CPRV:11 ... ECID:001A... SRTG:[iBoot-...]'."""
    d = dict(re_kv.findall(s or ""))
    if "CPID" not in d:
        return None
    return {"cpid": d["CPID"], "chip": CPID.get(d["CPID"].upper(), "puce CPID %s" % d["CPID"]),
            "bdid": d.get("BDID"), "ecid": d.get("ECID"), "srtg": d.get("SRTG")}


re_kv = re.compile(r"(\w+):(\[[^\]]*\]|\S+)")


def classify_error(text):
    """Retourne (état, conseil) à partir d'un message d'erreur libimobiledevice."""
    if _AUTH_PATTERNS.search(text or ""):
        return AUTH_REQUIRED, "Déverrouillez l'appareil et touchez « Se fier à cet ordinateur », puis patientez."
    return INACCESSIBLE, "Communication impossible : vérifiez câble/port et, sous Windows, le pilote Apple Mobile Device."


def _ioreg_battery(plist_bytes):
    try:
        root = plistlib.loads(plist_bytes)
    except Exception:
        return {}
    node = root.get("IORegistry", root) if isinstance(root, dict) else {}
    return node if isinstance(node, dict) else {}


def battery_raw(battery_domain, ioreg):
    """battery_domain : dict de `ideviceinfo -q com.apple.mobile.battery` ; ioreg : dict AppleSmartBattery."""
    raw = {"platform": "ios", "brand": "Apple", "sources": []}
    bd = battery_domain or {}
    lvl = str(bd.get("BatteryCurrentCapacity", ""))
    if lvl.isdigit():
        raw["level"] = int(lvl)
        raw["sources"].append("lockdown com.apple.mobile.battery")
    truthy = lambda x: str(x).lower() == "true"
    if "BatteryIsCharging" in bd:
        raw["status"] = "en charge" if truthy(bd["BatteryIsCharging"]) else (
            "pleine" if truthy(bd.get("FullyCharged")) else "pas en charge")
    if "ExternalConnected" in bd:
        raw["plugged"] = "USB / chargeur" if truthy(bd["ExternalConnected"]) else "non branché (alimentation)"
    io = ioreg or {}
    if io:
        raw["sources"].append("diagnostics_relay (registre batterie)")
        bdata = io.get("BatteryData") if isinstance(io.get("BatteryData"), dict) else {}
        pick = lambda k: io.get(k) if isinstance(io.get(k), int) else (bdata.get(k) if isinstance(bdata.get(k), int) else None)
        if pick("CycleCount") is not None:
            raw["cycles"] = pick("CycleCount")
        design = pick("DesignCapacity")
        full = pick("NominalChargeCapacity") or pick("AppleRawMaxCapacity")
        if design and full and full > 100:  # valeurs en mAh (MaxCapacity peut être un %)
            raw["full_mah"], raw["design_mah"] = full, design
        mcp = pick("MaximumCapacityPercent")
        if mcp is not None:
            raw["vendor_health_pct"] = mcp
            raw["vendor_health_source"] = "Capacité maximale calculée par iOS (identique à Réglages > Batterie)."
            raw["vendor_health_preferred"] = True  # la valeur Apple prime sur l'estimation mAh
        amp = pick("InstantAmperage") if pick("InstantAmperage") is not None else pick("Amperage")
        if amp is not None:
            raw["current_ma"] = amp
        if isinstance(io.get("BatteryShutdownReason"), int) and io["BatteryShutdownReason"]:
            raw["shutdown_reason"] = io["BatteryShutdownReason"]
        if isinstance(io.get("Temperature"), int):
            raw["temp_c"] = io["Temperature"] / 100.0
        if isinstance(io.get("Voltage"), int):
            raw["voltage_v"] = io["Voltage"] / 1000.0
        if raw.get("level") is None and isinstance(io.get("CurrentCapacity"), int) and io["CurrentCapacity"] <= 100:
            raw["level"] = io["CurrentCapacity"]
    return raw


class AppleAdapter(object):
    """Lecture unifiée. pymobiledevice3 si présent (aucune demande « Se fier » déclenchée),
    sinon libimobiledevice (peut afficher la demande « Se fier » sur l'iPhone)."""

    def __init__(self, udid):
        self.udid = udid

    def probe(self, want_battery=True):
        from . import ios_pmd3
        if ios_pmd3.AVAILABLE:
            return ios_pmd3.probe(self.udid, want_battery)
        r = tools.run([tools.find_tool("ideviceinfo"), "-u", self.udid], timeout=12)
        if not r.ok:
            return {"paired": False, "info": {}, "error": r.out + r.err}
        res = {"paired": True, "info": parse_info(r.out), "battery_domain": {}, "ioreg": {}, "error": None}
        if want_battery:
            b = tools.run([tools.find_tool("ideviceinfo"), "-u", self.udid, "-q", "com.apple.mobile.battery"], timeout=10)
            res["battery_domain"] = parse_info(b.out) if b.ok else {}
            diag = tools.find_tool("idevicediagnostics")
            if diag:  # sous-commande ioregentry uniquement (lecture)
                d = tools.run([diag, "-u", self.udid, "ioregentry", "AppleSmartBattery"], timeout=12)
                if d.ok and d.out.strip().startswith("<?xml"):
                    res["ioreg"] = _ioreg_battery(d.out.encode("utf-8"))
        return res

    @staticmethod
    def interpret(res):
        """Résultat de probe -> (état, conseils, identité, détails, batterie brute ou None)."""
        if res.get("error") and not res.get("info"):
            state, hint = classify_error(res["error"])
            return state, [hint], {}, {}, None
        info = res.get("info") or {}
        ident = identity_from_info(info) if info.get("ProductType") else {}
        details = details_from_info(info)
        if not res.get("paired"):
            return PARTIAL, ["Identifié sans autorisation (modèle, iOS). Batterie, IMEI et n° de série : iOS les réserve "
                             "aux ordinateurs autorisés. Déverrouillez et touchez « Se fier » une seule fois : "
                             "ensuite tout sera lu automatiquement à chaque branchement."], ident, details, None
        bd, io = res.get("battery_domain") or {}, res.get("ioreg") or {}
        return RECOGNIZED, [], ident, details, (battery_raw(bd, io) if (bd or io) else None)


def usb_only_identity(product):
    """Reconnaissance USB seule (sans libimobiledevice)."""
    m = re.search(r"iphone|ipad|ipod", product or "", re.I)
    kind = m.group(0) if m else "Appareil Apple"
    kind = {"iphone": "iPhone", "ipad": "iPad", "ipod": "iPod"}.get(kind.lower(), kind)
    return {"platform": "ios", "brand": "Apple", "manufacturer": "Apple", "model": kind + " (modèle exact non lu)",
            "os": "iPadOS" if kind == "iPad" else "iOS", "os_version": None, "_state": PARTIAL,
            "_hint": "Installez libimobiledevice pour lire modèle, version et batterie (voir README)."}
