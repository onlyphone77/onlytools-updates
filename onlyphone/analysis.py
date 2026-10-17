"""Moteur d'analyse local : extraction d'indices puis règles -> hypothèses classées.

Les « indices » sont des faits observés dans les données lues. Les « hypothèses » sont des
causes possibles avec un niveau de confiance ; plusieurs peuvent coexister. Aucune certitude.
"""
import re
from collections import Counter

CONF_ORDER = {"élevée": 0, "moyenne": 1, "faible": 2}

# --- classification des raisons de démarrage Android (sys.boot.reason / bootreason) ---
_REASONS = [
    ("kernel_panic", r"panic|kernel_crash|ksys"),
    ("watchdog", r"watchdog|wdog|wdt|hw_watchdog|bark|bite"),
    ("power", r"undervolt|uvlo|brownout|ocp|ovp|battery|vbat|low_?power|power_?loss|pmic|smpl"),
    ("thermal", r"therm|overtemp|temperature"),
    ("forced", r"long_?key|longpress|s1_power_key|keys_clear|forced|hard_reset|hw_reset"),
    ("normal", r"userrequested|shell|reboot,ota|^ota|recovery|bootloader|powerkey|cold,powerkey|^shutdown,userrequested|adb|reboot$|^reboot,?$|settings|factory"),
]


def classify_reason(reason):
    r = (reason or "").strip().lower()
    if not r:
        return None
    for name, pat in _REASONS:
        if re.search(pat, r):
            return name
    return "unknown"


