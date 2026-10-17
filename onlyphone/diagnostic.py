"""Orchestration du diagnostic complet + catalogue des tests guidés."""
import re
import threading
import time
import uuid

from . import analysis, battery, RECOGNIZED
from .adapters import android, apple

# Tests guidés : "auto" = vérification automatique possible sur Android via ADB (lecture seule).
TESTS = [
    {"id": "screen", "label": "Écran (pixels, couleurs, luminosité)", "auto": None,
     "procedure": "Afficher des fonds unis blanc/noir/rouge/vert/bleu (ex. image plein écran). Chercher pixels morts, taches, lignes, jaunissement. Varier la luminosité."},
    {"id": "touch", "label": "Tactile", "auto": None,
     "procedure": "Ouvrir une application de dessin et tracer des lignes sur toute la surface, bords compris. Aucune zone morte ni saut."},
    {"id": "battery_charge", "label": "Batterie et charge", "auto": "battery",
     "procedure": "Brancher le chargeur : le statut doit passer « en charge ». Vérifier la montée du pourcentage sur 5 min."},
    {"id": "cameras", "label": "Caméras et flash", "auto": "cameras",
     "procedure": "Ouvrir l'appareil photo : tester chaque objectif arrière, la caméra avant, l'autofocus, le flash et la vidéo."},
    {"id": "audio", "label": "Microphones et haut-parleurs", "auto": None,
     "procedure": "Enregistrer un mémo vocal et le réécouter (micro bas). Tester haut-parleur, écouteur d'appel et micro en appel / vidéo (micro haut)."},
    {"id": "wifi", "label": "Wi-Fi", "auto": "wifi",
     "procedure": "Se connecter au Wi-Fi de la boutique et charger une page web."},
    {"id": "bluetooth", "label": "Bluetooth", "auto": "bluetooth",
     "procedure": "Activer le Bluetooth et rechercher un appareil (enceinte / écouteurs de test)."},
    {"id": "sensors", "label": "Capteurs (proximité, gyroscope, boussole…)", "auto": "sensors",
     "procedure": "Proximité : écran qui s'éteint en appel en couvrant le haut. Rotation automatique. Boussole."},
    {"id": "buttons", "label": "Boutons (volume, alimentation, silencieux)", "auto": None,
     "procedure": "Appuyer sur chaque bouton : réaction immédiate, clic franc, pas de bouton enfoncé en permanence."},
    {"id": "usb_port", "label": "USB et connecteur de charge", "auto": "usb",
     "procedure": "Bouger légèrement le câble branché : la charge et la connexion ne doivent pas se couper. Inspecter le connecteur (poussière, oxydation)."},
    {"id": "cellular", "label": "Réseau mobile", "auto": "cellular",
     "procedure": "Insérer une SIM de test : réseau détecté, appel sortant, données mobiles."},
    {"id": "stability", "label": "Stabilité générale", "auto": "stability",
     "procedure": "Utiliser le téléphone 10 min (appareil photo, vidéo, navigation) : aucun redémarrage ni blocage."},
]


