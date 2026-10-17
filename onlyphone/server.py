"""Serveur HTTP local (127.0.0.1 uniquement) : API JSON + interface web."""
import json
import re
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import __version__, diagnostic, history, firmware, flash, deadphone, updater
from .monitor import Monitor, mask

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
monitor = Monitor()
jobs = {}

DEVICE_FIELDS = ("brand", "manufacturer", "model", "model_code", "os", "os_version", "os_detail", "platform",
                 "state", "transport", "usb")


def device_summary(d):
    s = {k: d.get(k) for k in DEVICE_FIELDS}
    s["serial_masked"] = mask(d.get("serial"))
    s["details"] = {k: v for k, v in (d.get("details") or {}).items() if k not in ("wifi_mac", "bt_mac")}
    return s


_lf = {"t": 0, "v": None}


def last_flash():
    """Dernier flash des 6 dernières heures (pour l'afficher sur la fiche), mis en cache 3 s."""
    if time.time() - _lf["t"] < 3:
        return _lf["v"]
    v = None
    for h in history.search(""):
        if h.get("kind") == "flash" and time.time() - (h.get("created") or 0) < 6 * 3600:
            try:
                r = history.load(h["id"])
                v = {"id": r["id"], "model": r["device"].get("model"), **{k: r["flash"].get(k) for k in
                     ("operation", "firmware", "state", "error", "explain", "duration_s")}}
            except (OSError, ValueError, KeyError):
                pass
            break
    _lf.update(t=time.time(), v=v)
    return v


def imei_valid(imei):
    """15 chiffres + clé de Luhn. Pure, testable."""
    if not re.match(r"^\d{15}$", imei or ""):
        return False
    total = 0
    for i, ch in enumerate(imei[::-1]):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def save_report(kind, device, result=None, ticket="", consent_raw=False):
    if result and not consent_raw:
        result = {k: v for k, v in result.items() if k != "raw"}
    rep = {"kind": kind, "device": device_summary(device), "battery": (result or {}).get("battery") or device.get("battery"),
           "result": result, "manual_tests": {}, "ticket": (ticket or "")[:60], "consent_raw": bool(consent_raw),
           "app_version": __version__}
    bl = monitor.blacklist.get((device.get("details") or {}).get("imei") or device.get("manual_imei") or "")
    if bl:
        rep["blacklist"] = bl
    return history.save(rep)


