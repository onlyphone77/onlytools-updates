#!/bin/bash
# .dmg « glisser dans Applications » à partir de <sortie>/Only Tools.app (construit par build_app.sh).
#   packaging/macos/make_dmg.sh <sortie>   ->  <sortie>/OnlyTools-Mac.dmg
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?dossier de sortie}"
APP="$OUT/Only Tools.app"
DMG="$OUT/OnlyTools-Mac.dmg"
ST="$OUT/dmg-staging"
RW="$OUT/rw.dmg"
VOL="Only Tools"
[ -d "$APP" ] || { echo "App absente : lancez build_app.sh"; exit 1; }
[ ! -d "/Volumes/$VOL" ] || { echo "Un volume « $VOL » est déjà monté : éjectez-le."; exit 1; }

rm -rf "$ST" "$RW" && mkdir -p "$ST/.background"
cp -R "$APP" "$ST/"
ln -s /Applications "$ST/Applications"
cp "$ROOT/packaging/macos/dmg-background.png" "$ST/.background/bg.png"
hdiutil create -quiet -srcfolder "$ST" -volname "$VOL" -fs HFS+ -format UDRW -ov "$RW"
hdiutil attach -quiet -readwrite -noverify -noautoopen "$RW"

# Mise en page de la fenêtre (Finder) ; sans elle le .dmg reste utilisable
osascript <<AS || echo "Mise en page ignorée (Finder non pilotable ici)."
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set bounds of container window to {200, 120, 840, 542}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 112
    set text size of opts to 13
    set background picture of opts to file ".background:bg.png"
    set position of item "Only Tools.app" of container window to {150, 215}
    set position of item "Applications" of container window to {490, 215}
    update without registering applications
    delay 1
    close
  end tell
end tell
AS
sync
hdiutil detach -quiet "/Volumes/$VOL"
hdiutil convert -quiet "$RW" -format UDZO -imagekey zlib-level=9 -o "$DMG" -ov
rm -rf "$RW" "$ST"
echo "Prêt : $DMG ($(du -h "$DMG" | cut -f1))"