def automated_checks(outputs, batt):
    """Résultats automatiques Android (lecture seule). Retourne {test_id: {result, detail}}."""
    r = {}
    if batt and batt.get("level") is not None:
        r["battery_charge"] = {"result": "info", "detail": "Charge %s %%, statut : %s, alimentation : %s" % (
            batt["level"], batt.get("status") or "?", batt.get("plugged") or "?")}
    cam = re.search(r"Number of camera devices:\s*(\d+)", outputs.get("camera", ""))
    if cam:
        n = int(cam.group(1))
        r["cameras"] = {"result": "ok" if n else "échec", "detail": "%d caméra(s) déclarée(s) par le système (fonctionnement optique à vérifier manuellement)" % n}
    wifi = outputs.get("wifi_on", "").strip()
    if wifi in ("0", "1", "2", "3"):
        r["wifi"] = {"result": "info", "detail": "Wi-Fi %s (réglage). Connexion à tester manuellement." % ("activé" if wifi != "0" else "désactivé")}
    bt = outputs.get("bluetooth_on", "").strip()
    if bt in ("0", "1"):
        r["bluetooth"] = {"result": "info", "detail": "Bluetooth %s. Appairage à tester manuellement." % ("activé" if bt == "1" else "désactivé")}
    sensors = outputs.get("sensors", "")
    if sensors:
        names = set(re.findall(r"^0x[0-9a-f]+\)\s*([^|]+?)\s*\|", sensors, re.M))
        r["sensors"] = {"result": "info", "detail": "%d capteur(s) déclaré(s) par le système" % len(names) if names else "Liste des capteurs lue (format non reconnu)"}
    tel = outputs.get("telephony", "")
    vs = re.search(r"mVoiceRegState=(\d+)\((\w+)\)", tel) or re.search(r"mVoiceRegState\s*=\s*(\d+)", tel)
    if vs:
        code = vs.group(1)
        r["cellular"] = {"result": "ok" if code == "0" else "info",
                         "detail": "Enregistré sur le réseau" if code == "0" else "Non enregistré (pas de SIM, mode avion ou réception)"}
    r["usb_port"] = {"result": "échec" if outputs.get("adb_dropped") else "ok",
                     "detail": "Connexion ADB interrompue pendant le diagnostic" if outputs.get("adb_dropped")
                     else "Connexion ADB stable pendant le diagnostic (test mécanique à faire manuellement)"}
    return r


class Job(object):
    def __init__(self, device):
        self.id = uuid.uuid4().hex[:12]
        self.device = device
        self.steps = []
        self.progress = 0
        self.done = False
        self.error = None
        self.result = None

    def step(self, label, pct):
        self.steps.append(label)
        self.progress = pct

    def public(self):
        return {"id": self.id, "steps": self.steps, "progress": self.progress, "done": self.done,
                "error": self.error, "result": self.result}


def _run_android(job, keep_raw):
    d = job.device
    ad = android.AndroidAdapter(d["serial"])
    out, data = {}, {}

    def sh(key, cmd, label, pct, timeout=25):
        job.step(label, pct)
        r = ad.shell(cmd, timeout=timeout)
        if not r.ok and re.search(r"not found|no devices|offline|closed", r.err, re.I):
            out["adb_dropped"] = True
        out[key] = r.out if r.ok else ""
        return out[key]

    props = android.parse_getprop(sh("props", "getprop", "Lecture des propriétés système", 5))
    data["props"] = props
    ident = android.identity_from_props(props) if props else {}
    job.step("Lecture de la batterie", 15)
    raw = ad.battery(ident.get("brand") or d.get("brand"))
    batt = battery.assess(raw) if raw else None
    data["battery"] = batt
    db_text = sh("dropbox", "dumpsys dropbox", "Analyse des événements système (dropbox)", 30, timeout=40)
    data["dropbox"] = analysis.parse_dropbox(db_text)
    if data["dropbox"].get("SYSTEM_LAST_KMSG"):
        data["last_kmsg_text"] = sh("last_kmsg", "dumpsys dropbox --print SYSTEM_LAST_KMSG", "Lecture du journal noyau précédent", 40, timeout=40)[-200000:]
    cb = sh("crash", "logcat -b crash -d -t 400", "Lecture des plantages récents", 50)
    data["crash_buffer"] = analysis.parse_crash_buffer(cb)
    data["thermal"] = analysis.parse_thermal(sh("thermal", "dumpsys thermalservice", "Températures et événements thermiques", 60))
    data["storage_pct"] = analysis.parse_df_data(sh("df", "df /data", "Stockage", 65))
    data["mem_avail_pct"] = analysis.parse_meminfo(sh("mem", "cat /proc/meminfo", "Mémoire", 70))
    sh("camera", "dumpsys media.camera", "Caméras déclarées", 75)
    sh("wifi_on", "settings get global wifi_on", "Wi-Fi", 78)
    sh("bluetooth_on", "settings get global bluetooth_on", "Bluetooth", 80)
    sh("sensors", "dumpsys sensorservice", "Capteurs", 85)
    sh("telephony", "dumpsys telephony.registry", "Réseau mobile", 90)
    data["adb_dropped"] = bool(out.get("adb_dropped"))
    job.step("Analyse des indices", 95)
    ev = analysis.collect_evidence(data)
    res = {
        "identity": {k: v for k, v in ident.items()} or {k: d.get(k) for k in ("brand", "model", "os", "os_version")},
        "battery": batt, "evidence": ev, "hypotheses": analysis.analyze(ev),
        "auto_tests": automated_checks(out, batt),
        "boot": {"last_reason": props.get("sys.boot.reason") or props.get("ro.boot.bootreason"),
                 "uptime": None},
        "limits": ["Diagnostic en lecture seule via ADB : certaines données (journaux noyau, sysfs) peuvent être "
                   "inaccessibles sans privilèges root selon le fabricant.",
                   "Les tests matériels (écran, audio, boutons…) nécessitent une validation manuelle."],
    }
    if keep_raw:
        res["raw"] = {k: (v[-20000:] if isinstance(v, str) else v) for k, v in out.items() if k not in ("props",)}
    return res


