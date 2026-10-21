"""Évaluation de la batterie à partir des seules données réellement lues.

Distingue strictement : niveau de charge actuel (%) ≠ santé (usure / capacité restante).
Entrée : dict brut produit par un adaptateur (clés optionnelles) :
  level, status, plugged, temp_c, voltage_v, cycles, full_mah, design_mah,
  vendor_health_pct (+ vendor_health_source), bms_health, platform, brand
"""

GOOD_THRESHOLD = 80  # seuil usuel (Apple / constructeurs) en dessous duquel la batterie est usée

BMS_LABELS = {
    "good": "Bon (indicateur BMS)", "overheat": "Surchauffe", "dead": "Batterie défaillante",
    "over_voltage": "Surtension", "failure": "Défaillance non spécifiée", "cold": "Trop froide",
    "unknown": "Inconnu",
}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def alternatives(platform, brand):
    b = (brand or "").lower()
    if platform == "ios":
        return ["Sur l'iPhone : Réglages > Batterie > État et charge (capacité maximale, et cycles sur iPhone 15 et ultérieurs).",
                "Diagnostic officiel Apple : Apple Diagnostics / Self Service Repair (getsupport.apple.com)."]
    alts = []
    if "samsung" in b:
        alts.append("Application Samsung Members > Diagnostics > Statut de la batterie.")
    if "google" in b or "pixel" in b:
        alts.append("Pixel (Android 14+) : Paramètres > À propos du téléphone > Informations sur la batterie (cycles).")
    if any(k in b for k in ("xiaomi", "redmi", "poco")):
        alts.append("Xiaomi : composer *#*#6485#*#* (valeurs constructeur de la batterie).")
    alts.append("Mesure physique : testeur de batterie / multimètre USB (capacité réelle en mAh lors d'une charge complète).")
    return alts


def assess(raw):
    raw = raw or {}
    platform, brand = raw.get("platform"), raw.get("brand")
    res = {"level": None, "status": raw.get("status"), "plugged": raw.get("plugged"),
           "temp_c": None, "voltage_v": None, "cycles": None, "capacity_pct": None,
           "capacity_source": None, "full_mah": None, "design_mah": None,
           "health": "inconnu", "health_basis": "", "bms_health": None,
           "anomalies": [], "unavailable": [], "alternatives": [], "current_ma": None}
    an, un = res["anomalies"], res["unavailable"]

    level = _num(raw.get("level"))
    if level is not None:
        if 0 <= level <= 100:
            res["level"] = int(round(level))
        else:
            an.append("Niveau de charge incohérent (%s %%) : jauge ou communication suspecte." % level)
    else:
        un.append("Niveau de charge : non fourni par l'interface.")

    t = _num(raw.get("temp_c"))
    if t is not None:
        if -20 <= t <= 80:
            res["temp_c"] = round(t, 1)
            if t >= 45:
                an.append("Température élevée (%.1f °C)." % t)
            elif t <= 0:
                an.append("Température très basse (%.1f °C) ou capteur suspect." % t)
        else:
            an.append("Température incohérente (%s °C) : capteur NTC ou nappe suspecte." % t)

    v = _num(raw.get("voltage_v"))
    if v is not None:
        if 2.5 <= v <= 5.0:
            res["voltage_v"] = round(v, 3)
            if v < 3.3:
                an.append("Tension basse (%.2f V)." % v)
            elif v > 4.5:
                an.append("Tension élevée (%.2f V)." % v)
        else:
            an.append("Tension incohérente (%s V)." % v)
        if res["level"] is not None and res["voltage_v"]:
            if res["level"] <= 5 and res["voltage_v"] >= 3.95:
                an.append("Niveau très bas mais tension élevée : jauge de batterie possiblement déréglée.")
            if res["level"] >= 95 and res["voltage_v"] <= 3.6:
                an.append("Niveau élevé mais tension basse : batterie ou jauge suspecte.")

    cur = _num(raw.get("current_ma"))
    if cur is not None and abs(cur) < 10000:
        res["current_ma"] = int(cur)
        if res["status"] == "en charge" and 0 <= cur < 100:
            an.append("En charge mais courant quasi nul (%d mA) : chargeur, câble ou connecteur à vérifier." % cur)

    c = _num(raw.get("cycles"))
    if c is not None:
        if 0 <= c < 10000:
            res["cycles"] = int(c)
        else:
            an.append("Nombre de cycles incohérent (%s)." % c)
    else:
        un.append("Nombre de cycles : non exposé par l'interface utilisée.")

    vh = _num(raw.get("vendor_health_pct"))
    full, design = _num(raw.get("full_mah")), _num(raw.get("design_mah"))
    if full and design and full > 0 and design > 0:
        res["full_mah"], res["design_mah"] = int(full), int(design)
        pct = full / design * 100.0
        if 20 <= pct <= 110:
            if not (raw.get("vendor_health_preferred") and vh is not None):
                res["capacity_pct"] = round(min(pct, 100.0), 1)
                res["capacity_source"] = "Capacité pleine / capacité nominale (%d / %d mAh) — estimation." % (full, design)
        else:
            an.append("Capacités incohérentes (%d / %d mAh) : données non exploitables." % (full, design))
    if res["capacity_pct"] is None and vh is not None:
        if 20 <= vh <= 110:
            res["capacity_pct"] = round(min(vh, 100.0), 1)
            res["capacity_source"] = raw.get("vendor_health_source") or "Valeur constructeur."
        else:
            an.append("Valeur de santé constructeur incohérente (%s %%)." % vh)
    if res["capacity_pct"] is None:
        un.append("Capacité maximale / santé en % : non exposée par l'interface utilisée.")

    if res["cycles"] == 0 and res["level"] is not None and res["level"] <= 1:
        an.append("Données batterie aberrantes (0 cycle, %d %%) : la batterie ne communique probablement pas avec la carte." % res["level"])
    bms = raw.get("bms_health")
    if bms:
        res["bms_health"] = BMS_LABELS.get(bms, bms)

    # Verdict santé
    if bms in ("dead", "over_voltage", "failure"):
        res["health"] = "à contrôler"
        res["health_basis"] = "Le système de gestion de batterie signale : %s." % res["bms_health"]
    elif bms in ("overheat", "cold"):
        res["health"] = "à contrôler"
        res["health_basis"] = "Le système signale une température anormale (%s)." % res["bms_health"]
    elif res["capacity_pct"] is not None:
        if res["capacity_pct"] >= GOOD_THRESHOLD:
            res["health"] = "bon"
        else:
            res["health"] = "dégradé"
        res["health_basis"] = "Capacité restante %.0f %% (seuil d'usure : %d %%)." % (res["capacity_pct"], GOOD_THRESHOLD)
    elif res["cycles"] is not None and res["cycles"] >= 800:
        res["health"] = "à contrôler"
        res["health_basis"] = "%d cycles, capacité non disponible." % res["cycles"]
    else:
        res["health"] = "inconnu"
        res["health_basis"] = ("Aucune donnée d'usure accessible. " +
                               ("L'indicateur BMS « Good » ne mesure pas l'usure." if bms == "good" else ""))
    if any("incohérent" in a for a in an) and res["health"] == "bon":
        res["health"] = "à contrôler"
        res["health_basis"] += " Données incohérentes détectées."
    if res["health"] in ("inconnu", "à contrôler", "dégradé") or un:
        res["alternatives"] = alternatives(platform, brand)
    return res
