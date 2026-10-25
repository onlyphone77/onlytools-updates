"""Bibliothèque de firmwares iOS (IPSW) : liste, statut de signature, téléchargement vérifié.

Liste : API publique ipsw.me (statut « signé » = installable, décidé par Apple).
Fichiers : téléchargés UNIQUEMENT depuis les serveurs Apple (*.apple.com), avec contrôle d'intégrité.
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from urllib.parse import urlparse

from .history import data_dir

API = "https://api.ipsw.me/v4/device/%s?type=ipsw"
UA = {"User-Agent": "OnlyPhoneDiagnostic/0.4", "Accept": "application/json"}
_cache = {}
downloads = {}  # url -> état


def fw_dir():
    d = os.path.join(os.path.dirname(data_dir()), "firmwares")
    os.makedirs(d, exist_ok=True)
    return d


def valid_product_type(pt):
    return bool(re.match(r"^(iPhone|iPad|iPod)\d{1,2},\d{1,2}$", pt or ""))


def apple_url(url):
    u = urlparse(url or "")
    h = (u.hostname or "").lower()
    official = any(h == d or h.endswith("." + d) for d in ("apple.com", "cdn-apple.com"))
    return u.scheme == "https" and official and u.path.endswith(".ipsw")


def parse_firmwares(data):
    """Réponse ipsw.me -> liste triée (pure, testable)."""
    out = []
    for f in (data or {}).get("firmwares", []):
        if not apple_url(f.get("url")):
            continue
        out.append({"version": f.get("version"), "build": f.get("buildid"), "signed": str(f.get("signed")).lower() == "true",
                    "size": f.get("filesize"), "date": (f.get("releasedate") or "")[:10], "url": f["url"],
                    "sha1": f.get("sha1sum"), "sha256": f.get("sha256sum"), "file": os.path.basename(urlparse(f["url"]).path)})
    return sorted(out, key=lambda x: (x["date"], x["version"] or ""), reverse=True)


def list_firmwares(product_type):
    if not valid_product_type(product_type):
        raise ValueError("Modèle invalide")
    c = _cache.get(product_type)
    if c and time.time() - c[0] < 1800:
        return c[1]
    req = urllib.request.Request(API % product_type, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        fws = parse_firmwares(json.loads(r.read().decode("utf-8")))
    _cache[product_type] = (time.time(), fws)
    return fws


def ipsw_matches(filename, product_type):
    """'iPhone11,8,iPhone12,1_18.0_22A3354_Restore.ipsw' couvre iPhone11,8 et iPhone12,1. Pure, testable."""
    ids = re.findall(r"(?:iPhone|iPad|iPod)\d+,\d+", (filename or "").split("_")[0])
    return product_type in ids


def library():
    out = []
    for f in sorted(os.listdir(fw_dir())):
        p = os.path.join(fw_dir(), f)
        if f.endswith(".ipsw") and os.path.isfile(p):
            out.append({"file": f, "size": os.path.getsize(p), "path": p})
    return out


def import_local(src):
    """Ajoute un .ipsw de l'ordinateur à la bibliothèque (lien si même disque, sinon copie en arrière-plan)."""
    import shutil
    import threading
    import zipfile
    src = os.path.realpath(os.path.expanduser(src or ""))
    if not src.lower().endswith(".ipsw") or not os.path.isfile(src):
        raise ValueError("Choisissez un fichier .ipsw")
    try:
        with zipfile.ZipFile(src) as z:
            if "BuildManifest.plist" not in z.namelist():
                raise ValueError("Ce fichier n'est pas un firmware Apple valide")
    except zipfile.BadZipFile:
        raise ValueError("Fichier .ipsw illisible ou incomplet")
    dst = os.path.join(fw_dir(), os.path.basename(src))
    if os.path.exists(dst):
        return {"ok": True, "message": "Déjà dans la bibliothèque"}
    try:
        os.link(src, dst)
        return {"ok": True, "message": "Ajouté à la bibliothèque"}
    except OSError:
        def copy():
            tmp = dst + ".part"
            try:
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
            except OSError:
                if os.path.exists(tmp):
                    os.remove(tmp)
        threading.Thread(target=copy, daemon=True).start()
        return {"ok": True, "message": "Copie en cours (quelques minutes pour 8 Go)…"}


def local_path(filename):
    f = os.path.basename(filename or "")
    p = os.path.join(fw_dir(), f)
    if not f.endswith(".ipsw") or not os.path.isfile(p):
        raise ValueError("Firmware introuvable dans la bibliothèque")
    return p


def _verify(path, fw):
    algo, expected = ("sha256", fw.get("sha256")) if fw.get("sha256") else ("sha1", fw.get("sha1"))
    if not expected:
        return True, "aucune empreinte publiée (non vérifié)"
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower(), algo


def start_download(fw):
    url = fw.get("url")
    if not apple_url(url):
        raise ValueError("Source non Apple refusée")
    st = downloads.get(url)
    if st and st["state"] in ("en cours", "vérification"):
        return st
    dest = os.path.join(fw_dir(), os.path.basename(urlparse(url).path))
    size = int(fw.get("size") or 0)
    if size and shutil.disk_usage(fw_dir()).free < size * 1.1:
        raise ValueError("Espace disque insuffisant (%.1f Go nécessaires)" % (size / 1e9))
    st = downloads[url] = {"url": url, "file": os.path.basename(dest), "state": "en cours", "done": 0, "total": size,
                           "speed": 0, "error": None, "cancel": False}

    def work():
        part = dest + ".part"
        try:
            if os.path.isfile(dest):
                st["state"], st["done"] = "terminé", st["total"]
                return
            have = os.path.getsize(part) if os.path.isfile(part) else 0
            req = urllib.request.Request(url, headers=dict(UA, Range="bytes=%d-" % have) if have else UA)
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if have and r.status == 206 else "wb") as f:
                if r.status != 206:
                    have = 0
                st["done"], t0, last = have, time.time(), have
                while True:
                    if st["cancel"]:
                        st["state"] = "annulé"
                        return
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    st["done"] += len(chunk)
                    dt = time.time() - t0
                    if dt >= 1:
                        st["speed"], t0, last = (st["done"] - last) / dt, time.time(), st["done"]
            st["state"] = "vérification"
            ok, algo = _verify(part, fw)
            if not ok:
                os.remove(part)
                raise ValueError("Fichier corrompu (empreinte %s différente) : supprimé, relancez le téléchargement." % algo)
            os.replace(part, dest)
            st["state"] = "terminé"
        except Exception as e:
            st["state"], st["error"] = "erreur", str(e)[:300]

    threading.Thread(target=work, daemon=True).start()
    return st


def public_downloads():
    return [{k: v for k, v in d.items() if k != "cancel"} for d in downloads.values()]
