"""Lancement des tâches iPhone (photos, applications, redémarrage, extinction) dans task_runner.

Chaque tâche est enregistrée dans l'historique dès son démarrage, tourne dans un processus qui survit
à la fermeture de l'app, et n'est jamais lancée pendant un flash.
"""
import asyncio
import os
import subprocess
import sys
import time
import uuid

from . import history, flash
from .task_runner import load, save, task_dir, aw

LABELS = {"photos": "Export des photos", "uninstall": "Suppression d'une application",
          "restart": "Redémarrage de l'iPhone", "shutdown": "Extinction de l'iPhone",
          "activate": "Activation (serveurs Apple)", "skip_setup": "Passage de l'assistant de configuration",
          "after_flash": "Après le flash : activation / configuration"}
SETUP = ("activate", "skip_setup")  # possibles sur l'écran « Bonjour », avant la demande « Se fier »


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def get(tid):
    t = load(tid)
    if t.get("state") in ("en attente", "en cours") and time.time() - t.get("created", 0) > 20 and not _alive(t.get("pid")):
        t.update(state="interrompu", error="Processus arrêté avant la fin.")
        save(t)
        try:
            rep = history.load(t["report_id"])
            rep["operation"].update(state="interrompu", ok=False, error=t["error"])
            history.save(rep)
        except (OSError, ValueError, KeyError):
            pass
    return t


def running_for(udid):
    for name in os.listdir(task_dir()):
        if name.endswith(".json"):
            try:
                t = get(name[:-5])
            except (OSError, ValueError):
                continue
            if t.get("udid") == udid and t.get("state") in ("en attente", "en cours"):
                return t
    return None


def start(kind, device, summary, **params):
    if kind not in LABELS:
        raise ValueError("Tâche inconnue")
    if device.get("transport") != "lockdown" or (device.get("state") != "reconnu" and kind not in SETUP):
        raise ValueError("iPhone non reconnu ou non autorisé.")
    if flash.busy():
        raise ValueError("Un flash est en cours : attendez la fin.")
    udid = device["serial"]
    cur = running_for(udid)
    if cur:
        raise ValueError("Une opération est déjà en cours sur cet iPhone : %s." % LABELS.get(cur["type"], cur["type"]))
    label = LABELS[kind] + (" — " + params["app_name"] if params.get("app_name") else "")
    rid = history.save({"kind": "operation", "device": summary, "result": None,
                        "operation": {"type": kind, "label": label, "state": "en cours", "ok": None, "started": time.time(),
                                      "dest": params.get("dest"), "app": params.get("app_name")}})["id"]
    t = dict(params, id=uuid.uuid4().hex[:12], type=kind, label=label, udid=udid, report_id=rid, state="en attente", created=time.time())
    save(t)
    _spawn(t["id"])
    return t


def start_after_flash(flash_report, summary, udid=None, ecid=None, do_activate=False, do_skip=False):
    """Lancé avec le flash : attend sa réussite puis le retour de l'iPhone (survit à la fermeture de l'app)."""
    if not (do_activate or do_skip):
        return None
    label = LABELS["after_flash"]
    rid = history.save({"kind": "operation", "device": summary, "result": None,
                        "operation": {"type": "after_flash", "label": label, "state": "en cours", "ok": None, "started": time.time()}})["id"]
    t = {"id": uuid.uuid4().hex[:12], "type": "after_flash", "label": label, "udid": udid, "ecid": ecid, "report_id": rid,
         "flash_report": flash_report, "do_activate": bool(do_activate), "do_skip": bool(do_skip), "state": "en attente", "created": time.time()}
    save(t)
    _spawn(t["id"])
    return t


def _spawn(tid):
    subprocess.Popen([sys.executable, "-W", "ignore", "-m", "onlyphone.task_runner", tid],
                     cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=(sys.platform != "win32"),  # survit à la fermeture de l'app
                     creationflags=(0x08000000 | 0x00000200) if sys.platform == "win32" else 0)


def cancel(tid):
    t = load(tid)
    t["cancel"] = True
    save(t)
    return t


def default_photo_dir(model):
    base = os.path.join(os.path.expanduser("~"), "Pictures", "Only Tools")
    return os.path.join(base, "%s %s" % (model or "iPhone", time.strftime("%Y-%m-%d %Hh%M")))


async def _apps(udid):
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    ld = await asyncio.wait_for(create_using_usbmux(serial=udid, autopair=False, connection_type="USB"), 10)
    try:
        if not ld.paired:
            raise ValueError("iPhone non autorisé sur cet ordinateur.")
        apps = await asyncio.wait_for(aw(InstallationProxyService(ld).get_apps(application_type="User", calculate_sizes=True)), 90)
    finally:
        try:
            await aw(ld.close())
        except Exception:
            pass
    out = []
    for bid, a in (apps or {}).items():
        out.append({"bundle": bid, "name": a.get("CFBundleDisplayName") or a.get("CFBundleName") or bid,
                    "version": a.get("CFBundleShortVersionString") or a.get("CFBundleVersion"),
                    "app_size": a.get("StaticDiskUsage"), "data_size": a.get("DynamicDiskUsage")})
    return sorted(out, key=lambda x: x["name"].lower())


def list_apps(udid):
    return asyncio.run(_apps(udid))
