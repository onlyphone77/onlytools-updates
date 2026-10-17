"""Localisation et exécution sûre des outils externes (adb, libimobiledevice)."""
import os
import shutil
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_cache = {}


def find_tool(name):
    """Retourne le chemin de l'outil ou None. Cherche PATH, dossier tools/ et emplacements SDK usuels."""
    if name in _cache:
        return _cache[name]
    exe = name + (".exe" if os.name == "nt" else "")
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(_HERE, "..", "tools", exe),
        os.path.join(_HERE, "..", "tools", "platform-tools", exe),
    ]
    found = shutil.which(exe)
    if not found:
        if name == "adb":
            for base in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                         os.path.join(home, "Library", "Android", "sdk"),
                         os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk")):
                if base:
                    candidates.append(os.path.join(base, "platform-tools", exe))
        if name == "cfgutil":  # Apple Configurator (Mac App Store), même sans « outils d'automatisation »
            candidates += ["/Applications/Apple Configurator.app/Contents/MacOS/cfgutil",
                           "/Applications/Apple Configurator 2.app/Contents/MacOS/cfgutil"]
        candidates += ["/opt/homebrew/bin/" + exe, "/usr/local/bin/" + exe]
        for c in candidates:
            if os.path.isfile(c) and os.access(c, os.X_OK):
                found = os.path.abspath(c)
                break
    if found:  # un outil absent sera recherché à nouveau (installation en cours de session)
        _cache[name] = found
    return found


def reset_cache():
    _cache.clear()


class Result(object):
    def __init__(self, code, out, err, missing=False, timeout=False):
        self.code, self.out, self.err = code, out, err
        self.missing, self.timeout = missing, timeout

    @property
    def ok(self):
        return self.code == 0 and not self.missing and not self.timeout

    def __repr__(self):
        return "Result(code=%r, missing=%r, timeout=%r)" % (self.code, self.missing, self.timeout)


def run(args, timeout=10):
    """Exécute une commande sans shell. Ne lève pas d'exception : renvoie un Result."""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                           stdin=subprocess.DEVNULL, **kwargs)
    except FileNotFoundError:
        return Result(127, "", "outil introuvable", missing=True)
    except subprocess.TimeoutExpired:
        return Result(124, "", "délai dépassé", timeout=True)
    except OSError as e:
        return Result(126, "", str(e))
    dec = lambda b: b.decode("utf-8", "replace") if b else ""
    return Result(p.returncode, dec(p.stdout), dec(p.stderr))
