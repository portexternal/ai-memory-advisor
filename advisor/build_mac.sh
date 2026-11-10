#!/bin/bash
# Builds dist/AIMemoryAdvisor.app and dist/AIMemoryAdvisor-mac/ (app + readme + helper scripts).
# Needs: pip install -r requirements.txt pyinstaller. Run on a Mac: bash advisor/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python -m PyInstaller --noconfirm --clean --windowed --name AIMemoryAdvisor --paths . \
  --osx-bundle-identifier com.aimemoryadvisor.app \
  --distpath dist --workpath build --specpath build advisor/app.py
APP=dist/AIMemoryAdvisor.app
# background agent: no Dock icon, no menu bar
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$APP/Contents/Info.plist"
codesign --force --deep -s - "$APP"
OUT=dist/AIMemoryAdvisor-mac
rm -rf "$OUT" && mkdir -p "$OUT"
cp -R "$APP" "$OUT/"
cp advisor/README-for-testers-mac.txt "$OUT/README.txt"
cp advisor/mac/*.command "$OUT/"
cp docs/TERMS.md "$OUT/TERMS.txt"
cp docs/PRIVACY.md "$OUT/PRIVACY.txt"
chmod +x "$OUT"/*.command
ls -la "$OUT"