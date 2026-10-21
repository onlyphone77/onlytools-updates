"""iPhone/iPad via pymobiledevice3 (GPL-3.0) — lecture seule, AUCUNE demande « Se fier » déclenchée.

- Appareil jamais autorisé : lecture des valeurs publiques de lockdown (modèle, iOS…).
- Appareil déjà autorisé sur cet ordinateur (Finder/iTunes/3uTools/…) : l'enregistrement
  d'appairage existant est réutilisé automatiquement -> IMEI, batterie complète, etc.
"""
import asyncio

try:
    from pymobiledevice3 import usbmux
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.diagnostics import DiagnosticsService
    AVAILABLE = True
except Exception:  # dépendance optionnelle
    AVAILABLE = False

TIMEOUT = 8

INFO_KEYS = ("ProductType", "ProductVersion", "BuildVersion", "DeviceClass", "HardwareModel", "ModelNumber",
             "RegionInfo", "SerialNumber", "InternationalMobileEquipmentIdentity",
             "InternationalMobileEquipmentIdentity2", "MobileEquipmentIdentifier", "ActivationState",
             "BasebandVersion", "PasswordProtected", "DeviceColor", "DeviceEnclosureColor", "WiFiAddress",
             "BluetoothAddress", "UniqueChipID", "CPUArchitecture", "PhoneNumber_hidden")


async def _list():
    return [d.serial for d in await usbmux.list_devices() if getattr(d, "connection_type", "USB") == "USB"]


async def _probe(udid, want_battery):
    res = {"udid": udid, "paired": False, "info": {}, "battery_domain": {}, "ioreg": {}, "disk": {}, "error": None}
    ld = await asyncio.wait_for(create_using_usbmux(serial=udid, autopair=False, connection_type="USB"), TIMEOUT)
    try:
        res["paired"] = bool(ld.paired)
        res["info"] = {k: ld.all_values.get(k) for k in INFO_KEYS if ld.all_values.get(k) is not None}
        if res["paired"] and want_battery:
            try:
                res["battery_domain"] = await asyncio.wait_for(ld.get_value(domain="com.apple.mobile.battery"), TIMEOUT) or {}
            except Exception:
                pass
            try:
                res["disk"] = await asyncio.wait_for(ld.get_value(domain="com.apple.disk_usage"), TIMEOUT) or {}
            except Exception:
                pass
            try:
                diag = DiagnosticsService(ld)
                try:
                    res["ioreg"] = await asyncio.wait_for(diag.get_battery(), TIMEOUT) or {}
                finally:
                    try:
                        await diag.close()
                    except Exception:
                        pass
            except Exception as e:
                res["ioreg_error"] = str(e)[:200]
    finally:
        try:
            await ld.close()
        except Exception:
            pass
    return res


def list_udids():
    if not AVAILABLE:
        return None
    try:
        return asyncio.run(asyncio.wait_for(_list(), TIMEOUT))
    except Exception:
        return []  # usbmuxd absent / pilote Apple manquant


def probe(udid, want_battery=True):
    try:
        return asyncio.run(_probe(udid, want_battery))
    except Exception as e:
        return {"udid": udid, "paired": False, "info": {}, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _jsonable(v):
    if isinstance(v, (bytes, bytearray)):
        return None
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items() if not isinstance(x, (bytes, bytearray))}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


# ---------- Autorisation (« Se fier ») à la demande du réparateur ----------
async def _pair(udid, timeout):
    ld = await asyncio.wait_for(create_using_usbmux(serial=udid, autopair=False, connection_type="USB"), TIMEOUT)
    try:
        if ld.paired:
            return {"ok": True, "message": "Déjà autorisé."}
        await ld.pair(timeout=timeout)
        return {"ok": True, "message": "Autorisation acceptée sur l'iPhone."}
    finally:
        try:
            await ld.close()
        except Exception:
            pass


def request_pair(udid, timeout=90):
    """Affiche la demande « Se fier » sur l'iPhone et attend la réponse (déverrouillage requis)."""
    try:
        return asyncio.run(_pair(udid, timeout))
    except Exception as e:
        n = type(e).__name__
        msg = {"UserDeniedPairingError": "Refusé sur l'iPhone (« Ne pas se fier »). Débranchez/rebranchez pour réessayer.",
               "PairingDialogResponsePendingError": "Pas de réponse sur l'iPhone dans le délai.",
               "PasswordRequiredError": "Déverrouillez l'iPhone (code) puis relancez la demande."}.get(n, "%s: %s" % (n, str(e)[:150]))
        return {"ok": False, "message": msg}


# ---------- Rapports de panic (redémarrages spontanés) ----------
async def _panics(udid, limit):
    from pymobiledevice3.services.crash_reports import CrashReportsManager
    ld = await asyncio.wait_for(create_using_usbmux(serial=udid, autopair=False, connection_type="USB"), TIMEOUT)
    out = []
    try:
        if not ld.paired:
            return None
        m = CrashReportsManager(ld)
        try:
            try:  # déplace les rapports vers la zone lisible (même opération que la synchro Finder/iTunes)
                await asyncio.wait_for(m.flush(), 20)
            except Exception:
                pass
            names = await asyncio.wait_for(m.ls("/", depth=2), 30)
            panics = sorted([n for n in names if "panic" in n.lower() and n.endswith(".ips")], reverse=True)[:limit]
            for n in panics:
                try:
                    data = await asyncio.wait_for(m.afc.get_file_contents(n), 20)
                    out.append({"name": n.rsplit("/", 1)[-1], "text": data[:2000000].decode("utf-8", "replace")})
                except Exception:
                    continue
        finally:
            try:
                await m.aclose()
            except Exception:
                pass
    finally:
        try:
            await ld.close()
        except Exception:
            pass
    return out


def panics(udid, limit=15):
    try:
        return asyncio.run(_panics(udid, limit))
    except Exception:
        return None


# ---------- Échantillon batterie léger (test de charge) ----------
async def _sample(udid):
    ld = await asyncio.wait_for(create_using_usbmux(serial=udid, autopair=False, connection_type="USB"), TIMEOUT)
    try:
        if not ld.paired:
            return None
        diag = DiagnosticsService(ld)
        try:
            io = await asyncio.wait_for(diag.get_battery(), TIMEOUT) or {}
        finally:
            try:
                await diag.close()
            except Exception:
                pass
        g = lambda k: io.get(k) if isinstance(io.get(k), (int, float)) and not isinstance(io.get(k), bool) else None
        return {"current_ma": g("InstantAmperage") if g("InstantAmperage") is not None else g("Amperage"),
                "level": g("CurrentCapacity"), "voltage_v": (g("Voltage") or 0) / 1000.0 or None,
                "plugged": bool(io.get("ExternalConnected")),
                "adapter_w": (io.get("AdapterDetails") or {}).get("Watts") if isinstance(io.get("AdapterDetails"), dict) else None}
    finally:
        try:
            await ld.close()
        except Exception:
            pass


def sample(udid):
    try:
        return asyncio.run(_sample(udid))
    except Exception:
        return None
