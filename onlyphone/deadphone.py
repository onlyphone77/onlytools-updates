"""iPhone qui ne démarre pas : traduction des codes d'erreur de restauration + assistant ampèremètre.

Pistes issues des recommandations Apple et des retours d'atelier courants : jamais une certitude.
"""
import re

C = "câble / port USB"
_T = lambda *checks: list(checks)
CABLE = _T("Changer de câble (Apple d'origine) et de port USB, éviter les hubs.", "Refaire le flash depuis un autre ordinateur.")

# code Finder/iTunes -> (cause, catégorie, confiance, vérifications)
RESTORE_CODES = {
    9: ("Coupure USB pendant le flash, ou défaut NAND / processeur", "matériel", "moyenne",
        CABLE + ["Si l'erreur persiste en DFU : mémoire NAND ou processeur (carte mère)."]),
    4005: ("Communication interrompue : câble/port, ou NAND / processeur", "matériel", "moyenne",
           CABLE + ["Persistant en DFU : NAND ou processeur."]),
    4013: ("iPhone déconnecté pendant le flash : câble/port, NAND, modem ou processeur", "matériel", "moyenne",
           CABLE + ["Flasher en mode DFU.", "Persistant : NAND, modem (baseband) ou processeur — diagnostic carte mère."]),
    4014: ("Échec au redémarrage pendant le flash : NAND, alimentation ou carte mère", "matériel", "moyenne",
           CABLE + ["Tester avec une batterie connue bonne.", "Persistant : NAND / carte mère."]),
    2009: ("Coupure USB ou alimentation instable", "matériel", "moyenne", CABLE + ["Batterie chargée ou batterie de test."]),
    14: ("Fichier firmware corrompu, ou câble/port", C, "moyenne",
         ["Supprimer et retélécharger le firmware.", "Changer de câble et de port."]),
    40: ("Défaut de mémoire NAND", "matériel", "moyenne", ["Flasher en DFU ; si persistant : NAND à reprogrammer/remplacer."]),
    53: ("Capteur biométrique (Touch ID / Face ID) non reconnu par la carte mère", "matériel", "élevée",
         ["Remonter le Touch ID / Face ID d'origine (appairé à la carte).", "Vérifier la nappe du bouton principal / caméra TrueDepth."]),
    56: ("Défaut NFC / Touch ID / nappe associée", "matériel", "moyenne", ["Contrôler nappes Touch ID et NFC, puis carte mère."]),
    1: ("Échec de mise à jour du modem (baseband)", "matériel", "moyenne", ["Réessayer en DFU ; si persistant : modem/carte mère."]),
    3: ("Problème de modem (baseband)", "matériel", "faible", ["Réessayer ; persistant : partie modem de la carte mère."]),
    6: ("Défaut de stockage (NAND)", "matériel", "moyenne", ["Flasher en DFU ; persistant : NAND."]),
    10: ("Firmware incompatible ou défaut carte mère", "matériel", "faible", ["Vérifier que le firmware correspond au modèle et est signé."]),
    3194: ("Refus de signature Apple (firmware non signé) ou accès aux serveurs Apple bloqué", "logiciel", "élevée",
           ["Utiliser un firmware SIGNÉ.", "Vérifier la connexion Internet / pare-feu / fichier hosts."]),
    3004: ("Accès aux serveurs Apple impossible", "logiciel", "moyenne", ["Vérifier la connexion Internet / pare-feu."]),
    3014: ("Accès aux serveurs Apple impossible", "logiciel", "moyenne", ["Vérifier la connexion Internet / pare-feu."]),
}

# messages d'erreur du moteur de flash -> code équivalent / explication
MESSAGE_RULES = [
    (r"Unexpected device state 'DFU' expected 'Recovery'", (
        "L'iPhone est retombé en DFU au lieu de redémarrer en Récupération pendant le flash (4014)", "matériel", "moyenne",
        ["Ne pas maintenir de bouton pendant le flash (relâcher dès que le mode DFU est détecté).",
         "Changer de câble (Apple d'origine) et de port, brancher directement sur le Mac (sans hub).",
         "Batterie chargée ou batterie de test connue bonne.",
         "Si l'erreur revient à chaque essai : NAND, processeur ou alimentation sur la carte mère."])),
    (r"no devices found|aucun appareil", ("iPhone non trouvé au moment du flash : il a quitté le mode DFU/Récupération, ou câble/port", C, "moyenne",
                                         ["Remettre l'iPhone en DFU puis relancer.", "Changer de câble (Apple d'origine) et de port USB."])),
    (r"disk failure|failed to mount filesystems", ("Défaut de stockage (NAND)", "matériel", "moyenne",
                                                   ["Flasher en DFU ; persistant : NAND à reprogrammer/remplacer."])),
    (r"SEP firmware", ("Échec du Secure Enclave (SEP) : firmware non signé ou défaut Face ID / Touch ID / carte mère", "matériel", "moyenne",
                       ["Vérifier que le firmware est signé.", "Persistant : Face ID / Touch ID ou carte mère."])),
    (r"FDR", ("Données d'usine (FDR) irrécupérables : carte mère ou pièces non appairées", "matériel", "moyenne",
              ["Remonter les pièces d'origine (écran, Face ID, batterie).", "Persistant : carte mère."])),
    (r"Baseband", ("Échec de mise à jour du modem (baseband)", "matériel", "moyenne", ["Réessayer en DFU ; persistant : modem/carte mère."])),
    (r"USBError|ConnectionAbort|ConnectionReset|Broken pipe|timed? ?out|NoDeviceConnected|déconnect", RESTORE_CODES[4013]),
    (r"not signed|TSS|signature|3194", RESTORE_CODES[3194]),
]


