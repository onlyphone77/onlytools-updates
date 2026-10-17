"""Processus isolé pour les opérations iOS qui MODIFIENT l'appareil (recovery, flash).

Lancé par flash.py ; écrit une ligne JSON par événement sur stdout :
  {"type": "progress"|"log"|"done"|"error", ...}
Usage :
  python -m onlyphone.flash_runner detect
  python -m onlyphone.flash_runner enter-recovery <udid>
  python -m onlyphone.flash_runner exit-recovery [ecid]
  python -m onlyphone.flash_runner restore <ipsw> <update|erase> [udid|ecid:<n>]
"""
import ast
import asyncio
import os
import time
import json
import logging
import re
import sys
import warnings

warnings.filterwarnings("ignore")


_STATE = {"log": [], "error": None, "message": None, "pipe_ok": True}


def emit(**kw):
    t = kw.get("type")
    if t == "log":
        _STATE["log"] = (_STATE["log"] + [kw.get("message", "")])[-40:]
    elif t == "error":
        _STATE["error"] = kw.get("message")
    elif t == "done":
        _STATE["message"] = kw.get("message")
    if _STATE["pipe_ok"]:  # l'app peut avoir été fermée : on continue sans elle
        try:
            sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError, ValueError):
            _STATE["pipe_ok"] = False


def _finish_report(t0):
    """Met à jour le rapport de flash créé par l'app (variable OPD_FLASH_REPORT) : réussi / échec + causes."""
    rid = os.environ.get("OPD_FLASH_REPORT")
    if not rid:
        return
    try:
        from onlyphone import history
        from onlyphone.deadphone import explain_error
        rep = history.load(rid)
        err = _STATE["error"]
        rep["flash"].update({"state": "échec" if err else "réussi", "ok": not err, "error": err,
                             "explain": explain_error((err or "") + " " + " ".join(_STATE["log"][-30:])) if err else [],
                             "duration_s": round(time.time() - t0), "log_tail": _STATE["log"][-30:]})
        history.save(rep)
    except Exception:
        pass


def _patch_libusb():
    """pyusb doit utiliser la libusb fournie par le paquet libusb-package (Mac/Windows sans Homebrew)."""
    try:
        import libusb_package
        import usb.core
        backend = libusb_package.get_libusb1_backend()
        if backend is None:
            return
        import pymobiledevice3.irecv as irecv_mod
        orig = usb.core.find
        irecv_mod.find = lambda *a, **k: orig(*a, **dict(k, backend=k.get("backend") or backend))
    except Exception as e:
        emit(type="log", message="libusb indisponible : %s" % e)


class _ProgressLog(logging.Handler):
    """Transforme les journaux de pymobiledevice3 en progression lisible."""
    OPS = re.compile(r"progress-bar: (\{.*\})")

    def emit(self, record):
        msg = record.getMessage()
        m = self.OPS.search(msg)
        if m:
            try:
                d = ast.literal_eval(m.group(1))  # dict python émis par pmd3 (évaluation sûre)
                emit(type="progress", operation=str(d.get("Operation")), percent=d.get("Progress"))
                return
            except Exception:
                pass
        if record.levelno >= logging.INFO and len(msg) < 400:
            emit(type="log", message=msg)


async def _lockdown(udid):
    from pymobiledevice3.lockdown import create_using_usbmux
    return await create_using_usbmux(serial=udid, autopair=False, connection_type="USB")


def detect():
    from pymobiledevice3.irecv import IRecv
    try:
        ir = IRecv(timeout=3)
        emit(type="done", device={"mode": str(ir.mode.name if hasattr(ir.mode, "name") else ir.mode),
                                  "ecid": ir.ecid, "product_type": ir.product_type,
                                  "hardware_model": ir.hardware_model, "display_name": ir.display_name})
    except Exception as e:
        emit(type="error", message="Aucun appareil en mode Récupération/DFU détecté (%s)." % type(e).__name__)


async def enter_recovery(udid):
    ld = await _lockdown(udid)
    try:
        await ld.enter_recovery()
    finally:
        try:
            await ld.close()
        except Exception:
            pass
    emit(type="done", message="L'iPhone redémarre en mode Récupération.")


def exit_recovery(ecid):
    from pymobiledevice3.irecv import IRecv
    ir = IRecv(ecid=ecid, timeout=10)
    if not ir.mode.is_recovery:  # DFU : la ROM de démarrage n'accepte aucune commande de redémarrage
        emit(type="error", code="dfu", message="iPhone en mode DFU : la sortie se fait par redémarrage forcé (voir les instructions).")
        return
    ir.set_autoboot(True)
    ir.reboot()
    emit(type="done", message="Sortie du mode Récupération : l'iPhone redémarre normalement (30 à 60 s).")


