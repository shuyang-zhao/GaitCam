#!/usr/bin/env bash
# Assemble the portable Windows bundle on Linux (no PyInstaller: nothing packed / self-extracting,
# so antivirus heuristics have nothing to object to). Output: build/dist/GaitCam-win64-<ver>.zip
#
#   runtime/      python.org embeddable Python (pythonw.exe is Authenticode-signed by the PSF) + site-packages
#   ffmpeg/       ffmpeg.exe, ffprobe.exe + shared DLLs (BtbN LGPL shared build)
#   app/          the GaitCam sources
#   calibration/  printable calibration targets (PDF)
#   licenses/     GaitCam's license, third-party notices, GPL / LGPL texts
#   GaitCam.bat, GaitCam (demo, no camera).bat, README.txt
#
# Needs a CPython 3.12 to fetch wheels and precompile bytecode (the .pyc format is version-specific):
#   PYTHON=/path/to/python3.12 bash build/build_windows.sh        (default: the project's .env-dev)
# Versions are pinned so the bundle matches THIRD_PARTY_NOTICES.md; update both together.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(dirname "$HERE")
PY=${PYTHON:-$ROOT/.env-dev/bin/python}
"$PY" -c 'import sys; assert sys.version_info[:2] == (3, 12), "need CPython 3.12, got " + sys.version'
PYVER=3.12.10
FFZIP=ffmpeg-n8.1-latest-win64-lgpl-shared-8.1.zip
WHEELS=("PySide6-Essentials==6.8.3" "numpy==2.2.6" "opencv-python-headless==4.10.0.84")
VER=$(grep -oP 'APP_VERSION = "\K[^"]+' "$ROOT/app/engine.py")
CACHE=$HERE/cache; OUT=$HERE/dist/GaitCam; ZIP=$HERE/dist/GaitCam-win64-$VER.zip
mkdir -p "$CACHE"; rm -rf "$OUT" "$ZIP"; mkdir -p "$OUT"/{runtime,ffmpeg,app,calibration,licenses}

# 1. embeddable Python
[ -f "$CACHE/python-$PYVER-embed-amd64.zip" ] || curl -sSfL -o "$CACHE/python-$PYVER-embed-amd64.zip" \
    "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-amd64.zip"
unzip -q "$CACHE/python-$PYVER-embed-amd64.zip" -d "$OUT/runtime"
PTH=$(ls "$OUT"/runtime/python3*._pth)
printf 'python312.zip\r\n.\r\nLib\\site-packages\r\n..\\app\r\nimport site\r\n' > "$PTH"

# 2. Windows wheels, installed straight into the bundle
"$PY" -m pip install -q --no-compile --platform win_amd64 --python-version 3.12 \
    --implementation cp --only-binary=:all: --target "$OUT/runtime/Lib/site-packages" "${WHEELS[@]}"
SP=$OUT/runtime/Lib/site-packages
# trim parts of Qt a widgets app never loads (developer tools, QML, translations, software OpenGL)
rm -rf "$SP"/PySide6/{translations,qml,metatypes,include,typesystems,glue,scripts} "$SP"/PySide6/*.exe \
       "$SP"/PySide6/opengl32sw.dll "$SP"/bin 2>/dev/null || true
find "$SP" -name "__pycache__" -type d -prune -exec rm -rf {} +
rm -f "$SP"/*.whl

# 3. ffmpeg (the cached "latest" zip is the build named in THIRD_PARTY_NOTICES.md; a fresh download is newer)
[ -f "$CACHE/$FFZIP" ] || curl -sSfL -o "$CACHE/$FFZIP" \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$FFZIP"
unzip -q -j "$CACHE/$FFZIP" '*/bin/*' -x '*/bin/ffplay.exe' -d "$OUT/ffmpeg"
unzip -q -j "$CACHE/$FFZIP" '*/LICENSE.txt' -d "$OUT/ffmpeg"

# 4. app, calibration targets, licenses, launchers (CRLF)
cp "$ROOT"/app/*.py "$OUT/app/"
cp "$ROOT"/calibration/*.pdf "$OUT/calibration/"
sed 's/$/\r/' "$ROOT/LICENSE" > "$OUT/licenses/LICENSE-GaitCam.txt"
sed 's/$/\r/' "$ROOT/THIRD_PARTY_NOTICES.md" > "$OUT/licenses/THIRD_PARTY_NOTICES.txt"
for f in "$ROOT"/licenses/*.txt; do sed 's/$/\r/' "$f" > "$OUT/licenses/$(basename "$f")"; done
printf '@echo off\r\nstart "" "%%~dp0runtime\\pythonw.exe" "%%~dp0app\\main.py"\r\n' > "$OUT/GaitCam.bat"
printf '@echo off\r\nstart "" "%%~dp0runtime\\pythonw.exe" "%%~dp0app\\main.py" --demo\r\n' > "$OUT/GaitCam (demo, no camera).bat"
sed 's/$/\r/' "$ROOT/README.md" > "$OUT/README.txt"

# 5. precompile bytecode (CPython 3.12, same as the bundle). unchecked-hash: valid regardless of file times,
#    which unzipping changes - otherwise every launch recompiles numpy & co. (slow cold start on Windows)
"$PY" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$OUT/app" "$SP" >/dev/null

(cd "$HERE/dist" && zip -q -r -9 "$(basename "$ZIP")" GaitCam)
echo "bundle: $(du -sh "$OUT" | cut -f1)  zip: $(du -sh "$ZIP" | cut -f1)  -> $ZIP"
