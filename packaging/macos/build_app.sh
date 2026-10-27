#!/bin/bash
# Construit « Only Tools.app » autonome (Python + bibliothèques embarqués, rien à installer chez le collègue).
#   packaging/macos/build_app.sh <dossier de sortie>        -> app pour l'architecture de cette machine
#   packaging/macos/build_app.sh <sortie> --add <autre.app>  -> ajoute le Python d'une autre architecture (app universelle)
# Puis : packaging/macos/make_dmg.sh <sortie>
set -euo pipefail
PBS_TAG=20261009            # python-build-standalone (astral-sh), version ≥ 2 semaines
PY_VER=3.12.15
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "${1:?dossier de sortie}" && OUT="$(cd "$1" && pwd)"   # chemin absolu
APP="$OUT/Only Tools.app"
RES="$APP/Contents/Resources"
ARCH="$(uname -m)"          # x86_64 ou arm64
CACHE="$OUT/cache"
mkdir -p "$CACHE"

if [ "${2:-}" = "--add" ]; then   # fusion : python-<arch> d'une autre app construite sur l'autre type de Mac
  for d in "${3:?app à fusionner}/Contents/Resources"/python-*; do
    [ -d "$RES/$(basename "$d")" ] || cp -R "$d" "$RES/"
  done
  codesign --force --deep -s - "$APP"
  echo "Architectures : $(ls -d "$RES"/python-* | xargs -n1 basename | tr '\n' ' ')"
  exit 0
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"

# 1. Python autonome (empreinte SHA-256 vérifiée)
PBS_ARCH=$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86_64)
TGZ="cpython-$PY_VER+$PBS_TAG-$PBS_ARCH-apple-darwin-install_only.tar.gz"
BASE="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG"
[ -f "$CACHE/SHA256SUMS-$PBS_TAG" ] || curl -fsSL -o "$CACHE/SHA256SUMS-$PBS_TAG" "$BASE/SHA256SUMS"
[ -f "$CACHE/$TGZ" ] || curl -fsSL -o "$CACHE/$TGZ" "$BASE/${TGZ/+/%2B}"
EXPECTED="$(grep " $TGZ\$" "$CACHE/SHA256SUMS-$PBS_TAG" | cut -d' ' -f1)"
GOT="$(shasum -a 256 "$CACHE/$TGZ" | cut -d' ' -f1)"
[ -n "$EXPECTED" ] && [ "$EXPECTED" = "$GOT" ] || { echo "Empreinte Python incorrecte"; exit 1; }
mkdir -p "$RES/python-$ARCH"
tar -xzf "$CACHE/$TGZ" -C "$RES/python-$ARCH" --strip-components 1
PY="$RES/python-$ARCH/bin/python3"

# 2. Bibliothèques (iPhone, fenêtre, USB) dans ce Python uniquement
PYTHONNOUSERSITE=1 "$PY" -m pip install -q --no-warn-script-location --disable-pip-version-check \
  --prefer-binary --only-binary=cryptography "cryptography>=43,<46" -r "$ROOT/requirements.txt"
find "$RES/python-$ARCH" -name "__pycache__" -type d -prune -exec rm -rf {} +
# allègement : tests, interface Tk, console interactive (inutiles pour l'app)
L="$RES/python-$ARCH/lib"
rm -rf "$RES/python-$ARCH/share" "$RES/python-$ARCH/include" "$L"/python3.12/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} \
  "$L"/python3.12/config-3.12-darwin "$L"/tcl* "$L"/tk* "$L"/itcl* "$L"/thread* "$L"/libtcl* "$L"/libtk* \
  "$L"/python3.12/site-packages/{PyObjCTest,jedi}

# 3. Logiciel, icône, ADB (Google)
rsync -a --exclude __pycache__ --exclude .DS_Store "$ROOT/onlyphone" "$RES/"
cp "$ROOT/packaging/macos/AppIcon.icns" "$RES/"
if [ ! -d "$CACHE/platform-tools" ]; then
  curl -fsSL -o "$CACHE/pt.zip" https://dl.google.com/android/repository/platform-tools-latest-darwin.zip
  unzip -q -o "$CACHE/pt.zip" -d "$CACHE"
fi
mkdir -p "$RES/tools" && cp -R "$CACHE/platform-tools" "$RES/tools/"

# 4. Lanceur + Info.plist
cp "$ROOT/packaging/macos/launcher.sh" "$APP/Contents/MacOS/Only Tools"
chmod +x "$APP/Contents/MacOS/Only Tools"
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/onlyphone/__init__.py")"
cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Only Tools</string><key>CFBundleDisplayName</key><string>Only Tools</string>
<key>CFBundleIdentifier</key><string>fr.onlyphone.onlytools</string>
<key>CFBundleShortVersionString</key><string>$VER</string><key>CFBundleVersion</key><string>$VER</string>
<key>CFBundlePackageType</key><string>APPL</string><key>CFBundleExecutable</key><string>Only Tools</string>
<key>CFBundleIconFile</key><string>AppIcon</string><key>LSMinimumSystemVersion</key><string>11.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PL

# 5. Contrôle : le logiciel démarre avec ce Python
(cd "$RES" && PYTHONNOUSERSITE=1 "$PY" -W ignore -c "import onlyphone.server, onlyphone.desktop, onlyphone.phone_tasks, webview, pymobiledevice3, cryptography, libusb_package; print('imports ok')")

codesign --force --deep -s - "$APP"   # signature locale (pas de compte développeur Apple)
echo "Prêt : $APP ($(du -sh "$APP" | cut -f1), $ARCH, version $VER)"