async def restore(ipsw_path, behavior, target):
    from ipsw_parser.ipsw import IPSW
    from pymobiledevice3.irecv import IRecv
    from pymobiledevice3.restore.base_restore import Behavior
    from pymobiledevice3.restore.device import Device
    from pymobiledevice3.restore.restore import Restore
    if target and target.startswith("ecid:"):
        device = Device(irecv=IRecv(ecid=int(target[5:]), timeout=30))
    elif target:
        device = Device(lockdown=await _lockdown(target))
    else:
        device = Device(irecv=IRecv(timeout=30))
    emit(type="log", message="Appareil prêt, préparation du firmware…")
    ipsw = IPSW.create_from_path(ipsw_path)
    await Restore(ipsw, device, behavior=Behavior.Erase if behavior == "erase" else Behavior.Update).update()
    emit(type="done", message="Flash terminé. L'iPhone redémarre.")


def cfg_list():
    """Appareils vus par Apple Configurator (DFU, Récupération, normal) : ECID, modèle."""
    import subprocess
    from onlyphone.tools import find_tool
    exe = find_tool("cfgutil")
    if not exe:
        emit(type="error", message="Apple Configurator non installé.")
        return
    out = subprocess.run([exe, "--format", "JSON", "list"], capture_output=True, text=True, timeout=30).stdout
    emit(type="done", devices=parse_cfg_list(out))


def parse_cfg_list(text):
    """Sortie JSON de `cfgutil list` -> [{ecid, type, name, udid}]. Pure, testable."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return []
    out = []
    for key, d in (data.get("Output") or {}).items():
        if isinstance(d, dict):
            ecid = d.get("ECID") or key
            out.append({"ecid": str(ecid), "type": d.get("deviceType"), "name": d.get("name"), "udid": d.get("UDID"),
                        "location": d.get("locationID")})
    return out


def cfg_restore(op, ecid, ipsw):
    """cfgutil restore (efface) ou revive (réinstalle en gardant les données si possible), ciblé par ECID."""
    import subprocess
    from onlyphone.tools import find_tool
    exe = find_tool("cfgutil")
    if not exe:
        emit(type="error", message="Apple Configurator non installé.")
        return
    cmd = [exe, "-e", ecid, "restore" if op == "cfg-restore" else "revive"]
    if ipsw and op == "cfg-restore":  # « revive » n'accepte pas de firmware personnalisé
        cmd += ["-I", ipsw]
    emit(type="log", message="Apple Configurator : %s…" % ("restauration" if op == "cfg-restore" else "ranimation"))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    last = ""
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        last = line
        m = re.search(r"(\d{1,3})(?:\.\d+)?\s*%", line)
        if m:
            emit(type="progress", operation=line[:80], percent=int(m.group(1)))
        else:
            emit(type="log", message=line[:300])
    p.wait()
    if p.returncode:
        useful = [l for l in _STATE["log"] if re.search(r"error|Code: \d+", l, re.I)]
        emit(type="error", message="Apple Configurator a échoué : %s" % (" ".join(useful[-2:]) or last)[:400])
    else:
        emit(type="done", message="Terminé : l'iPhone redémarre.")


def main(argv):
    try:
        import signal
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass
    t0 = time.time()
    try:
        return _main(argv)
    finally:
        _finish_report(t0)


def _main(argv):
    _patch_libusb()
    h = _ProgressLog()
    lg = logging.getLogger("pymobiledevice3")
    lg.setLevel(logging.DEBUG)
    lg.addHandler(h)
    cmd = argv[0] if argv else ""
    try:
        if cmd == "detect":
            detect()
        elif cmd == "enter-recovery":
            asyncio.run(enter_recovery(argv[1]))
        elif cmd == "exit-recovery":
            exit_recovery(int(argv[1]) if len(argv) > 1 and argv[1] else None)
        elif cmd == "cfg-list":
            cfg_list()
        elif cmd in ("cfg-restore", "cfg-revive"):
            cfg_restore(cmd, argv[1], argv[2] if len(argv) > 2 else None)
        elif cmd == "restore":
            asyncio.run(restore(argv[1], argv[2], argv[3] if len(argv) > 3 else None))
        else:
            emit(type="error", message="commande inconnue")
            return 2
    except Exception as e:
        emit(type="error", message="%s : %s" % (type(e).__name__, str(e)[:500]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
