"""Surveillance des branchements : fusionne USB brut + ADB + lockdown Apple, sans action utilisateur.

Un thread interroge les interfaces toutes les POLL_S secondes ; l'identité est lue une fois par
connexion, la batterie au plus toutes les BATTERY_S secondes. L'interface lit seulement le cache.
"""
import threading
import time

from . import usb, battery, analysis, RECOGNIZED, PARTIAL, INACCESSIBLE, UNIDENTIFIED
from .adapters import android, apple

POLL_S = 3
BATTERY_S = 30
PANIC_FAST_S, PANIC_SLOW_S, PANIC_FAST_WINDOW = 20, 120, 180  # iOS écrit le panic-full peu après le redémarrage
REBOOT_WINDOW = 300  # réapparition sous 5 min = redémarrage (ou rebranchement)


def mask(serial):
    s = serial or ""
    return (s[:3] + "•••" + s[-3:]) if len(s) > 8 else ("•••" if s else "")


def build_snapshot(usb_devs, adb_devs, ios_udids):
    """Fusion pure (testable) -> {cle: dict appareil sans identité détaillée}.

    adb_devs / ios_udids valent None si l'outil est absent.
    """
    out = {}
    usb_by_serial = {usb.norm_serial(d.serial): d for d in usb_devs if d.serial}
    used = set()
    for a in adb_devs or []:
        key = usb.norm_serial(a["serial"])
        u = usb_by_serial.get(key)
        if u:
            used.add(id(u))
        state, hint = android.ADB_STATES.get(a["state"], (INACCESSIBLE, "État ADB inconnu : %s" % a["state"]))
        out["adb:" + a["serial"]] = {
            "key": "adb:" + a["serial"], "transport": "adb", "serial": a["serial"], "adb_state": a["state"],
            "platform": "android", "state": state, "hints": [hint] if hint else [],
            "brand": u.brand if u else None, "model": a.get("model", "").replace("_", " ") or None,
            "usb": u.to_dict() if u else None,
        }
    for udid in ios_udids or []:
        key = usb.norm_serial(udid)
        u = usb_by_serial.get(key)
        if u:
            used.add(id(u))
        out["ios:" + udid] = {"key": "ios:" + udid, "transport": "lockdown", "serial": udid, "platform": "ios",
                              "state": PARTIAL, "hints": [], "brand": "Apple", "model": None,
                              "usb": u.to_dict() if u else None}
    for u in usb_devs:
        if id(u) in used:
            continue
        hints, state, platform = [], PARTIAL, None
        if u.vid == 0x05AC:
            platform = "ios"
            if u.special_mode:
                state = INACCESSIBLE
                irecv = apple.parse_irecv_serial(u.serial)
                if irecv:
                    hints.append("Puce détectée : %s — ECID %s." % (irecv["chip"], irecv.get("ecid") or "?"))
                if u.pid == 0x1881:
                    hints.append("Identifiant Apple 1881 (« Debug USB ») : restauration via Apple Configurator (onglet Flash iOS) "
                                 "ou le Finder, ou sortie par redémarrage forcé.")
                hints.append("%s : iOS n'a pas démarré. Batterie et IMEI illisibles dans ce mode (aucun logiciel ne peut "
                             "les lire) : utiliser un programmateur de batterie (JC V1SE, i2C…)." % u.special_mode)
            elif ios_udids is None:
                hints.append("libimobiledevice absent : seule la reconnaissance USB est possible (voir README).")
            else:
                state = INACCESSIBLE
                hints.append("Vu en USB mais le service Apple ne répond pas : iPhone en cours de démarrage, câble/port, "
                             "ou pilote Apple manquant (Windows : installer « Apple Devices » ou iTunes).")
        elif u.special_mode:
            platform = "android"
            state = INACCESSIBLE
            hints.append("%s : mode bas niveau, aucun diagnostic système possible. Ne rien flasher." % u.special_mode)
        elif u.brand:
            platform = "android"
            if adb_devs is None:
                hints.append("ADB introuvable : installez Android Platform-Tools (voir README).")
            else:
                hints.append("Débogage USB non actif ou non autorisé : Options pour les développeurs > Débogage USB, "
                             "puis acceptez la clé sur le téléphone. Vérifiez aussi le mode USB (Transfert de fichiers).")
        else:
            state = UNIDENTIFIED
            hints.append("Fabricant USB non reconnu (VID %04x)." % u.vid)
        if u.driver_error:
            state = INACCESSIBLE
            hints.insert(0, u.driver_error + " : réinstallez le pilote du fabricant / Apple Mobile Device Support.")
        k = "usb:%04x:%04x:%s" % (u.vid, u.pid, usb.norm_serial(u.serial) or u.product)
        extra = {}
        if u.vid == 0x05AC and u.special_mode:
            irecv = apple.parse_irecv_serial(u.serial) or {}
            try:
                extra["ecid"] = int(irecv.get("ecid") or "", 16)
            except ValueError:
                pass
            extra["apple_mode"] = "dfu" if "DFU" in u.special_mode else "recovery"
            extra["usb_location"] = u.location
        product = u.product if u.product and u.product.lower() not in ("mtp", "android") else None
        out[k] = {"key": k, "transport": "usb", "serial": u.serial, "platform": platform, "state": state,
                  "hints": hints, "brand": u.brand, "model": product, "usb": u.to_dict(), **extra}
    return out


