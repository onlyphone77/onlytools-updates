"""Lancement des opérations iOS modifiant l'appareil dans un processus séparé (flash_runner).

Garde-fous : une seule opération à la fois, phrase de confirmation vérifiée côté serveur,
journal conservé dans l'historique.
"""
import json
import os
import subprocess
import sys
import threading
import time

from . import history
from .diagnostic import Job

_lock = threading.Lock()
current = {"job": None}

OP_LABELS = {"restore-update": "Flash en conservant les données", "restore-erase": "Flash complet (effacement)",
             "enter-recovery": "Entrée en mode Récupération", "exit-recovery": "Sortie du mode Récupération",
             "detect": "Détection Récupération/DFU", "cfg-list": "Recherche (Apple Configurator)",
             "cfg-restore": "Restauration complète (Apple Configurator)", "cfg-revive": "Ranimation (Apple Configurator)"}


def confirm_phrase(op, model):
    """Mot que le réparateur doit taper (même mot pour les deux modes). Pure, testable."""
    return "FLASH" if op in ("restore-update", "restore-erase", "cfg-restore", "cfg-revive") else None


def busy():
    j = current["job"]
    return bool(j and not j.done)


def run(op, args, device_label="", on_done=None):
    """Démarre flash_runner ; renvoie un Job (progress, steps, result/error)."""
    with _lock:
        if busy():
            raise RuntimeError("Une opération de flash est déjà en cours.")
        job = Job({"label": device_label})
        current["job"] = job
    job.log = []
    job.step(OP_LABELS.get(op, op) + " — démarrage…", 1)
    rid = None
    if op.startswith("restore") or op in ("cfg-restore", "cfg-revive"):
        fw = args[0] if op.startswith("restore") else (args[1] if len(args) > 1 else "dernier iOS signé (Apple)")
        rid = history.save({"kind": "flash", "device": {"model": device_label}, "result": None,
                            "flash": {"operation": OP_LABELS[op], "firmware": os.path.basename(fw), "state": "en cours",
                                      "ok": None, "started": time.time()}})["id"]
    job.report_id = rid

    def work():
        cmd = [sys.executable, "-W", "ignore", "-m", "onlyphone.flash_runner", op.replace("restore-update", "restore")
               .replace("restore-erase", "restore")] + args
        t0 = time.time()
        env = dict(os.environ)
        if rid:
            env["OPD_FLASH_REPORT"] = rid
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
                                 start_new_session=(sys.platform != "win32"),  # survit à la fermeture de l'app
                                 creationflags=(0x08000000 | 0x00000200) if sys.platform == "win32" else 0)
            ops_seen = []
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "progress":
                    opn = ev.get("operation") or "?"
                    if opn not in ops_seen:
                        ops_seen.append(opn)
                    pct = ev.get("percent")
                    # progression globale approximative : étapes vues + % de l'étape en cours
                    glob = min(99, 5 + len(ops_seen) * 6 + (int(pct) * 0.3 if isinstance(pct, (int, float)) else 0))
                    job.step("%s %s%%" % (opn, pct if pct is not None else ""), int(glob))
                elif t == "log":
                    job.log.append(ev.get("message", ""))
                    job.log = job.log[-200:]
                    job.step(ev.get("message", "")[:160], job.progress)
                elif t == "done":
                    job.result = {"message": ev.get("message"), "device": ev.get("device")}
                elif t == "error":
                    job.error = ev.get("message")
            p.wait()
            if p.returncode and not job.error:
                job.error = "Échec (code %s)." % p.returncode
            if job.error and (op.startswith("restore") or op.startswith("cfg-r")):
                from .deadphone import explain_error
                job.result = dict(job.result or {}, explain=explain_error(job.error + " " + " ".join(job.log[-30:])))
            if not job.error:
                job.step(job.result and job.result.get("message") or "Terminé", 100)
        except Exception as e:
            job.error = str(e)
        finally:
            job.done = True
            if rid:  # le runner a normalement mis à jour le rapport ; sinon : interrompu
                try:
                    rep = history.load(rid)
                    if rep["flash"].get("state") == "en cours":
                        rep["flash"].update({"state": "interrompu", "ok": False, "error": job.error or "Opération interrompue",
                                             "duration_s": round(time.time() - t0), "log_tail": job.log[-30:]})
                        history.save(rep)
                except (OSError, ValueError, KeyError):
                    pass
            if on_done:
                on_done(job)

    threading.Thread(target=work, daemon=True, name="flash").start()
    return job
