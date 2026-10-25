"""Application de bureau : fenêtre native (WebKit sur macOS, Edge WebView2 sur Windows) via pywebview."""
import json
import os
import socket
import time
import sys
import threading
import urllib.request
import warnings
import webbrowser

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "assets", "icon.png")


def _running(url):
    try:
        urllib.request.urlopen(url + "api/tests", timeout=2)
        return True
    except Exception:
        return False


def _free_port(preferred=8765):
    for p in (preferred,) + tuple(range(8770, 8800)):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("Aucun port local libre")


class Api(object):
    def __init__(self, base):
        self.base = base

    def restart(self):
        """Relance Only Tools (après mise à jour) : nouveau processus qui attend la fermeture de celui-ci."""
        import subprocess
        env = dict(os.environ, OPD_WAIT="3")
        kw = {"start_new_session": True} if sys.platform != "win32" else {"creationflags": 0x00000008 | 0x00000200}
        subprocess.Popen([sys.executable, "-W", "ignore", "-m", "onlyphone.desktop"], cwd=os.path.dirname(HERE), env=env, **kw)
        import webview
        for w in list(webview.windows):
            w.destroy()
        os._exit(0)

    def choose_ipsw(self):
        """Sélecteur de fichier natif pour importer un firmware .ipsw ; renvoie le chemin ou None."""
        import webview
        r = webview.windows[0].create_file_dialog(webview.OPEN_DIALOG, file_types=("Firmware Apple (*.ipsw)",))
        return r[0] if r else None

    def open_report(self, rid):
        # Le navigateur par défaut gère l'impression « Enregistrer en PDF » de façon fiable
        if isinstance(rid, str) and rid.isalnum():
            webbrowser.open(self.base + "report.html?id=" + rid)


    def open_imei_check(self, imei):
        """Ouvre imeipro.info dans une fenêtre du logiciel, pré-remplit l'IMEI, puis lit le résultat
        affiché après que le réparateur a validé le CAPTCHA et cliqué CHECK."""
        if not (isinstance(imei, str) and imei.isdigit() and len(imei) == 15):
            return
        import webview
        win = webview.create_window("Vérification blacklist — " + imei, "https://imeipro.info/", width=1000, height=820)
        closed = threading.Event()
        win.events.closed += lambda: closed.set()

        def fill():
            win.evaluate_js("(function(){var i=document.querySelector('input[name=phone]');"
                            "if(i){i.value='%s';i.dispatchEvent(new Event('input'));i.scrollIntoView({block:'center'});}})()" % imei)
        win.events.loaded += fill

        def watch():
            end = time.time() + 900
            while not closed.is_set() and time.time() < end:
                time.sleep(1)
                try:
                    r = win.evaluate_js("(function(){var o=document.getElementById('other');"
                                        "var td=document.querySelector('#other tr.blacklist td:nth-child(2)');"
                                        "if(!o||o.style.display==='none'||!td)return '';"
                                        "return td.className+'|'+td.innerText+'|'+(document.querySelector('#other tr.imeiNumber td.imei')||{}).innerText;})()")
                except Exception:
                    continue
                status = parse_imeipro_result(r, imei)
                if status:
                    self._post_blacklist(imei, status)
                    time.sleep(2.5)
                    try:
                        win.destroy()
                    except Exception:
                        pass
                    return
        threading.Thread(target=watch, daemon=True).start()

    def _post_blacklist(self, imei, status):
        req = urllib.request.Request(self.base + "api/blacklist", json.dumps({"imei": imei, "status": status}).encode(),
                                     {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


def parse_imeipro_result(raw, imei):
    """'clean|CLEAN|490154…' -> 'clean' ; 'blacklisted|BLACKLISTED…' -> 'blacklisté' ; UNKNOWN -> 'inconnu'.
    Ignore un résultat qui concernerait un autre IMEI. Pure, testable."""
    if not raw or "|" not in raw:
        return None
    cls, text, shown = (raw.split("|") + ["", ""])[:3]
    digits = "".join(c for c in shown if c.isdigit())
    if digits and digits != imei:
        return None
    if "blacklisted" in cls.split() or text.strip().upper().startswith("BLACKLISTED"):
        return "blacklisté"
    if "clean" in cls.split() or text.strip().upper() == "CLEAN":
        return "clean"
    if text.strip().upper() == "UNKNOWN":
        return "inconnu"
    return None


def _set_dock_icon():
    if sys.platform != "darwin" or not os.path.isfile(ICON):
        return
    try:
        from AppKit import NSApplication, NSImage
        NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(ICON))
    except Exception:
        pass


def _log_to_file_if_no_console():
    """pythonw (Windows) n'a pas de console : les erreurs vont dans un journal."""
    if sys.stderr is None or sys.stdout is None:
        from .history import data_dir
        f = open(os.path.join(os.path.dirname(data_dir()), "app.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f


def main():
    _log_to_file_if_no_console()
    if os.environ.get("OPD_WAIT"):  # relance après mise à jour : laisser l'ancienne instance se fermer
        time.sleep(float(os.environ.pop("OPD_WAIT")))
    import webview
    from .server import serve
    port = 8765
    base = "http://127.0.0.1:%d/" % port
    if not _running(base):
        port = _free_port(port)
        base = "http://127.0.0.1:%d/" % port
        httpd = serve(port)
        threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    webview.create_window("Only Tools", base, js_api=Api(base), width=1360, height=880,
                          min_size=(1000, 680), background_color="#141518")
    webview.start(_set_dock_icon)


if __name__ == "__main__":
    main()