def _run_ios(job, keep_raw):
    from .adapters import ios_pmd3
    d = job.device
    ap = apple.AppleAdapter(d["serial"])
    job.step("Lecture des informations et de la batterie", 20)
    state, hints, ident, details, raw = ap.interpret(ap.probe())
    if not ident:
        raise RuntimeError("; ".join(hints) or "Appareil Apple illisible")
    batt = battery.assess(raw) if raw else None
    ev = analysis.collect_evidence({"battery": batt})
    if raw and raw.get("shutdown_reason"):
        ev.append({"code": "battery_check", "text": "Le registre batterie signale une extinction anormale (code %s)" % raw["shutdown_reason"],
                   "source": "registre batterie", "severity": "alerte"})
    hyps = analysis.analyze(ev)
    panics_out, limits = [], []
    if ios_pmd3.AVAILABLE:
        job.step("Récupération des rapports de panic (redémarrages)", 50)
        files = ios_pmd3.panics(d["serial"])
        if files is None:
            limits.append("Rapports de panic non récupérés (appareil non autorisé ou service indisponible).")
        else:
            parsed = [dict(analysis.parse_ios_panic(f["name"], f["text"]), name=f["name"]) for f in files]
            pev, phyps = analysis.analyze_ios_panics(parsed)
            ev += pev
            hyps = sorted(hyps + phyps, key=lambda h: analysis.CONF_ORDER[h["confidence"]])
            panics_out = [{"name": p["name"], "date": p["date"], "summary": p["summary"], "missing_sensors": p["missing_sensors"]}
                          for p in parsed]
            if not parsed:
                ev.append({"code": "ios_no_panic", "text": "Aucun rapport de panic présent sur l'iPhone", "source": "rapports d'analyse iOS",
                           "severity": "info"})
    job.step("Analyse", 90)
    res = {
        "identity": ident, "battery": batt, "evidence": ev, "hypotheses": hyps, "auto_tests": {},
        "details": details, "panics": panics_out,
        "limits": limits + ["L'état des pièces (pièces d'origine) n'est pas exposé par USB : Réglages > Général > Informations.",
                            "Diagnostic officiel complémentaire : Apple Diagnostics / Self Service Repair.",
                            "Les tests matériels nécessitent une validation manuelle."],
    }
    if keep_raw:
        res["raw"] = {"panics": [f["text"][:20000] for f in (files or [])]} if ios_pmd3.AVAILABLE else {}
    return res


# ---------- Test de charge (courant sur la durée) ----------
def _sample_android(serial):
    ad = android.AndroidAdapter(serial)
    r = ad.shell("cat /sys/class/power_supply/battery/current_now", timeout=5)
    b = android.parse_kv(ad.shell("dumpsys battery", timeout=8).out)
    cur = None
    if r.ok and r.out.strip().lstrip("-").isdigit():
        v = int(r.out.strip())
        cur = v / 1000.0 if abs(v) > 20000 else float(v)  # µA -> mA selon fabricant
        if (b.get("status") == "2") and cur < 0:  # convention de signe variable : + = charge
            cur = -cur
    plugged = any(b.get(n + " powered") == "true" for n in ("ac", "usb", "wireless"))
    lvl = b.get("level")
    w = None
    try:  # « Max charging current » (µA) × « Max charging voltage » (µV) annoncés par le chargeur
        w = round(int(b.get("max charging current", 0)) * int(b.get("max charging voltage", 0)) / 1e12, 1) or None
    except ValueError:
        pass
    return {"current_ma": cur, "level": int(lvl) if lvl and lvl.isdigit() else None, "plugged": plugged, "adapter_w": w,
            "voltage_v": int(b["voltage"]) / 1000.0 if b.get("voltage", "").isdigit() else None}


