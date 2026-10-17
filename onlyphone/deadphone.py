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
    {"id": "charge_only", "label": "Consomme en charge mais s'éteint dès qu'on débranche",
     "causes": [("Batterie hors service ou connecteur batterie", "alimentation", "élevée", "Remplacer la batterie / vérifier son connecteur.")]},
]


def ammeter(pattern_id):
    p = next((x for x in AMMETER if x["id"] == pattern_id), None)
    if not p:
        return []
    return [{"title": t, "category": c, "confidence": conf, "next_checks": [chk], "source": "ampèremètre : " + p["label"]}
            for t, c, conf, chk in p["causes"]]
