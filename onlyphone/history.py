"""Historique local des rapports (fichiers JSON dans le dossier de données utilisateur).

Protection client : numéro de série masqué, aucun journal brut sans consentement explicite,
données stockées uniquement sur ce poste.
"""
import json
import os
import re
import sys
import threading
import time
import uuid

_lock = threading.Lock()


def data_dir():
    if os.environ.get("ONLYPHONE_DATA"):
        base = os.environ["ONLYPHONE_DATA"]
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/OnlyPhoneDiagnostic")
    elif os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OnlyPhoneDiagnostic")
    else:
        base = os.path.expanduser("~/.local/share/onlyphone-diagnostic")
    path = os.path.join(base, "reports")
    os.makedirs(path, exist_ok=True)
    return path


def _path(rid):
    if not re.match(r"^[0-9a-f]{12}$", rid or ""):
        raise ValueError("identifiant invalide")
    return os.path.join(data_dir(), rid + ".json")


def save(report):
    report.setdefault("id", uuid.uuid4().hex[:12])
    report.setdefault("created", time.time())
    with _lock:
        tmp = _path(report["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path(report["id"]))
    return report


def load(rid):
    with open(_path(rid), encoding="utf-8") as f:
        return json.load(f)


def search(q="", date=""):
    q, out = (q or "").lower().strip(), []
    for name in os.listdir(data_dir()):
        if not name.endswith(".json"):
            continue
        try:
            r = load(name[:-5])
        except (ValueError, OSError):
            continue
        dev, res = r.get("device", {}), r.get("result") or {}
        if r.get("kind") == "diagnostic" and res and not res.get("score"):  # rapports d'avant le bilan
            from . import insights
            res = dict(res, score=insights.score(res, insights.authenticity(res)))
        hay = " ".join(str(dev.get(k) or "") for k in ("brand", "model", "model_code", "serial_masked", "os")) + " " + (r.get("ticket") or "") \
            + " " + str((r.get("flash") or {}).get("error") or "") + " " + r.get("kind", "") + " " + str((r.get("operation") or {}).get("label") or "")
        day = time.strftime("%Y-%m-%d", time.localtime(r.get("created", 0)))
        if q and q not in hay.lower():
            continue
        if date and not day.startswith(date):
            continue
        out.append({"id": r["id"], "created": r.get("created"), "day": day, "kind": r.get("kind"),
                    "device": {k: dev.get(k) for k in ("brand", "model", "os", "os_version", "serial_masked")},
                    "ticket": r.get("ticket"), "hypotheses": len((r.get("result") or {}).get("hypotheses") or []),
                    "flash": {k: (r.get("flash") or {}).get(k) for k in ("operation", "state", "error")} if r.get("flash") else None,
                    "causes": [h["title"] for h in ((r.get("flash") or {}).get("explain") or [])][:2],
                    "score": (res.get("score") or {}).get("score"), "grade": (res.get("score") or {}).get("grade"),
                    "top": ((res.get("hypotheses") or [{}])[0]).get("title"),
                    "charge": (r.get("charge_test") or {}).get("verdict"),
                    "operation": {k: (r.get("operation") or {}).get(k) for k in ("label", "state", "error", "message")} if r.get("operation") else None})
    return sorted(out, key=lambda x: -(x["created"] or 0))
