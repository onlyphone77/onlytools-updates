#!/bin/bash
# Lanceur de « Only Tools.app » : Python embarqué, code dans ~/Library/Application Support/OnlyTools/app
# (c'est là que les mises à jour automatiques s'installent ; l'app ne fait que fournir la première version).
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
PY="$RES/python-$(uname -m)/bin/python3"
[ -x "$PY" ] || PY="$RES/python-x86_64/bin/python3"   # Mac Apple Silicon avec Rosetta, app Intel seule
RT="$HOME/Library/Application Support/OnlyTools/app"
LOG="$HOME/Library/Logs/OnlyTools.log"
mkdir -p "$RT" "$(dirname "$LOG")"
export PYTHONNOUSERSITE=1

ver() { sed -n 's/^__version__ = "\(.*\)"/\1/p' "$1/onlyphone/__init__.py" 2>/dev/null; }
NEWER=$("$PY" -c 'import sys
t = lambda v: tuple(int(x) for x in v.split(".")) if v else (0,)
print(t(sys.argv[1]) > t(sys.argv[2]))' "$(ver "$RES")" "$(ver "$RT")")
if [ "$NEWER" = "True" ]; then   # première installation, ou app plus récente que le code mis à jour
  rsync -a --delete --exclude __pycache__ "$RES/onlyphone/" "$RT/onlyphone/"
fi
[ -x "$RT/tools/platform-tools/adb" ] || { mkdir -p "$RT/tools"; cp -R "$RES/tools/platform-tools" "$RT/tools/"; }

cd "$RT" || exit 1
exec "$PY" -W ignore -m onlyphone.desktop >> "$LOG" 2>&1