def _h(src, cause):
    title, cat, conf, checks = cause
    return {"title": title, "category": cat, "confidence": conf, "next_checks": checks, "source": src}


def explain_error(text):
    """Code numérique (Finder/iTunes) ou message d'erreur du flash -> hypothèses. Pure, testable."""
    t = re.sub(r"\(code -?\d+\)|code de sortie -?\d+", " ", str(text or "")).strip()  # code de sortie du programme ≠ code Apple
    out = []
    # code seul (saisi par le réparateur) ou précédé de « error / erreur / Code: »
    cands = [t] if re.fullmatch(r"\d{1,4}", t) else re.findall(r"(?:error|erreur|code)\s*:?\s*(\d{1,4})(?![\d.])", t, re.I)
    for m in cands:
        if int(m) in RESTORE_CODES:
            out.append(_h("erreur %s" % m, RESTORE_CODES[int(m)]))
            break
    for pat, cause in MESSAGE_RULES:
        if re.search(pat, t, re.I) and not any(o["title"] == cause[0] for o in out):
            out.append(_h("message du flash", cause))
    return out


# Assistant ampèremètre : motif observé au branchement (iPhone éteint, ampèremètre USB ou alimentation de labo)
AMMETER = [
    {"id": "zero", "label": "0 mA — aucune consommation",
     "causes": [("Batterie totalement déchargée ou morte", "alimentation", "moyenne", "Laisser charger 30 min, puis tester avec une batterie connue bonne."),
                ("Connecteur de charge / nappe défectueux", "matériel", "moyenne", "Tester avec une nappe de charge neuve ou connue bonne."),
                ("Circuit de charge sur la carte mère (puce USB/charge)", "matériel", "faible", "Si batterie et nappe bonnes : diagnostic carte mère.")]},
    {"id": "low", "label": "Faible et fixe (moins de 50 mA), ne démarre pas",
     "causes": [("Circuit d'alimentation (PMU) ou processeur ne démarre pas", "matériel", "moyenne", "Mesures sur la carte (lignes d'alimentation principales)."),
                ("Mémoire NAND", "matériel", "faible", "Tenter la détection DFU puis un flash.")]},
    {"id": "loop", "label": "Monte puis retombe en boucle",
     "causes": [("Batterie faible ou défectueuse", "alimentation", "moyenne", "Tester avec une batterie connue bonne."),
                ("Redémarrage en boucle logiciel ou capteur manquant (panic)", "logiciel", "moyenne", "Lire les panics si l'iPhone démarre ; sinon flash en DFU."),
                ("Mémoire NAND", "matériel", "faible", "Persistant après flash : NAND.")]},
    {"id": "high", "label": "Élevée et fixe (plus de 1 A) ou chauffe",
     "causes": [("Court-circuit sur la carte mère (condensateur sur la ligne d'alimentation principale)", "matériel", "élevée",
                 "Localiser le point chaud (caméra thermique ou colophane), batterie débranchée, sur alimentation de labo.")]},
    {"id": "normal_black", "label": "Normale (0,3 à 1 A) mais écran noir",
     "causes": [("Écran, nappe ou rétroéclairage — l'iPhone est peut-être allumé", "matériel", "moyenne",
                 "Vérifier vibration/son/détection USB, tester avec un écran connu bon."),
                ("Circuit d'affichage sur la carte mère", "matériel", "faible", "Si un écran connu bon reste noir.")]},
    {"id": "logo_loop", "label": "Symptôme : boucle sur la pomme, non détecté par l'ordinateur",
     "causes": [("iOS endommagé", "logiciel", "moyenne", "Forcer le mode Récupération (Vol+, Vol−, maintenir latéral jusqu'au logo câble) puis « Ranimer », sinon « Restaurer »."),
                ("Batterie qui s'effondre au démarrage", "alimentation", "moyenne", "Tester avec une batterie connue bonne ou une alimentation de labo."),
                ("Pièce défectueuse qui fait planter le démarrage (nappe de charge, Face ID, écran)", "matériel", "moyenne",
                 "iPhone sur batterie de test, débrancher les nappes une par une jusqu'à l'arrêt de la boucle."),
                ("Mémoire NAND ou carte mère", "matériel", "faible", "Si la restauration échoue (4013/4014/9) ou si la boucle persiste tout débranché.")]},
    {"id": "charge_only", "label": "Consomme en charge mais s'éteint dès qu'on débranche",
     "causes": [("Batterie hors service ou connecteur batterie", "alimentation", "élevée", "Remplacer la batterie / vérifier son connecteur.")]},
]


