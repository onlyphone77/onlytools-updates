"""Bilan complémentaire d'un diagnostic (pur, testable) :
- authenticité des pièces : INDICES seulement (une pièce copiée peut imiter les valeurs d'origine) ;
- pannes connues du modèle, signalées quand les indices du diagnostic les recoupent ;
- score global 0-100 sur les données lues (les tests manuels ne sont pas comptés).
"""
import re

# Capacité nominale Apple (mAh, fiches réglementaires) par ProductType
DESIGN_MAH = {
    "iPhone9,1": 1960, "iPhone9,3": 1960, "iPhone9,2": 2900, "iPhone9,4": 2900,
    "iPhone10,1": 1821, "iPhone10,4": 1821, "iPhone10,2": 2691, "iPhone10,5": 2691, "iPhone10,3": 2716, "iPhone10,6": 2716,
    "iPhone11,2": 2658, "iPhone11,4": 3174, "iPhone11,6": 3174, "iPhone11,8": 2942,
    "iPhone12,1": 3110, "iPhone12,3": 3046, "iPhone12,5": 3969, "iPhone12,8": 1821,
    "iPhone13,1": 2227, "iPhone13,2": 2815, "iPhone13,3": 2815, "iPhone13,4": 3687,
    "iPhone14,4": 2406, "iPhone14,5": 3227, "iPhone14,2": 3095, "iPhone14,3": 4352, "iPhone14,6": 2018,
    "iPhone14,7": 3279, "iPhone14,8": 4325, "iPhone15,2": 3200, "iPhone15,3": 4323,
    "iPhone15,4": 3349, "iPhone15,5": 4383, "iPhone16,1": 3274, "iPhone16,2": 4422,
    "iPhone17,3": 3561, "iPhone17,4": 4674, "iPhone17,1": 3582, "iPhone17,2": 4685, "iPhone17,5": 3961,
}

NOT_USB = "Non exposé par USB : Réglages > Général > Informations, rubrique « Historique des pièces » (iOS 15.2 et +)."


def _text(res):
    """Tous les indices lisibles du diagnostic, pour les recoupements."""
    parts = [e.get("text", "") for e in res.get("evidence") or []]
    parts += [h.get("title", "") for h in res.get("hypotheses") or []]
    parts += [(p.get("summary") or "") + (" missing sensor(s): " + p["missing_sensors"] if p.get("missing_sensors") else "")
              for p in res.get("panics") or []]
    return "\n".join(parts)


def authenticity(res):
    """iPhone uniquement -> [{part, status, detail}] ; status : aucun indice / à vérifier / suspect / non vérifiable."""
    ident = res.get("identity") or {}
    if ident.get("platform") != "ios":
        return []
    out = []
    b = res.get("battery") or {}
    ref, design = DESIGN_MAH.get(ident.get("model_code") or ""), b.get("design_mah")
    if ref and design:
        dev = abs(design - ref) * 100.0 / ref
        if dev > 8:
            out.append({"part": "Batterie", "status": "suspect",
                        "detail": "Capacité nominale déclarée %d mAh au lieu de %d mAh (Apple) : batterie de remplacement non Apple probable." % (design, ref)})
        elif dev > 4:
            out.append({"part": "Batterie", "status": "à vérifier",
                        "detail": "Capacité nominale %d mAh, un peu éloignée des %d mAh Apple. Contrôler Réglages > Batterie." % (design, ref)})
        elif re.search(r"TB0[TV]|TG0[BV]|BSC failure", _text(res)):
            out.append({"part": "Batterie", "status": "à vérifier",
                        "detail": "Capteurs batterie illisibles par la carte (panics)%s : batterie non Apple, mal branchée ou nappe abîmée." % (
                            ", 0 cycle" if b.get("cycles") == 0 else "")})
        elif b.get("cycles") == 0:
            out.append({"part": "Batterie", "status": "à vérifier",
                        "detail": "0 cycle : batterie neuve ou remplacée. Vérifier « Pièce d'origine » dans Réglages > Batterie."})
        else:
            out.append({"part": "Batterie", "status": "aucun indice",
                        "detail": "Capacité nominale cohérente (%d mAh, Apple : %d mAh). Une copie peut imiter cette valeur." % (design, ref)})
    else:
        out.append({"part": "Batterie", "status": "non vérifiable",
                    "detail": "Capacité nominale non lue ou modèle non répertorié. " + NOT_USB})
    if re.search(r"Secure Enclave|SEP\b", _text(res)):
        out.append({"part": "Face ID / Touch ID", "status": "à vérifier",
                    "detail": "Panics du Secure Enclave : module remplacé, non appairé ou endommagé possible."})
    else:
        out.append({"part": "Face ID / Touch ID", "status": "non vérifiable", "detail": "Aucun panic lié. " + NOT_USB})
    for part in ("Écran", "Caméras"):
        out.append({"part": part, "status": "non vérifiable", "detail": NOT_USB})
    return out