def summarize_charge(samples):
    """Échantillons -> verdict prudent. Pure, testable."""
    cur = [s["current_ma"] for s in samples if s.get("current_ma") is not None]
    if not cur:
        return {"verdict": "indéterminé", "detail": "Courant non exposé par l'appareil.", "anomalies": []}
    avg, mn, mx = sum(cur) / len(cur), min(cur), max(cur)
    unplugged = sum(1 for s in samples if s.get("plugged") is False)
    drops = sum(1 for i in range(1, len(cur)) if cur[i - 1] > 300 and cur[i] < 50)
    an = []
    if unplugged and unplugged < len(samples):
        an.append("Alimentation perdue %d fois pendant le test : connecteur, câble ou chargeur." % unplugged)
    if drops:
        an.append("%d chute(s) brutale(s) du courant : faux contact probable (connecteur/câble)." % drops)
    lvl = next((s["level"] for s in reversed(samples) if s.get("level") is not None), None)
    if unplugged == len(samples):
        verdict, detail = "pas de charge", "Aucune alimentation détectée : chargeur, câble ou connecteur de charge."
    elif avg < 100:
        verdict, detail = "charge très faible", "Courant moyen %.0f mA." % avg
    elif avg < 500:
        verdict, detail = "charge lente", "Courant moyen %.0f mA (chargeur faible, câble ou connecteur)." % avg
    else:
        verdict, detail = "charge normale", "Courant moyen %.0f mA." % avg
    if lvl is not None and lvl >= 80:
        detail += " Batterie à %d %% : la charge ralentit normalement au-delà de 80 %%." % lvl
    return {"verdict": "à vérifier" if an and verdict == "charge normale" else verdict, "detail": detail,
            "avg_ma": round(avg), "min_ma": round(mn), "max_ma": round(mx), "anomalies": an}


def charge_causes(summary, samples, batt):
    """Pièce probable selon l'ampérage. Hypothèses classées, jamais une certitude. Pure, testable."""
    C, batt = [], batt or {}
    add = lambda part, conf, why: C.append({"part": part, "confidence": conf, "reason": why})
    v = summary["verdict"]
    adapter = max([s["adapter_w"] for s in samples if isinstance(s.get("adapter_w"), (int, float))] or [0])
    lvl = next((s["level"] for s in reversed(samples) if s.get("level") is not None), None)
    volts = [s["voltage_v"] for s in samples if s.get("voltage_v")]
    bad_batt = batt.get("health") in ("dégradé", "à contrôler") or any("incohérent" in a or "Tension" in a or "Température" in a
                                                                       for a in batt.get("anomalies") or [])
    if v == "indéterminé" and not summary.get("causes_extra"):
        return C
    if v == "pas de charge":
        add("chargeur / câble", "moyenne", "Aucune alimentation détectée : tester d'abord un chargeur et un câble connus bons.")
        add("connecteur de charge", "moyenne", "Si rien ne change avec un autre chargeur : connecteur encrassé, oxydé ou nappe défectueuse.")
        add("carte mère", "faible", "Si un connecteur neuf ne change rien : circuit de charge sur la carte mère (puce de gestion USB/charge).")
    elif summary.get("anomalies") and any("chute" in a or "perdue" in a for a in summary["anomalies"]):
        add("connecteur de charge", "élevée", "Coupures ou chutes brutales du courant : faux contact typique du connecteur (ou câble usé).")
        add("chargeur / câble", "moyenne", "Refaire le test avec un autre câble pour l'exclure.")
    elif v in ("charge très faible", "charge lente"):
        if lvl is not None and lvl >= 80:
            add("aucune panne", "moyenne", "Batterie au-dessus de 80 % : le téléphone réduit volontairement le courant. Refaire le test sous 50 %.")
        elif bad_batt:
            add("batterie", "moyenne", "Courant faible avec une batterie usée ou des mesures anormales : la batterie limite la charge.")
            add("connecteur de charge", "faible", "À contrôler si une batterie neuve ne change rien.")
        else:
            if adapter >= 10:
                add("connecteur de charge", "moyenne", "Chargeur de %s W détecté mais courant faible : connecteur encrassé/oxydé ou nappe." % adapter)
                add("carte mère", "faible", "Si connecteur neuf sans effet : circuit de charge sur la carte mère.")
            else:
                add("chargeur / câble", "moyenne", "Chargeur faible ou non identifié : refaire le test avec un chargeur rapide connu bon.")
                add("connecteur de charge", "faible", "Si le courant reste faible avec un bon chargeur.")
    else:  # charge normale
        if bad_batt:
            add("batterie", "moyenne", "La charge fonctionne, mais la batterie est usée ou ses mesures sont anormales.")
        else:
            add("aucune panne", "moyenne", "Courant de charge normal : connecteur, câble et circuit de charge fonctionnent.")
    if summary.get("causes_extra"):
        C.insert(0, {"part": "batterie", "confidence": "moyenne", "reason": "Redémarrage/déconnexion pendant la charge : batterie qui s'effondre sous l'appel de courant (cas le plus fréquent)."})
        C.insert(1, {"part": "carte mère", "confidence": "faible", "reason": "Si une batterie testée ne change rien : circuit d'alimentation ou de charge sur la carte mère."})
    if volts and min(volts) < 3.4 and v != "pas de charge":
        add("batterie", "moyenne", "Tension batterie basse (%.2f V) pendant la charge : cellule fatiguée ou défectueuse." % min(volts))
    return C