class Handler(BaseHTTPRequestHandler):
    server_version = "OnlyPhoneDiagnostic"

    def log_message(self, *a):
        pass

    def _host_ok(self):  # protection contre le DNS rebinding
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("JSON attendu")
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(min(n, 1000000)) or b"{}")

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "hôte refusé"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/status":
                st = monitor.public()
                st["now"] = time.time()
                st["last_flash"] = last_flash()
                return self._json(st)
            if u.path == "/api/job":
                j = jobs.get(q.get("id"))
                return self._json(j.public() if j else {"error": "inconnu"}, 200 if j else 404)
            if u.path == "/api/history":
                return self._json(history.search(q.get("q", ""), q.get("date", "")))
            if u.path == "/api/report":
                return self._json(history.load(q.get("id", "")))
            if u.path == "/api/firmwares":
                return self._json(firmware.list_firmwares(q.get("pt", "")))
            if u.path == "/api/firmware/library":
                return self._json({"files": firmware.library(), "downloads": firmware.public_downloads(), "dir": firmware.fw_dir(),
                                   "flash": flash.current["job"].public() if flash.current["job"] else None})
            if u.path == "/api/update":
                return self._json(updater.check(force=q.get("force") == "1"))
            if u.path == "/api/deadphone":
                return self._json({"patterns": [{"id": a["id"], "label": a["label"]} for a in deadphone.AMMETER],
                                   "restore": deadphone.explain_error(q.get("code", "")) if q.get("code") else [],
                                   "ammeter": deadphone.ammeter(q.get("amp", ""))})
            if u.path == "/api/tests":
                return self._json(diagnostic.TESTS)
            return self._static(u.path)
        except FileNotFoundError:
            return self._json({"error": "Rapport introuvable"}, 404)
        except (ValueError, OSError) as e:
            return self._json({"error": str(e)}, 400)

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB) or not os.path.isfile(full):
            return self._json({"error": "introuvable"}, 404)
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(full)[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._host_ok():
            return self._json({"error": "hôte refusé"}, 403)
        try:
            b = self._body()
            path = urlparse(self.path).path
            if path == "/api/diagnostic":
                d = monitor.get(b.get("device", ""))
                if not d:
                    return self._json({"error": "Appareil déconnecté"}, 404)
                ticket, consent = b.get("ticket", ""), bool(b.get("consent_raw"))

                def done(job):
                    job.result["report_id"] = save_report("diagnostic", d, job.result, ticket, consent)["id"]
                job = diagnostic.start(d, keep_raw=consent, on_done=done)
                jobs[job.id] = job
                return self._json({"job": job.id})
            if path == "/api/pair":
                d = monitor.get(b.get("device", ""))
                if not d or d.get("transport") != "lockdown":
                    return self._json({"error": "iPhone non détecté"}, 404)
                from .adapters import ios_pmd3
                if not ios_pmd3.AVAILABLE:
                    return self._json({"error": "Module iPhone absent"}, 400)
                job = diagnostic.Job(d)

                def pair_work(job=job, d=d):
                    job.step("Demande « Se fier » affichée sur l'iPhone : déverrouillez-le et acceptez.", 10)
                    r = ios_pmd3.request_pair(d["serial"])
                    job.result = r
                    if r["ok"]:
                        monitor.refresh(d["key"])
                    else:
                        job.error = r["message"]
                    job.step(r["message"], 100)
                    job.done = True
                import threading
                threading.Thread(target=pair_work, daemon=True).start()
                jobs[job.id] = job
                return self._json({"job": job.id})
            if path == "/api/chargetest":
                d = monitor.get(b.get("device", ""))
                if not d:
                    return self._json({"error": "Appareil déconnecté"}, 404)
                rid = b.get("report_id")

                job = diagnostic.start_charge_test(d, duration=min(int(b.get("duration") or 20), 120))
                jobs[job.id] = job
                if rid:
                    def attach(job=job, rid=rid):
                        import time as _t
                        while not job.done:
                            _t.sleep(1)
                        if job.result:
                            rep = history.load(rid)
                            rep["charge_test"] = job.result["summary"]
                            history.save(rep)
                    import threading
                    threading.Thread(target=attach, daemon=True).start()
                return self._json({"job": job.id})
            if path == "/api/firmware/download":
                fws = firmware.list_firmwares(b.get("pt", ""))
                fw = next((f for f in fws if f["url"] == b.get("url")), None)
                if not fw:
                    return self._json({"error": "Firmware inconnu pour ce modèle"}, 400)
                return self._json({k: v for k, v in firmware.start_download(fw).items() if k != "cancel"})
            if path == "/api/firmware/cancel":
                st = firmware.downloads.get(b.get("url"))
                if st:
                    st["cancel"] = True
                return self._json({"ok": True})
            if path == "/api/flash/detect":
                job = flash.run("detect", [])
                jobs[job.id] = job
                return self._json({"job": job.id})
            if path == "/api/flash/recovery":
                act = b.get("action")
                if act == "enter":
                    d = monitor.get(b.get("device", ""))
                    if not d or d.get("transport") != "lockdown":
                        return self._json({"error": "iPhone non détecté en mode normal"}, 404)
                    job = flash.run("enter-recovery", [d["serial"]], monitor.label(d))
                elif act == "exit":
                    job = flash.run("exit-recovery", [str(int(b["ecid"]))] if b.get("ecid") else [])
                else:
                    return self._json({"error": "action invalide"}, 400)
                jobs[job.id] = job
                return self._json({"job": job.id})
            if path == "/api/update/install":
                if flash.busy():
                    return self._json({"error": "Opération en cours : mise à jour reportée."}, 409)
                return self._json(updater.install())
            if path == "/api/cfg/list":
                job = flash.run("cfg-list", [])
                jobs[job.id] = job
                return self._json({"job": job.id})
            if path == "/api/cfg/restore":
                op = "cfg-restore" if b.get("op") == "restore" else "cfg-revive" if b.get("op") == "revive" else None
                ecid = str(b.get("ecid") or "")
                if not op or not re.match(r"^(0x)?[0-9A-Fa-f]{1,20}$", ecid):
                    return self._json({"error": "opération ou ECID invalide"}, 400)
                if (b.get("confirm") or "").strip().upper() != "FLASH":
                    return self._json({"error": "Confirmation incorrecte : tapez FLASH"}, 400)
                args = [ecid] + ([firmware.local_path(b["file"])] if b.get("file") else [])
                job = flash.run(op, args, b.get("model") or "iPhone")
                jobs[job.id] = job
                return self._json({"job": job.id})
            if path == "/api/flash/start":
                op = b.get("op")
                if op not in ("restore-update", "restore-erase"):
                    return self._json({"error": "opération invalide"}, 400)
                ipsw = firmware.local_path(b.get("file", ""))
                d = monitor.get(b.get("device", "")) if b.get("device") else None
                model = (d or {}).get("model") or b.get("model") or "IPHONE"
                expected = flash.confirm_phrase(op, model)
                if (b.get("confirm") or "").strip().upper() != expected:
                    return self._json({"error": "Confirmation incorrecte : tapez exactement « %s »" % expected}, 400)
                pt_dev = (d or {}).get("model_code") or b.get("product_type") or ""
                if pt_dev and not firmware.ipsw_matches(os.path.basename(ipsw), pt_dev):
                    return self._json({"error": "Ce firmware ne correspond pas au modèle %s" % pt_dev}, 400)
                batt = ((d or {}).get("battery") or {}).get("level")
                if batt is not None and batt < 30 and not b.get("force_low_battery"):
                    return self._json({"error": "Batterie à %s %% : chargez au moins à 30 %% avant de flasher." % batt}, 400)
                if d and d.get("transport") == "lockdown":
                    target = d["serial"]
                elif b.get("ecid"):
                    target = "ecid:%d" % int(b["ecid"])
                else:
                    return self._json({"error": "Aucun appareil cible"}, 400)
                job = flash.run(op, [ipsw, "erase" if op == "restore-erase" else "update", target], model)
                jobs[job.id] = job
                return self._json({"job": job.id})
            if path == "/api/blacklist":
                imei = (b.get("imei") or "").strip()
                if not imei_valid(imei):
                    return self._json({"error": "IMEI invalide (15 chiffres, clé de contrôle incorrecte)"}, 400)
                if b.get("status") not in ("clean", "blacklisté", "inconnu", None):
                    return self._json({"error": "statut invalide"}, 400)
                if b.get("status"):
                    monitor.blacklist[imei] = {"status": b["status"], "source": "imeipro.info", "checked_at": time.time()}
                    if b.get("report_id"):
                        rep = history.load(b["report_id"])
                        rep["blacklist"] = monitor.blacklist[imei]
                        history.save(rep)
                return self._json({"url": "https://imeipro.info/?phone=" + imei, "result": monitor.blacklist.get(imei)})
            if path == "/api/report/quick":
                d = monitor.get(b.get("device", ""))
                if not d:
                    return self._json({"error": "Appareil déconnecté"}, 404)
                return self._json({"id": save_report("fiche", d, None, b.get("ticket", ""))["id"]})
            if path == "/api/report/tests":
                rep = history.load(b.get("id", ""))
                allowed = {t["id"] for t in diagnostic.TESTS}
                for k, v in (b.get("results") or {}).items():
                    if k in allowed and v.get("result") in ("ok", "échec", "non testé", "n/a"):
                        rep["manual_tests"][k] = {"result": v["result"], "note": (v.get("note") or "")[:300]}
                history.save(rep)
                return self._json({"ok": True})
            return self._json({"error": "route inconnue"}, 404)
        except (ValueError, OSError, KeyError) as e:
            return self._json({"error": str(e)}, 400)


def serve(port=8765):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    monitor.start()
    return httpd