def parse_dropbox(text):
    """`dumpsys dropbox` -> Counter des tags (system_server_crash, SYSTEM_TOMBSTONE…)."""
    tags = re.findall(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\S*\s+(\S+)", text or "", re.M)
    return Counter(tags)


def parse_thermal(text):
    st = re.search(r"Thermal Status:\s*(\d)", text or "")
    temps = []
    for m in re.finditer(r"Temperature\{mValue=([-\d.]+), mType=(-?\d+), mName=([^,]+), mStatus=(\d)", text or ""):
        temps.append({"value": float(m.group(1)), "type": int(m.group(2)), "name": m.group(3), "status": int(m.group(4))})
    return {"status": int(st.group(1)) if st else None, "temps": temps}


def parse_df_data(text):
    for line in (text or "").splitlines():
        m = re.search(r"(\d+)%\s+(/data)\s*$", line)
        if m:
            return int(m.group(1))
    return None


def parse_meminfo(text):
    d = dict((k, int(v)) for k, v in re.findall(r"^(\w+):\s+(\d+) kB", text or "", re.M))
    if d.get("MemTotal") and "MemAvailable" in d:
        return round(d["MemAvailable"] * 100.0 / d["MemTotal"], 1)
    return None


def parse_crash_buffer(text):
    t = text or ""
    pkgs = Counter(re.findall(r"Process: ([\w.]+), PID", t))
    return {"java_fatal": len(re.findall(r"FATAL EXCEPTION", t)),
            "native_fatal": len(re.findall(r"Fatal signal \d+", t)),
            "packages": pkgs.most_common(5)}


def collect_evidence(data):
    """data : dict de sorties brutes (voir diagnostic.py). Retourne une liste d'indices observés."""
    ev = []
    add = lambda code, text, src, sev="info": ev.append({"code": code, "text": text, "source": src, "severity": sev})
    props = data.get("props") or {}
    reasons = [props.get("sys.boot.reason"), props.get("ro.boot.bootreason"), props.get("sys.boot.reason.last")]
    for r in [x for x in reasons if x]:
        c = classify_reason(r)
        sev = "info" if c in ("normal", None) else "alerte"
        add("boot_reason:" + (c or "unknown"), "Dernière raison de démarrage : « %s »" % r, "propriétés système", sev)
        break
    hist = props.get("persist.sys.boot.reason.history", "")
    if hist:
        cls = Counter(classify_reason(l.split(",")[0] + "," + l.split(",")[1] if l.count(",") >= 2 else l)
                      for l in hist.splitlines() if l.strip())
        abnormal = sum(v for k, v in cls.items() if k not in ("normal", None))
        if abnormal:
            add("boot_history_abnormal", "Historique : %d démarrage(s) non demandés sur %d enregistrés (%s)" % (
                abnormal, sum(cls.values()), ", ".join("%s×%d" % (k, v) for k, v in cls.items() if k != "normal")),
                "historique des démarrages", "alerte")
            for k in cls:
                if k in ("kernel_panic", "watchdog", "power", "thermal", "forced") and cls[k]:
                    add("boot_reason:" + k, "Historique : raison « %s » observée %d fois" % (k, cls[k]),
                        "historique des démarrages", "alerte")
    db = data.get("dropbox")
    if db:
        for tag, label, code in (("SYSTEM_TOMBSTONE", "plantage(s) natif(s) (tombstone)", "native_crash"),
                                 ("system_server_crash", "plantage(s) du serveur système", "system_server_crash"),
                                 ("system_server_watchdog", "blocage(s) watchdog du serveur système", "boot_reason:watchdog"),
                                 ("system_server_native_crash", "plantage(s) natif(s) du serveur système", "system_server_crash"),
                                 ("SYSTEM_LAST_KMSG", "journal noyau d'un redémarrage précédent", "last_kmsg"),
                                 ("SYSTEM_RESTART", "redémarrage(s) du framework", "system_restart"),
                                 ("data_app_crash", "plantage(s) d'applications", "app_crash"),
                                 ("data_app_anr", "blocage(s) d'applications (ANR)", "app_anr"),
                                 ("SYSTEM_FSCK", "vérification(s) du système de fichiers", "fsck")):
            if db.get(tag):
                sev = "info" if code in ("app_crash", "app_anr", "fsck") and db[tag] < 5 else "alerte"
                add(code, "%d %s" % (db[tag], label), "dropbox système", sev)
    kmsg = data.get("last_kmsg_text") or ""
    if re.search(r"Kernel panic|kernel BUG|Unable to handle kernel", kmsg):
        add("boot_reason:kernel_panic", "Trace de kernel panic dans le journal noyau précédent", "dropbox SYSTEM_LAST_KMSG", "alerte")
    th = data.get("thermal")
    if th:
        if th.get("status") is not None and th["status"] >= 3:
            add("thermal_status", "Statut thermique actuel : niveau %d/6" % th["status"], "thermalservice", "alerte")
        hot = [t for t in th.get("temps", []) if t["status"] >= 3 or (t["type"] in (0, 2) and t["value"] >= 48)]
        for t in hot[:3]:
            add("thermal_hot", "Capteur %s à %.1f °C (niveau %d)" % (t["name"], t["value"], t["status"]), "thermalservice", "alerte")
    if data.get("storage_pct") is not None:
        p = data["storage_pct"]
        add("storage_full" if p >= 95 else "storage_ok", "Stockage /data utilisé à %d %%" % p, "df",
            "alerte" if p >= 95 else "info")
    if data.get("mem_avail_pct") is not None and data["mem_avail_pct"] < 5:
        add("low_memory", "Mémoire disponible très faible (%.1f %%)" % data["mem_avail_pct"], "meminfo", "alerte")
    cb = data.get("crash_buffer")
    if cb and (cb["java_fatal"] or cb["native_fatal"]):
        top = ", ".join("%s (%d)" % p for p in cb["packages"][:3])
        add("crash_buffer", "%d plantage(s) Java, %d natif(s) dans le journal récent%s" % (
            cb["java_fatal"], cb["native_fatal"], (" — " + top) if top else ""), "logcat crash", "alerte")
        if cb["packages"] and cb["packages"][0][1] >= 3:
            add("app_repeat:" + cb["packages"][0][0], "L'application %s plante de façon répétée" % cb["packages"][0][0],
                "logcat crash", "alerte")
    b = data.get("battery")
    if b:
        if b.get("health") in ("dégradé", "à contrôler"):
            add("battery_" + ("worn" if b["health"] == "dégradé" else "check"),
                "Batterie : %s — %s" % (b["health"], b.get("health_basis", "")), "batterie", "alerte")
        for a in b.get("anomalies", []):
            add("battery_anomaly", a, "batterie", "alerte")
    if data.get("adb_dropped"):
        add("usb_unstable", "La connexion USB/ADB s'est interrompue pendant le diagnostic", "ADB", "alerte")
    return ev


def _has(ev, prefix):
    return [e for e in ev if e["code"].startswith(prefix)]


def analyze(ev):
    """Indices -> hypothèses triées par confiance. Catégories : matériel, logiciel, alimentation, indéterminé."""
    H = []

    def hyp(title, cat, conf, evidence, checks):
        H.append({"title": title, "category": cat, "confidence": conf,
                  "evidence": [e["text"] for e in evidence], "next_checks": checks})

    power = _has(ev, "boot_reason:power")
    batt = _has(ev, "battery_worn") + _has(ev, "battery_check") + _has(ev, "battery_anomaly")
    panic = _has(ev, "boot_reason:kernel_panic") + _has(ev, "last_kmsg")
    wd = _has(ev, "boot_reason:watchdog")
    thermal = _has(ev, "boot_reason:thermal") + _has(ev, "thermal_")
    abnormal_reboot = power or panic or wd or _has(ev, "boot_history_abnormal") or _has(ev, "boot_reason:unknown")

    if power:
        hyp("Coupure d'alimentation (batterie ou circuit de charge)", "alimentation", "élevée" if batt else "moyenne",
            power + batt, ["Contrôler la batterie (capacité, tension sous charge) avec un testeur.",
                           "Tester avec une batterie de remplacement connue bonne.",
                           "Inspecter connecteur de charge et nappe ; mesurer la consommation au démarrage.",
                           "Si persistant avec batterie neuve : circuit d'alimentation (PMIC) sur carte mère."])
    elif batt and abnormal_reboot:
        hyp("Batterie usée provoquant des extinctions/redémarrages", "alimentation", "moyenne", batt + abnormal_reboot,
            ["Mesurer la tension de la batterie sous forte charge (lancement appareil photo, jeu).",
             "Remplacer par une batterie testée et observer 24 h."])
    elif batt:
        hyp("Batterie à surveiller (usure ou mesures anormales)", "alimentation", "moyenne", batt,
            ["Vérifier l'état batterie avec l'outil officiel du constructeur.", "Mesurer la capacité réelle au testeur."])
    if panic:
        hyp("Plantage du noyau (kernel panic) — cause logicielle (pilote / mise à jour)", "logiciel", "moyenne", panic,
            ["Vérifier les mises à jour système disponibles.", "Démarrer en mode sans échec et observer.",
             "Sauvegarder puis envisager une réinstallation officielle du système (avec accord du client)."])
        hyp("Plantage du noyau — cause matérielle (mémoire, carte mère, oxydation)", "matériel", "faible", panic,
            ["Inspecter la carte (oxydation, choc).", "Si le kernel panic persiste après réinstallation officielle : défaut carte mère probable."])
    if wd:
        hyp("Blocage système détecté par le watchdog", "logiciel", "moyenne", wd + _has(ev, "storage_full") + _has(ev, "low_memory"),
            ["Libérer de l'espace de stockage.", "Mode sans échec pour exclure une application.",
             "Mettre à jour le système ; si persistant, réinstallation officielle."])
    if thermal:
        hyp("Surchauffe (protection thermique)", "matériel", "moyenne" if _has(ev, "boot_reason:thermal") else "faible", thermal,
            ["Vérifier les applications consommatrices en arrière-plan.", "Contrôler la température en charge et hors charge.",
             "Inspecter batterie gonflée / court-circuit / dissipation thermique."])
    sw = _has(ev, "system_server_crash") + _has(ev, "native_crash") + _has(ev, "system_restart")
    if sw:
        hyp("Instabilité du système Android (plantages du framework)", "logiciel", "moyenne" if len(sw) > 1 else "faible", sw,
            ["Mettre à jour le système et les applications.", "Effacer le cache système via le menu officiel si disponible.",
             "Sauvegarde puis réinitialisation (avec accord explicite du client) si persistant."])
    for e in _has(ev, "app_repeat:"):
        hyp("Application défaillante : %s" % e["code"].split(":", 1)[1], "logiciel", "élevée", [e],
            ["Mettre à jour ou désinstaller l'application (par le client).", "Vider les données de l'application si le client accepte."])
    if _has(ev, "storage_full"):
        hyp("Stockage saturé provoquant lenteurs et plantages", "logiciel", "élevée", _has(ev, "storage_full"),
            ["Libérer au moins 10 % d'espace (photos, caches, applications).", "Puis observer la stabilité."])
    if _has(ev, "usb_unstable"):
        hyp("Connexion USB instable (câble, port ou connecteur du téléphone)", "matériel", "moyenne", _has(ev, "usb_unstable"),
            ["Essayer un autre câble data et un autre port.", "Nettoyer/inspecter le connecteur de charge.",
             "Si instable avec plusieurs câbles : connecteur ou nappe à remplacer."])
    if _has(ev, "boot_reason:forced"):
        hyp("Redémarrage forcé par appui long (utilisateur ou bouton bloqué)", "indéterminé", "faible",
            _has(ev, "boot_reason:forced"), ["Demander au client s'il force le redémarrage.", "Tester le bouton d'alimentation (test guidé)."])
    if (_has(ev, "boot_reason:unknown") or _has(ev, "boot_history_abnormal")) and not (power or panic or wd or thermal):
        hyp("Redémarrages anormaux de cause indéterminée", "indéterminé", "faible",
            _has(ev, "boot_reason:unknown") + _has(ev, "boot_history_abnormal"),
            ["Tester la batterie.", "Mode sans échec.", "Générer un rapport de bug complet (adb bugreport) pour analyse approfondie."])
    H.sort(key=lambda h: CONF_ORDER[h["confidence"]])
    return H


# ---------- iPhone : rapports de panic (.ips) ----------
# (motif, indice lisible, piste, catégorie). Pistes issues des retours d'atelier connus :
# à confirmer par mesure / échange de pièce, jamais une certitude.
IOS_PANIC_RULES = [
    (r"missing sensor\(s\):[^\n\"]*\b(mic1|mic2|prs0)\b", "Capteur manquant (micro / baromètre)",
     "Nappe du connecteur de charge ou nappe power/volume (selon modèle) — vérifier connecteur, puis tester une nappe de remplacement.", "matériel"),
    (r"missing sensor\(s\):[^\n\"]*\bTG0[BV]\b", "Capteur de température batterie manquant (TG0B/TG0V)",
     "Batterie (sonde intégrée) ou connecteur batterie sur carte mère.", "alimentation"),
    (r"missing sensor\(s\):", "Capteur manquant signalé par thermalmonitord",
     "Nappe reliée au capteur cité (lire le nom exact du capteur dans le panic).", "matériel"),
    (r"no successful checkins from thermalmonitord", "Watchdog : thermalmonitord bloqué",
     "Capteur thermique ou nappe défectueuse (souvent connecteur de charge / batterie).", "matériel"),
    (r"no successful checkins from (wifid|bluetoothd|WirelessRadioManager)", "Watchdog : module Wi-Fi/Bluetooth",
     "Puce ou antenne Wi-Fi/Bluetooth (carte mère) ; vérifier d'abord une mise à jour iOS.", "matériel"),
    (r"(SEP Panic|SEPOS|sep panic)", "Panic du Secure Enclave (SEP)",
     "Face ID / Touch ID ou carte mère ; vérifier après restauration iOS officielle.", "matériel"),
    (r"AOP PANIC|aop panic", "Panic du coprocesseur capteurs (AOP)",
     "Capteurs (proximité, accéléromètre…) ou leurs nappes.", "matériel"),
    (r"i2c\d*::_checkTransferStatus|i2c.*timeout", "Erreur de bus I2C",
     "Composant sur bus I2C (caméra, audio, charge) ; débrancher les nappes une à une pour isoler.", "matériel"),
    (r"ANS2|NVMe|nand|AppleANS", "Erreur de stockage (NAND)",
     "Mémoire NAND / contrôleur de stockage ; sauvegarder les données en priorité.", "matériel"),
    (r"SMC PANIC|PMU|PMGR|pmu fault|power ?fault|AppleSMC", "Erreur de gestion d'alimentation (PMU/SMC)",
     "Alimentation : batterie, puis circuit PMU sur carte mère.", "alimentation"),
    (r"baseband|BBPanic|AppleBaseband", "Erreur du modem (baseband)",
     "Modem / carte mère partie radio ; tester sans SIM.", "matériel"),
    (r"WDT timeout|watchdog timeout", "Watchdog système",
     "Blocage système : mise à jour/restauration iOS, puis matériel si persistant.", "logiciel"),
    (r"Kernel data abort|Kernel instruction fetch abort", "Plantage du noyau (data abort)",
     "Logiciel (mise à jour) ou matériel (RAM/CPU) si répété après restauration.", "indéterminé"),
]


def parse_ios_panic(name, text):
    """Fichier .ips -> {date, résumé, sensors}. Format : 1re ligne JSON d'en-tête, puis JSON avec panicString."""
    import json as _json
    body = text or ""
    panic = ""
    if "\n" in body:
        head, rest = body.split("\n", 1)
        try:
            panic = _json.loads(rest).get("panicString", "")
        except ValueError:
            panic = ""
    if not panic:
        m = re.search(r'"panicString"\s*:\s*"(.{0,4000}?)"', body, re.S)
        panic = m.group(1).replace("\\n", "\n") if m else body[:4000]
    m = re.search(r"(\d{4}-\d\d-\d\d)", name or "")
    sensors = re.findall(r"missing sensor\(s\):\s*([^\n\"]+)", panic, re.I)
    first = next((l.strip() for l in panic.splitlines() if l.strip()), "")
    return {"date": m.group(1) if m else None, "summary": first[:240], "text": panic,
            "missing_sensors": sensors[0].strip() if sensors else None}


def analyze_ios_panics(panics):
    """Liste de panics parsés -> (indices, hypothèses)."""
    ev, hyps = [], []
    if not panics:
        return ev, hyps
    dates = sorted({p["date"] for p in panics if p["date"]})
    ev.append({"code": "ios_panics", "text": "%d rapport(s) de panic sur l'iPhone%s" % (
        len(panics), (" (du %s au %s)" % (dates[0], dates[-1])) if dates else ""),
        "source": "rapports d'analyse iOS", "severity": "alerte"})
    hits = {}
    for p in panics:
        for pat, label, advice, cat in IOS_PANIC_RULES:
            if re.search(pat, p["text"], re.I):
                hits.setdefault(label, {"n": 0, "advice": advice, "cat": cat, "ex": p})
                hits[label]["n"] += 1
                break  # une règle (la plus spécifique) par panic
    for label, h in sorted(hits.items(), key=lambda x: -x[1]["n"]):
        detail = h["ex"]["missing_sensors"] or h["ex"]["summary"]
        e = {"code": "ios_panic_rule", "text": "%s — %d panic(s). Ex. : %s" % (label, h["n"], detail[:160]),
             "source": "panic iOS", "severity": "alerte"}
        ev.append(e)
        ratio = h["n"] / float(len(panics))
        conf = "élevée" if (h["n"] >= 3 and ratio >= 0.6) else ("moyenne" if h["n"] >= 2 else "faible")
        hyps.append({"title": label, "category": h["cat"], "confidence": conf, "evidence": [e["text"]],
                     "next_checks": [h["advice"], "Confirmer en remplaçant la pièce suspectée par une pièce testée, puis observer 24 h."]})
    unknown = len(panics) - sum(h["n"] for h in hits.values())
    if unknown:
        e = {"code": "ios_panic_unknown", "text": "%d panic(s) sans signature connue" % unknown, "source": "panic iOS", "severity": "info"}
        ev.append(e)
        hyps.append({"title": "Panics de cause indéterminée", "category": "indéterminé", "confidence": "faible",
                     "evidence": [e["text"]], "next_checks": ["Lire le résumé des panics dans le rapport.", "Restauration iOS officielle (avec accord client), puis observer."]})
    hyps.sort(key=lambda x: CONF_ORDER[x["confidence"]])
    return ev, hyps