def start_charge_test(device, duration=20, interval=2):
    from .adapters import ios_pmd3
    job = Job(device)

    def work():
        try:
            if device.get("state") != RECOGNIZED:
                raise RuntimeError("Appareil non reconnu ou non autorisé.")
            sampler = (lambda: ios_pmd3.sample(device["serial"])) if device["transport"] == "lockdown" else \
                (lambda: _sample_android(device["serial"]))
            samples, t0 = [], time.time()
            lost = 0
            while time.time() - t0 < duration:
                s = sampler() or {}
                lost = 0 if s else lost + 1
                if lost >= 2:  # 2 mesures perdues d'affilée : déconnexion ou redémarrage
                    samples.append({"t": round(time.time() - t0, 1), "lost": True})
                    break
                s["t"] = round(time.time() - t0, 1)
                samples.append(s)
                job.step("Mesure du courant… %s mA" % (s.get("current_ma") if s.get("current_ma") is not None else "?"),
                         min(99, int((time.time() - t0) * 100 / duration)))
                time.sleep(interval)
            summary = summarize_charge([x for x in samples if not x.get("lost")])
            if any(x.get("lost") for x in samples):
                summary["anomalies"].insert(0, "Le téléphone s'est déconnecté ou a redémarré pendant le test (après %s s)." % samples[-1]["t"])
                summary["causes_extra"] = True
            summary["causes"] = charge_causes(summary, samples, device.get("battery"))
            ad = [x["adapter_w"] for x in samples if isinstance(x.get("adapter_w"), (int, float))]
            summary["adapter_w"] = max(ad) if ad else None
            job.result = {"samples": samples, "summary": summary}
            job.step("Terminé", 100)
        except Exception as e:
            job.error = str(e)
        finally:
            job.done = True

    threading.Thread(target=work, daemon=True, name="charge-" + job.id).start()
    return job


def start(device, keep_raw=False, on_done=None):
    job = Job(device)

    def work():
        try:
            if device.get("state") != RECOGNIZED:
                raise RuntimeError("Appareil non reconnu ou non autorisé : %s" % "; ".join(device.get("hints") or []))
            if device["transport"] == "adb":
                job.result = _run_android(job, keep_raw)
            elif device["transport"] == "lockdown":
                job.result = _run_ios(job, keep_raw)
            else:
                raise RuntimeError("Aucune interface de diagnostic disponible pour cet appareil.")
            job.result["started"] = time.time()
            job.step("Terminé", 100)
            if on_done:
                on_done(job)
        except Exception as e:
            job.error = str(e)
        finally:
            job.done = True

    threading.Thread(target=work, daemon=True, name="diag-" + job.id).start()
    return job
