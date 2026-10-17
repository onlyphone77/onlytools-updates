"""Mise à jour automatique d'Only Tools.

Le serveur (adresse dans update_config.json) publie :
  version.json  {"version": "0.7.0", "url": "https://…/onlytools-0.7.0.zip", "sha256": "…", "signature": "…", "notes": "…"}
La signature Ed25519 du ZIP est vérifiée avec la clé publique de update_config.json :
seules les mises à jour signées par l'éditeur (clé privée sur son Mac) sont installées.
"""
import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile

from . import __version__

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "update_config.json")
_state = {"checked_at": 0, "info": None, "error": None, "installed": None}


def config():
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def verify(data, sha256, signature, pubkey_hex):
    """Empreinte + signature Ed25519. Pure, testable."""
    if hashlib.sha256(data).hexdigest() != (sha256 or "").lower():
        return False, "empreinte différente (fichier corrompu)"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(bytes.fromhex(signature or ""), data)
    except Exception:
        return False, "signature invalide (mise à jour non officielle refusée)"
    return True, ""


def check(force=False):
    cfg = config()
    if not cfg.get("url"):
        _state.update(error="Serveur de mise à jour non configuré.", info=None)
        return public()
    if not force and time.time() - _state["checked_at"] < 3600:
        return public()
    try:
        req = urllib.request.Request(cfg["url"].rstrip("/") + "/version.json", headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=15) as r:
            info = json.loads(r.read().decode("utf-8"))
        _state.update(info=info, error=None, checked_at=time.time())
    except Exception as e:
        _state.update(error="Vérification impossible : %s" % str(e)[:120], checked_at=time.time())
    return public()


def public():
    info = _state["info"] or {}
    return {"current": __version__, "latest": info.get("version"), "notes": info.get("notes"),
            "available": bool(info) and vtuple(info.get("version")) > vtuple(__version__),
            "error": _state["error"], "installed": _state["installed"], "configured": bool(config().get("url"))}


def install():
    """Télécharge, vérifie et remplace le dossier du logiciel (ancienne version gardée jusqu'au succès)."""
    info, cfg = _state["info"] or {}, config()
    if not (info and vtuple(info.get("version")) > vtuple(__version__)):
        raise ValueError("Aucune mise à jour disponible")
    if not str(info.get("url", "")).startswith("https://"):
        raise ValueError("Adresse de téléchargement non sécurisée")
    with urllib.request.urlopen(info["url"], timeout=120) as r:
        data = r.read()
    ok, why = verify(data, info.get("sha256"), info.get("signature"), cfg.get("public_key", ""))
    if not ok:
        raise ValueError(why)
    z = zipfile.ZipFile(io.BytesIO(data))
    if "onlyphone/__init__.py" not in z.namelist() or any(n.startswith("/") or ".." in n for n in z.namelist()):
        raise ValueError("Archive invalide")
    parent = os.path.dirname(HERE)
    tmp = tempfile.mkdtemp(prefix="opt-up-", dir=parent)
    try:
        z.extractall(tmp)
        shutil.copy2(CFG, os.path.join(tmp, "onlyphone", "update_config.json"))  # garde l'adresse et la clé
        old = HERE + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.rename(HERE, old)
        try:
            os.rename(os.path.join(tmp, "onlyphone"), HERE)
        except OSError:
            os.rename(old, HERE)  # retour arrière
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _state["installed"] = info["version"]
    return public()