# (ProductTypes concernés, titre, symptômes, contrôle, motif recoupant les indices ou None)
_X_TO_14 = tuple("iPhone%d," % n for n in (11, 12, 13, 14, 15))
KNOWN_FAULTS = [
    (("iPhone9,",), "Puce audio (« loop disease »)",
     "Haut-parleur grisé en appel, Dictaphone indisponible, démarrage très long.",
     "Tester Dictaphone et haut-parleur en appel ; réparation carte mère (puce audio).", r"audio"),
    (("iPhone9,",), "Modem : « Pas de service »",
     "Pas de réseau malgré une SIM valide, baseband absent dans Réglages > Informations.",
     "Vérifier la version du modem dans les infos appareil ; ancien programme de réparation Apple.", r"baseband|BBPanic|modem"),
    (("iPhone10,1", "iPhone10,4"), "Carte mère (ancien programme Apple 2018)",
     "Redémarrages inattendus, gel, ne s'allume plus.",
     "Vérifier l'éligibilité sur le site Apple avec le n° de série.", r"rapport\(s\) de panic|redémarr"),
    (("iPhone10,3", "iPhone10,6"), "Écran tactile qui ne répond plus (ancien programme Apple)",
     "Zones de l'écran qui ne réagissent pas ou touches fantômes.",
     "Tester avec un écran connu bon.", None),
    (("iPhone10,3", "iPhone10,6"), "Face ID indisponible après choc ou oxydation",
     "« Face ID n'est pas disponible », projecteur de points en défaut.",
     "Débrancher Face ID et rallumer ; contrôler la nappe et le projecteur.", r"Secure Enclave|SEP\b|Face ID"),
    (_X_TO_14, "Nappe du connecteur de charge : capteurs manquants",
     "Panics « missing sensor » (mic1, prs0…) et redémarrage toutes les ~3 minutes.",
     "Remplacer la nappe du connecteur de charge par une nappe testée, puis observer 24 h.", r"missing sensor|capteur manquant|3 min|thermalmonitord"),
    (("iPhone12,1",), "Écran tactile (ancien programme Apple, fabrication fin 2019 – mi-2020)",
     "Écran qui ne répond plus au toucher.",
     "Tester avec un écran connu bon ; éligibilité Apple avec le n° de série.", None),
    (("iPhone13,2", "iPhone13,3"), "Écouteur interne sans son (ancien programme Apple 2021)",
     "Pas de son dans l'écouteur pendant les appels.",
     "Tester l'écouteur en appel ; remplacer le module écouteur.", None),
    (("iPhone16,1", "iPhone16,2", "iPhone15,4", "iPhone15,5"), "Surchauffe (premières versions d'iOS 17)",
     "Chauffe anormale sans charge lourde.",
     "Mettre à jour iOS (correctif à partir d'iOS 17.0.3), puis refaire le test.", r"therm|chauff|température"),
]


def known_faults(res):
    """-> [{title, symptoms, check, matched}] ; matched = les indices du diagnostic recoupent cette panne."""
    code = (res.get("identity") or {}).get("model_code") or ""
    txt = _text(res)
    out = []
    for prefixes, title, symptoms, check, pat in KNOWN_FAULTS:
        if any(code == p or (p.endswith(",") and code.startswith(p)) for p in prefixes):
            out.append({"title": title, "symptoms": symptoms, "check": check,
                        "matched": bool(pat and re.search(pat, txt, re.I))})
    out.sort(key=lambda f: not f["matched"])
    return out


CONF_PTS = {"élevée": 15, "moyenne": 8, "faible": 3}
GRADES = [(85, "A", "Très bon état"), (70, "B", "Bon état"), (50, "C", "État moyen"), (30, "D", "Mauvais état"), (0, "E", "Hors service ou pour pièces")]


def score(res, auth=None):
    """-> {score, grade, label, factors:[{label, pts}]} ; estimation sur les données lues uniquement."""
    factors = []
    b = res.get("battery") or {}
    cap = b.get("capacity_pct")
    if isinstance(cap, (int, float)):
        if cap < 80:
            factors.append(("Batterie usée (%d %%)" % cap, -20))
        elif cap < 85:
            factors.append(("Batterie fatiguée (%d %%)" % cap, -8))
    if b.get("anomalies"):
        factors.append(("Anomalies batterie (%d)" % len(b["anomalies"]), -10))
    hyp_pts = sum(CONF_PTS.get(h.get("confidence"), 0) for h in res.get("hypotheses") or [])
    if hyp_pts:
        factors.append(("Causes de panne détectées (%d)" % len(res["hypotheses"]), -min(40, hyp_pts)))
    n = len(res.get("panics") or [])
    if n:
        factors.append(("%d rapport(s) de panic" % n, -min(20, 3 * n)))
    sus = [a["part"] for a in auth or [] if a["status"] == "suspect"]
    if sus:
        factors.append(("Pièce(s) probablement non d'origine : " + ", ".join(sus), -10 * len(sus)))
    fails = [k for k, t in (res.get("auto_tests") or {}).items() if t.get("result") == "échec"]
    if fails:
        factors.append(("Tests automatiques en échec (%d)" % len(fails), -5 * len(fails)))
    s = max(0, min(100, 100 + sum(p for _, p in factors)))
    if s > 60 and (n >= 3 or any(h.get("confidence") == "élevée" for h in res.get("hypotheses") or [])):
        factors.append(("Panne avérée : note plafonnée à C", 60 - s))  # un téléphone qui plante n'est pas « en bon état »
        s = 60
    _, grade, label = next(g for g in GRADES if s >= g[0])
    return {"score": s, "grade": grade, "label": label, "factors": [{"label": l, "pts": p} for l, p in factors]}


def enrich(res):
    """Ajoute authenticity / known_faults / score au résultat d'un diagnostic (modifie et retourne res)."""
    res["authenticity"] = authenticity(res)
    res["known_faults"] = known_faults(res)
    res["score"] = score(res, res["authenticity"])
    return res