class Monitor(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.devices = {}
        self.events = []  # [{t, type, label}]
        self.tools = {}
        self.panic_info = {}   # udid -> résultat de la dernière vérification
        self.blacklist = {}    # imei -> {status, source, checked_at} (saisi par le réparateur)
        self._panic_seen = {}  # udid -> noms de fichiers déjà vus (session)
        self._panic_busy = set()
        self._panic_at = {}    # udid -> dernière vérification
        self._gone_at = {}     # udid -> heure de disparition
        self._stop = threading.Event()
        self._thread = None

    # -- cycle --
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="usb-monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:  # le moniteur ne doit jamais mourir
                self._event("erreur", "Erreur de détection : %s" % e)
            self._stop.wait(POLL_S)

    def _event(self, typ, label):
        with self.lock:
            self.events.append({"t": time.time(), "type": typ, "label": label})
            self.events = self.events[-50:]

    def poll_once(self):
        adb_devs = android.list_devices()
        ios = apple.list_udids()
        usb_devs = usb.scan()
        from . import tools as _t
        self.tools = {"adb": android.adb_path(), "libimobiledevice": apple.available(), "cfgutil": bool(_t.find_tool("cfgutil"))}
        snap = build_snapshot(usb_devs, adb_devs, ios)
        self._cfg_enrich(snap)
        now = time.time()
        with self.lock:
            old = dict(self.devices)
        new = {}
        for k, d in snap.items():
            prev = old.get(k)
            if prev and prev.get("state") == d["state"] and prev.get("adb_state") == d.get("adb_state"):
                keep_hints = prev.get("transport") == "lockdown"
                d = dict(prev, hints=prev.get("hints") if keep_hints else d["hints"], usb=d["usb"] or prev.get("usb"))
            else:
                d["connected_since"] = prev["connected_since"] if prev else now
                d["identity_done"] = False
                d["battery_at"] = 0
            new[k] = d
        for k, d in new.items():
            self._enrich(d, now)
        with self.lock:
            self.devices = new
        for k in set(new) - set(old):
            udid = new[k].get("serial")
            gone = self._gone_at.pop(udid, None) if new[k]["transport"] == "lockdown" else None
            if gone and now - gone <= REBOOT_WINDOW:
                self._event("redémarrage", "Redémarrage (ou rebranchement) détecté : %s — recherche de panic…" % self.label(new[k]))
                self._panic_at.pop(udid, None)
            else:
                self._event("connexion", "Appareil branché : %s" % self.label(new[k]))
        for k in set(old) - set(new):
            if old[k].get("transport") == "lockdown":
                self._gone_at[old[k].get("serial")] = now
            self._event("déconnexion", "Appareil débranché : %s" % self.label(old[k]))
        for d in new.values():
            self._maybe_check_panics(d, now)

    @staticmethod
    def label(d):
        return " ".join(x for x in (d.get("brand"), d.get("model")) if x) or "appareil inconnu"

    def _enrich(self, d, now):
        if d["transport"] == "adb" and d.get("adb_state") == "device":
            ad = android.AndroidAdapter(d["serial"])
            if not d.get("identity_done"):
                ident = ad.identity()
                ident.pop("_props", None)
                if ident.get("model"):
                    d.update({k: v for k, v in ident.items() if v})
                    d["state"] = RECOGNIZED
                else:
                    d["state"] = PARTIAL
                    d["hints"] = ["Propriétés système illisibles : la connexion ADB est peut-être instable."]
                d["identity_done"] = True
            if now - d.get("battery_at", 0) >= BATTERY_S:
                raw = ad.battery(d.get("brand"))
                d["battery"] = battery.assess(raw) if raw else None
                d["battery_at"] = now
        elif d["transport"] == "lockdown":
            if not d.get("identity_done") or now - d.get("battery_at", 0) >= BATTERY_S:
                ap = apple.AppleAdapter(d["serial"])
                state, hints, ident, details, raw = ap.interpret(ap.probe())
                d["state"], d["hints"] = state, hints
                d.update({k: v for k, v in ident.items() if v})
                d["details"] = details
                d["battery"] = battery.assess(raw) if raw else None
                d["identity_done"] = bool(ident)
                d["battery_at"] = now
        elif d["transport"] == "usb" and d.get("platform") == "ios" and not d.get("identity_done") and not d.get("cfg_ecid"):
            ident = apple.usb_only_identity(d.get("model") or "")
            ident.pop("_state"), ident.pop("_hint")
            d.update({k: v for k, v in ident.items() if v})
            if d.get("apple_mode") and not d.get("cfg_ecid"):
                d["model"] = "iPhone / iPad en mode " + ("DFU" if d["apple_mode"] == "dfu" else "Récupération")
            d["identity_done"] = True
        if d.get("battery") is None and d["state"] != RECOGNIZED:
            d["battery_note"] = "Batterie non lisible tant que l'appareil n'est pas reconnu et autorisé."

    def refresh(self, key):
        """Force une relecture complète (ex. après autorisation)."""
        with self.lock:
            if key in self.devices:
                self.devices[key]["identity_done"] = False
                self.devices[key]["battery_at"] = 0

    def _cfg_enrich(self, snap):
        """Mode DFU/Récupération : modèle exact + ECID via Apple Configurator (si installé), appairé par position USB."""
        special = [d for d in snap.values() if d.get("apple_mode")]
        if not special:
            return
        from . import tools as _t
        exe = _t.find_tool("cfgutil")
        if not exe:
            return
        now = time.time()
        if now - getattr(self, "_cfg_at", 0) > 8:
            from .flash_runner import parse_cfg_list
            r = _t.run([exe, "--format", "JSON", "list"], timeout=20)
            self._cfg_list, self._cfg_at = (parse_cfg_list(r.out) if r.ok else []), now
        cfg = [c for c in getattr(self, "_cfg_list", []) if not c.get("udid")]  # sans UDID = DFU/Récupération
        for d in special:
            m = [c for c in cfg if d.get("usb_location") is not None and c.get("location") == d.get("usb_location")]
            if not m and len(cfg) == 1 and len(special) == 1:
                m = cfg
            if m:
                c = m[0]
                d["model_code"] = c.get("type")
                d["cfg_ecid"] = c["ecid"]
                name = apple.PRODUCT_TYPES.get(c.get("type") or "", c.get("type") or "iPhone")
                d["model"] = "%s en mode %s" % (name, "DFU" if d["apple_mode"] == "dfu" else "Récupération")
                d["identity_done"] = True

    def _maybe_check_panics(self, d, now):
        from .adapters import ios_pmd3
        udid = d.get("serial")
        if d["transport"] != "lockdown" or d.get("state") != RECOGNIZED or not ios_pmd3.AVAILABLE or udid in self._panic_busy:
            return
        interval = PANIC_FAST_S if now - d.get("connected_since", now) < PANIC_FAST_WINDOW else PANIC_SLOW_S
        if now - self._panic_at.get(udid, 0) < interval:
            return
        self._panic_at[udid] = now
        self._panic_busy.add(udid)
        threading.Thread(target=self._check_panics, args=(udid, self.label(d)), daemon=True).start()

    def _check_panics(self, udid, label):
        from .adapters import ios_pmd3
        try:
            files = ios_pmd3.panics(udid)
            if files is None:
                return
            self.record_panics(udid, label, files)
        finally:
            self._panic_busy.discard(udid)

    def record_panics(self, udid, label, files):
        """Analyse + détection des NOUVEAUX panics (pure hors E/S, testable)."""
        parsed = [dict(analysis.parse_ios_panic(f["name"], f["text"]), name=f["name"]) for f in files]
        names = {p["name"] for p in parsed}
        seen = self._panic_seen.get(udid)
        new = sorted(names - seen) if seen is not None else []
        self._panic_seen[udid] = names | (seen or set())
        _, hyps = analysis.analyze_ios_panics(parsed)
        info = {"checked_at": time.time(), "count": len(parsed), "new": new, "hypotheses": hyps[:3],
                "latest": [{"name": p["name"], "date": p["date"], "summary": p["summary"][:200],
                            "missing_sensors": p["missing_sensors"]} for p in sorted(parsed, key=lambda x: x["name"], reverse=True)[:5]]}
        with self.lock:
            prev_new = (self.panic_info.get(udid) or {}).get("new") or []
            info["new"] = sorted(set(prev_new) | set(new))  # reste « nouveau » pour la session
            self.panic_info[udid] = info
        if new:
            top = hyps[0]["title"] if hyps else "cause indéterminée"
            self._event("panic", "⚠ Nouveau panic détecté sur %s : %s" % (label, top))
        return info

    def public(self):
        with self.lock:
            devs = []
            for d in self.devices.values():
                pub = {k: v for k, v in d.items() if k not in ("serial",)}
                pub["serial_masked"] = mask(d.get("serial"))
                pub["panic_info"] = self.panic_info.get(d.get("serial"))
                pub["blacklist"] = self.blacklist.get((d.get("details") or {}).get("imei") or "")
                devs.append(pub)
            return {"devices": sorted(devs, key=lambda x: x.get("connected_since", 0)),
                    "events": list(self.events[-10:]), "tools": dict(self.tools)}

    def get(self, key):
        with self.lock:
            d = self.devices.get(key)
            return dict(d) if d else None