def ammeter(pattern_id):
    p = next((x for x in AMMETER if x["id"] == pattern_id), None)
    if not p:
        return []
    return [{"title": t, "category": c, "confidence": conf, "next_checks": [chk], "source": "ampèremètre : " + p["label"]}
            for t, c, conf, chk in p["causes"]]


# ---------- Redémarrage en boucle : analyse de la chronologie USB ----------
def analyze_bootloop(timeline, now, window=1800):
    """timeline : [{t, kind: connexion|déconnexion, mode: recovery|dfu|normal}] d'un même iPhone.
    Retourne None si pas de boucle, sinon {cycles, résumé, hypothèses}. Pure, testable."""
    ev = sorted((e for e in timeline if now - e["t"] <= window), key=lambda e: e["t"])
    sessions, opened = [], {}
    for e in ev:
        if e["kind"] == "connexion":
            opened[e["mode"]] = e["t"]
        elif e["mode"] in opened:
            sessions.append({"mode": e["mode"], "dur": e["t"] - opened.pop(e["mode"])})
    cycles = sum(1 for e in ev if e["kind"] == "connexion")
    if cycles < 2:
        return None
    modes = {s["mode"] for s in sessions} | {e["mode"] for e in ev}
    durs = sorted(s["dur"] for s in sessions) or [0]
    med = durs[len(durs) // 2]
    H = []
    add = lambda t, c, conf, checks: H.append({"title": t, "category": c, "confidence": conf, "next_checks": checks, "source": "chronologie USB"})
    if "normal" not in modes:
        summary = ("%d apparitions en mode %s en %d min, jamais démarré sous iOS (durée médiane %.0f s)."
                   % (cycles, "Récupération" if "recovery" in modes else "DFU", max(1, (ev[-1]["t"] - ev[0]["t"]) // 60), med))
        add("iOS ne démarre pas (système endommagé ou mise à jour interrompue)", "logiciel", "moyenne",
            ["Essayer « Sortir du mode Récupération » une fois.", "Sinon « Ranimer » (garde les données si possible), puis « Restaurer »."])
        add("Mémoire NAND défaillante", "matériel", "moyenne" if med < 20 else "faible",
            ["Si la ranimation/restauration échoue (4013/4014/9) : NAND ou processeur."])
        if med < 15:
            add("Coupure d'alimentation au démarrage (batterie ou circuit PMU)", "alimentation", "moyenne",
                ["Tester avec une batterie connue bonne ou une alimentation de labo (lire la consommation).",
                 "Vérifier le connecteur batterie."])
    else:
        nd = sorted(s["dur"] for s in sessions if s["mode"] == "normal") or [med]
        nmed = nd[len(nd) // 2]
        summary = "%d redémarrages en %d min ; iOS tient environ %.0f s avant de redémarrer." % (
            cycles, max(1, (ev[-1]["t"] - ev[0]["t"]) // 60), nmed)
        if 120 <= nmed <= 240:
            add("Capteur manquant / watchdog (redémarrage toutes les ~3 min)", "matériel", "élevée",
                ["Lire les panics (carte « Panics ») : le capteur cité indique la nappe.",
                 "Souvent : nappe du connecteur de charge, nappe power/volume, capteur de proximité."])
        elif nmed < 60:
            add("Alimentation instable (batterie / PMU) ou NAND", "alimentation", "moyenne",
                ["Tester avec une batterie connue bonne.", "Lire les panics si l'iPhone reste assez longtemps allumé."])
        else:
            add("Plantage système (logiciel, application, ou panne matérielle intermittente)", "indéterminé", "faible",
                ["Lire les panics.", "Mettre à jour / ranimer iOS."])
    if any(s["dur"] < 3 for s in sessions):
        add("Connexion USB qui coupe (câble ou connecteur de charge)", "matériel", "faible",
            ["Changer de câble et de port ; inspecter le connecteur du téléphone."])
    return {"cycles": cycles, "summary": summary, "hypotheses": H, "updated": now}
