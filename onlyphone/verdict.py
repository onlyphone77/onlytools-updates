"""Verdict unique : combine tous les indices (panics, batterie, boucle, test de charge, flash)
en UNE pièce prioritaire, une action, puis l'étape suivante si ça ne règle pas.

Chaque indice donne des points à des pièces ; le pourcentage = part des points (ce n'est pas une certitude).
"""
import re

ACTIONS = {
    "Batterie": "Remplacer par une batterie d'origine connue bonne, puis rallumer.",
    "Connecteur / nappe batterie": "Nettoyer et contrôler le connecteur batterie (carte mère et nappe), broches et oxydation.",
    "Nappe du connecteur de charge": "Remplacer la nappe du connecteur de charge par une nappe testée.",
    "Nappe power / volume": "Remplacer la nappe power / volume.",
    "Face ID / Touch ID": "Débrancher Face ID / Touch ID et rallumer ; si stable, remplacer le module (d'origine, appairé).",
    "Capteurs (proximité / AOP)": "Débrancher la nappe des capteurs (proximité, écouteur) et rallumer.",
    "Mémoire NAND": "Ranimer / restaurer iOS ; si échec 4013/4014/9 : NAND à reprogrammer ou remplacer.",
    "Carte mère (alimentation / modem)": "Diagnostic carte mère : lignes d'alimentation, PMU, modem.",
    "iOS (logiciel)": "Onglet Flash iOS : « Ranimer » (garde les données), sinon « Restaurer ».",
    "Câble / port USB": "Changer de câble Apple et de port, sans hub, puis refaire l'opération.",
    "Écran / nappe écran": "Tester avec un écran connu bon.",
}

# (motif du panic, {pièce: poids})
PANIC_WEIGHTS = [
    (r"SMC BSC failure|BSC failure|\bTB0[TV]\b", {"Batterie": 6, "Connecteur / nappe batterie": 3}),
    (r"missing sensor\(s\):[^\n\"]*\bTG0[BV]\b", {"Batterie": 5, "Connecteur / nappe batterie": 3}),
    (r"missing sensor\(s\):[^\n\"]*\b(mic1|mic2|prs0)\b", {"Nappe du connecteur de charge": 6, "Nappe power / volume": 2}),
    (r"missing sensor\(s\):", {"Nappe du connecteur de charge": 3, "Capteurs (proximité / AOP)": 2}),
    (r"no successful checkins from thermalmonitord", {"Nappe du connecteur de charge": 3, "Batterie": 1}),
    (r"SEP Panic|SEPOS", {"Face ID / Touch ID": 4, "Carte mère (alimentation / modem)": 2}),
    (r"AOP PANIC", {"Capteurs (proximité / AOP)": 4}),
    (r"ANS2|NVMe|AppleANS|nand", {"Mémoire NAND": 6}),
    (r"baseband|BBPanic", {"Carte mère (alimentation / modem)": 5}),
    (r"SMC PANIC|PMU|PMGR", {"Batterie": 2, "Carte mère (alimentation / modem)": 3}),
    (r"i2c\d*::_checkTransferStatus", {"Écran / nappe écran": 2, "Nappe du connecteur de charge": 2}),
]

CHARGE_PARTS = {"batterie": "Batterie", "connecteur de charge": "Nappe du connecteur de charge",
                "carte mère": "Carte mère (alimentation / modem)", "chargeur / câble": "Câble / port USB"}
CONF_W = {"élevée": 5, "moyenne": 3, "faible": 1}


def compute(device=None, panics=None, bootloop=None, charge=None, flash=None):
    """Retourne {part, pct, why, action, next_part, next_action, steps} ou None si aucun indice."""
    score, why = {}, {}

    def add(part, pts, reason):
        score[part] = score.get(part, 0) + pts
        why.setdefault(part, [])
        if reason not in why[part]:
            why[part].append(reason)

    for p in panics or []:  # panics parsés : {text, summary, missing_sensors}
        txt = p.get("text") or p.get("summary") or ""
        for pat, w in PANIC_WEIGHTS:
            if re.search(pat, txt, re.I):
                for part, pts in w.items():
                    add(part, pts, "Panic : " + (p.get("missing_sensors") and "capteur manquant " + p["missing_sensors"] or (p.get("summary") or "")[:90]))
                break
    b = (device or {}).get("battery") or {}
    if any("communique" in a or "Tension incohérente" in a for a in b.get("anomalies") or []):
        add("Batterie", 4, "Données batterie aberrantes (%s)" % "; ".join(a.split(" :")[0] for a in b["anomalies"][:2]))
        add("Connecteur / nappe batterie", 2, "La batterie ne communique pas avec la carte")
    if b.get("health") == "dégradé":
        add("Batterie", 4, "Batterie usée (%s %%)" % b.get("capacity_pct"))
    if bootloop:
        if "jamais démarré" in bootloop.get("summary", ""):
            add("iOS (logiciel)", 3, bootloop["summary"])
            add("Mémoire NAND", 2, "Ne démarre jamais iOS")
        for h in bootloop.get("hypotheses", []):
            if "3 min" in h["title"]:
                add("Nappe du connecteur de charge", 3, "Redémarre toutes les ~3 minutes")
    for c in (charge or {}).get("causes") or []:
        part = CHARGE_PARTS.get(c.get("part"))
        if part:
            add(part, CONF_W.get(c.get("confidence"), 1), "Test de charge : " + c.get("reason", "")[:90])
    for h in (flash or {}).get("explain") or []:
        t = h["title"]
        part = ("Mémoire NAND" if "NAND" in t else "Face ID / Touch ID" if ("Face ID" in t or "Touch ID" in t)
                else "Câble / port USB" if "câble" in t.lower() else "Carte mère (alimentation / modem)" if "modem" in t else None)
        if part:
            add(part, CONF_W.get(h["confidence"], 1), "Flash : " + t[:90])
    if not score:
        return None
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    total = float(sum(score.values()))
    part, pts = ranked[0]
    res = {"part": part, "pct": int(round(pts * 100 / total)), "why": why[part][:4], "action": ACTIONS.get(part, ""),
           "steps": [{"part": p, "pct": int(round(s * 100 / total)), "action": ACTIONS.get(p, "")} for p, s in ranked[:3]]}
    if len(ranked) > 1:
        res["next_part"], res["next_action"] = ranked[1][0], ACTIONS.get(ranked[1][0], "")
    return res
