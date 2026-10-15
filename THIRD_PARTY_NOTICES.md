# Third-party software

GaitCam's own code is released under the MIT License (`LICENSE`). The source repository contains no
third-party code.

## Portable Windows bundle

The portable bundle (`GaitCam-win64-<version>.zip` on the Releases page) also ships the following third-party
components, **unmodified**. Each one's license text is inside the bundle at the path given.

| Component | Version | License | License text in the bundle | Source code |
|---|---|---|---|---|
| Python, Windows embeddable package | 3.12.10 | PSF License 2.0 | `runtime/LICENSE.txt` | <https://www.python.org/downloads/release/python-31210/> |
| FFmpeg: `ffmpeg.exe`, `ffprobe.exe` and shared DLLs (BtbN "lgpl-shared" build, `--enable-version3`) | n8.1.3-14-g330caae0c1 (built 2026-10-05) | LGPL-3.0-or-later | `ffmpeg/LICENSE.txt`, `licenses/LGPL-3.0.txt`, `licenses/GPL-3.0.txt` | <https://github.com/FFmpeg/FFmpeg/commit/330caae0c1acccd2222edc52a05940c574561ce5>; build scripts: <https://github.com/BtbN/FFmpeg-Builds> |
| Qt for Python: PySide6-Essentials and shiboken6, with the Qt 6.8.3 libraries | 6.8.3 | LGPL-3.0-only | `licenses/LGPL-3.0.txt`, `licenses/GPL-3.0.txt` | PySide6: <https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/>; Qt: <https://download.qt.io/archive/qt/6.8/6.8.3/single/> |
| NumPy (with its bundled OpenBLAS and compiler runtime) | 2.2.6 | BSD-3-Clause; bundled libraries as listed in the file | `runtime/Lib/site-packages/numpy-2.2.6.dist-info/LICENSE.txt` | <https://github.com/numpy/numpy/releases/tag/v2.2.6> |
| OpenCV (`opencv-python-headless`, with its bundled FFmpeg video plugin) | 4.10.0.84 | Apache-2.0 (OpenCV), MIT (packaging); bundled libraries as listed in the files | `runtime/Lib/site-packages/cv2/LICENSE.txt`, `runtime/Lib/site-packages/cv2/LICENSE-3RD-PARTY.txt` | <https://github.com/opencv/opencv-python/releases/tag/84> |

**LGPL components (FFmpeg, Qt / PySide6).** They are used as published, without modification. GaitCam runs
FFmpeg as a separate program, and Qt / PySide6 are separate DLL and `.pyd` files. You may replace any of them
with your own build of a compatible version. Their source code is available at the links above.

## Calibration PDFs

`calibration/GaitCam_calibration_*.pdf` embed glyphs of **Noto Sans CJK SC** (© Google / Adobe, SIL Open Font
License 1.1, <https://github.com/notofonts/noto-cjk>) for the Chinese instructions. The OFL permits embedding fonts
in documents.

## Development-only dependencies (not part of the bundle)

| Package | Used by | License |
|---|---|---|
| matplotlib | `calibration/make_targets.py` (generates the PDF targets) | Matplotlib License (PSF-based, BSD-compatible) |
| Noto Sans CJK SC font | `calibration/make_targets.py` | SIL OFL 1.1 |
| PyMuPDF | `calibration/check_targets.py` (renders the PDFs to verify them) | AGPL-3.0 or commercial |
| opencv-python(-headless), numpy, PySide6 | running from source, tests, calibration scripts | as above |
