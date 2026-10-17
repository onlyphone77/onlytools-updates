import argparse
import webbrowser

from .server import serve


def main():
    ap = argparse.ArgumentParser(description="Only Tools")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = "http://127.0.0.1:%d/" % a.port
    try:
        httpd = serve(a.port)
    except OSError:
        # Port occupé : si Only Phone tourne déjà, on ouvre simplement l'interface existante.
        try:
            import urllib.request
            urllib.request.urlopen(url + "api/tests", timeout=2)
            print("Only Tools est déjà lancé : %s" % url)
            if not a.no_browser:
                webbrowser.open(url)
            return
        except Exception:
            print("Le port %d est occupé par un autre programme. Relancez avec : python3 -m onlyphone --port 8780" % a.port)
            input("Appuyez sur Entrée pour fermer.")
            return
    print("Only Tools : %s  (Ctrl+C pour quitter)" % url)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
