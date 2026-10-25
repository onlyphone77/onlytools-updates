"""Processus isolé pour les opérations iPhone hors flash (export des photos, désinstallation d'app,
redémarrage, extinction). Survit à la fermeture de l'app : l'état est écrit dans <données>/tasks/<id>.json
et le rapport d'historique est mis à jour au début et à la fin.

  python -m onlyphone.task_runner <id>
"""
import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
import warnings

warnings.filterwarnings("ignore")

from . import history  # noqa: E402

MEDIA = (".heic", ".jpg", ".jpeg", ".png", ".gif", ".mov", ".mp4", ".dng", ".webp", ".avi", ".m4v")


def task_dir():
    d = os.path.join(os.path.dirname(history.data_dir()), "tasks")
    os.makedirs(d, exist_ok=True)
    return d


def task_path(tid):
    if not tid.isalnum():
        raise ValueError("identifiant invalide")
    return os.path.join(task_dir(), tid + ".json")


def load(tid):
    with open(task_path(tid), encoding="utf-8") as f:
        return json.load(f)


def save(t):
    tmp = task_path(t["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False)
    os.replace(tmp, task_path(t["id"]))


async def aw(x):
    return await x if inspect.isawaitable(x) else x


def _report(t, **kw):
    """Met à jour le rapport d'historique de la tâche (opération : réussi / échec / interrompu)."""
    try:
        rep = history.load(t["report_id"])
        rep["operation"].update(kw)
        history.save(rep)
    except (OSError, ValueError, KeyError):
        pass


def _cancelled(t):
    try:
        return bool(load(t["id"]).get("cancel"))
    except (OSError, ValueError):
        return False


async def _lockdown(udid):
    from pymobiledevice3.lockdown import create_using_usbmux
    ld = await create_using_usbmux(serial=udid, autopair=False, connection_type="USB")
    if not ld.paired:
        raise RuntimeError("iPhone non autorisé sur cet ordinateur (« Se fier »).")
    return ld


def _stamp(st):
    m = st.get("st_mtime") or st.get("st_birthtime")
    try:
        return m.strftime("%Y-%m-%d_%H%M%S") if hasattr(m, "strftime") else time.strftime("%Y-%m-%d_%H%M%S", time.localtime(float(m) / (1e9 if float(m) > 1e12 else 1)))
    except (TypeError, ValueError):
        return ""


async def photos(t):
    from pymobiledevice3.services.afc import AfcService
    ld = await _lockdown(t["udid"])
    afc = AfcService(ld)
    dest = t["dest"]
    os.makedirs(dest, exist_ok=True)
    files = []
    for d in sorted(await aw(afc.listdir("/DCIM"))):
        if d.startswith("."):
            continue
        try:
            names = await aw(afc.listdir("/DCIM/" + d))
        except Exception:
            continue
        files += ["/DCIM/%s/%s" % (d, n) for n in sorted(names) if n.lower().endswith(MEDIA)]
    t.update(total=len(files), done=0, bytes=0, message="%d fichiers trouvés" % len(files))
    save(t)
    jpg = t.get("jpg") and sys.platform == "darwin"
    for i, src in enumerate(files):
        if _cancelled(t):
            raise InterruptedError("Export annulé par le réparateur.")
        st = await aw(afc.stat(src))
        name = os.path.basename(src)
        if t.get("by_date"):
            s = _stamp(st)
            name = (s + "_" + name) if s else name
        out = os.path.join(dest, name)
        size = int(st.get("st_size") or 0)
        final = os.path.splitext(out)[0] + ".jpg" if jpg and out.lower().endswith(".heic") else out
        if not (os.path.isfile(final) and (final != out or os.path.getsize(out) == size)):  # reprise : déjà copié
            await aw(afc.pull(src, out, progress_bar=False)) if "progress_bar" in inspect.signature(afc.pull).parameters else await aw(afc.pull(src, out))
            if final != out:  # conversion HEIC -> JPG (copie sur le Mac uniquement, jamais sur l'iPhone)
                r = subprocess.run(["sips", "-s", "format", "jpeg", out, "--out", final], capture_output=True)
                if r.returncode == 0 and os.path.isfile(final):
                    os.remove(out)
        t.update(done=i + 1, bytes=t["bytes"] + size, message=os.path.basename(src))
        if i % 5 == 0 or i + 1 == len(files):
            save(t)
    await aw(afc.close()) if hasattr(afc, "close") else None
    return "%d fichiers exportés dans %s" % (len(files), dest)


async def uninstall(t):
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    ld = await _lockdown(t["udid"])
    ip = InstallationProxyService(ld)
    await aw(ip.uninstall(t["bundle"]))
    return "Application « %s » supprimée de l'iPhone." % (t.get("app_name") or t["bundle"])


async def power(t):
    from pymobiledevice3.services.diagnostics import DiagnosticsService
    ld = await _lockdown(t["udid"])
    d = DiagnosticsService(ld)
    await aw(d.restart() if t["type"] == "restart" else d.shutdown())
    return "Redémarrage demandé." if t["type"] == "restart" else "Extinction demandée."


ICLOUD_LOCK = ("Verrouillage d'activation iCloud : Apple refuse l'activation. Le client doit saisir son identifiant "
               "Apple sur l'iPhone (aucun contournement possible).")


async def _find(udid, ecid, wait):
    """Attend l'iPhone (après un flash il redémarre plusieurs fois) ; renvoie son UDID."""
    from pymobiledevice3 import usbmux
    from pymobiledevice3.lockdown import create_using_usbmux
    end = time.time() + wait
    while True:
        for d in await usbmux.list_devices():
            if udid and d.serial == udid:
                return d.serial
            if ecid:
                try:
                    ld = await create_using_usbmux(serial=d.serial, autopair=False, connection_type="USB")
                    same = str(ld.all_values.get("UniqueChipID")) == str(ecid)
                    await aw(ld.close())
                    if same:
                        return d.serial
                except Exception:
                    pass
        if time.time() > end:
            raise TimeoutError("iPhone non revenu après le flash (débranché ou bloqué au démarrage).")
        await asyncio.sleep(5)


async def activate(t):
    """Activation par les serveurs Apple (comme le Finder) ; refusée si l'iPhone est verrouillé par iCloud."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.mobile_activation import MobileActivationService
    udid = await _find(t.get("udid"), t.get("ecid"), t.get("wait", 30))
    ld = await create_using_usbmux(serial=udid, autopair=False, connection_type="USB")
    if ld.all_values.get("ActivationState") == "Activated":
        return "iPhone déjà activé."
    if not ld.paired:  # iPhone sur l'écran « Bonjour » : appairage sans demande « Se fier »
        await aw(ld.close())
        ld = await create_using_usbmux(serial=udid, autopair=True, connection_type="USB")
    t.update(message="Activation auprès des serveurs Apple…")
    save(t)
    try:
        await MobileActivationService(ld).activate(skip_apple_id_query=True)
    except Exception as e:
        if "lock" in str(e).lower() or "icloud" in str(e).lower() or "apple id" in str(e).lower():
            raise RuntimeError(ICLOUD_LOCK)
        raise
    return "iPhone activé (serveurs Apple)."


def skip_setup(t):
    """Apple Configurator : passe tous les écrans de l'assistant de configuration (iPhone fraîchement effacé)."""
    from .tools import find_tool
    exe = find_tool("cfgutil")
    if not exe:
        raise RuntimeError("Apple Configurator est nécessaire (Mac App Store, gratuit).")
    ecid = t.get("ecid")
    if not ecid:
        raise RuntimeError("ECID de l'iPhone inconnu.")
    t.update(message="Passage de l'assistant de configuration (Apple Configurator)…")
    save(t)
    r = subprocess.run([exe, "-e", "0x%X" % int(ecid), "prepare", "--skip-all", "--language", t.get("lang", "fr"),
                        "--locale", t.get("locale", "fr_FR")], capture_output=True, text=True, timeout=600)
    out = (r.stdout + r.stderr).strip()
    if r.returncode:
        if "activation lock" in out.lower() or "locked" in out.lower():
            raise RuntimeError(ICLOUD_LOCK)
        raise RuntimeError("Apple Configurator : " + (out.splitlines()[-1] if out else "échec (code %s)" % r.returncode))
    return "Assistant de configuration passé : l'iPhone arrive sur l'écran d'accueil."


async def _ecid(udid):
    from pymobiledevice3.lockdown import create_using_usbmux
    ld = await create_using_usbmux(serial=udid, autopair=False, connection_type="USB")
    try:
        return ld.all_values.get("UniqueChipID")
    finally:
        await aw(ld.close())


async def setup_steps(t):
    msgs = []
    if t.get("do_activate"):
        msgs.append(await activate(t))
    if t.get("do_skip"):
        udid = await _find(t.get("udid"), t.get("ecid"), t.get("wait", 30))
        t["ecid"] = t.get("ecid") or await _ecid(udid)
        msgs.append(skip_setup(t))
    return " ".join(msgs) or "Rien à faire."


async def after_flash(t):
    """Attend la fin du flash (rapport d'historique), puis le retour de l'iPhone, puis active / passe la configuration."""
    t.update(message="En attente de la fin du flash…")
    save(t)
    while True:
        if _cancelled(t):
            raise InterruptedError("Annulé par le réparateur.")
        try:
            st = (history.load(t["flash_report"]).get("flash") or {}).get("state")
        except (OSError, ValueError):
            st = None
        if st == "réussi":
            break
        if st in ("échec", "interrompu"):
            raise InterruptedError("Flash en échec : activation et configuration non lancées.")
        await asyncio.sleep(5)
    t.update(message="Flash réussi : attente du redémarrage de l'iPhone…", wait=900)
    save(t)
    return await setup_steps(t)


async def _skip(t):
    udid = await _find(t.get("udid"), t.get("ecid"), 30)
    t["ecid"] = t.get("ecid") or await _ecid(udid)
    return skip_setup(t)


RUN = {"photos": photos, "uninstall": uninstall, "restart": power, "shutdown": power,
       "activate": activate, "skip_setup": _skip, "after_flash": after_flash}


def main(tid):
    t = load(tid)
    t.update(state="en cours", pid=os.getpid())
    save(t)
    t0 = time.time()
    try:
        msg = asyncio.run(RUN[t["type"]](t))
        t.update(state="réussi", message=msg, progress=100)
        _report(t, state="réussi", ok=True, message=msg, duration_s=round(time.time() - t0))
    except InterruptedError as e:
        t.update(state="interrompu", error=str(e))
        _report(t, state="interrompu", ok=False, error=str(e), duration_s=round(time.time() - t0))
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, str(e)[:300])
        from .deadphone import explain_error
        t.update(state="échec", error=err)
        _report(t, state="échec", ok=False, error=err, causes=[h["title"] for h in explain_error(err)][:3],
                duration_s=round(time.time() - t0))
    finally:
        t["done_at"] = time.time()
        save(t)


if __name__ == "__main__":
    main(sys.argv[1])
