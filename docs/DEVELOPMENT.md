# Development

## Layout

| Path | What |
|---|---|
| `app/engine.py` | No Qt: camera discovery and modes, ffmpeg capture + preview, exposure, ready checks, per-trial QC, naming, crash recovery |
| `app/gaitcam.py` | The PySide6 window |
| `app/i18n.py` | Every user-facing string, English + 中文 |
| `app/main.py` | Launcher: shows a splash at once, then builds the window behind it |
| `calibration/` | Printable targets (`make_targets.py` → PDFs, `check_targets.py` verifies them) |
| `build/build_windows.sh` | Builds the portable Windows bundle (run on Linux) |
| `build/test/` | Tests and tools (see below) |

Run from source: `pip install -r requirements.txt`, with ffmpeg / ffprobe on PATH, then `python app/main.py --demo`.

## Tests

```bash
python build/test/test_engine.py                               # unit tests, ~30 s
QT_QPA_PLATFORM=offscreen python build/test/stress_gui.py      # GUI stress test with the demo camera, ~9 min
QT_QPA_PLATFORM=offscreen python build/test/shots.py           # screenshots of the main UI states
```

* `test_engine.py` covers:
  - naming and trial numbering
  - QC verdicts on synthetic clips at 120 and 240 fps: clean, dropped frames, too slow, too short, exposure ramp
  - reject, crash recovery and the computer checks
  - parsing and choosing camera modes
  - completeness of the translations
* `stress_gui.py` drives the real window with the demo camera, which offers 1280×800 @ 120 and 640×400 @ 240. It
  covers:
  - 30 rapid start / stop cycles, with a detector for UI freezes
  - a 60-s run
  - a camera lost while recording and while idle, and a failed start
  - an unwritable data folder
  - typing latency with 300 runs in the folder
  - CPU and memory over 2 minutes idle
  - switching to 640×400 @ 240 fps through the Settings dialog and recording at that rate
* `profile_ui.py` samples UI-thread stalls.

Calibration targets: `python calibration/make_targets.py` writes both PDFs. It needs OpenCV ≥ 4.7, matplotlib and
the Noto Sans CJK SC font (found by name, or set `GAITCAM_CJK_FONT=/path/to/font`). With the same library and font
versions the output is byte-identical. `python calibration/check_targets.py [preview_dir]` renders the PDFs (needs
PyMuPDF) and checks detection, square sizes and the 100 mm bars. It writes preview PNGs only when you give a
directory.

Reference results (demo camera, Linux workstation):

| Measure | Result |
|---|---|
| Longest UI pause over 30 start / stop cycles | 76 ms |
| Keystroke with 300 runs in the folder | 0.3 ms |
| Idle preview CPU (excluding ffmpeg) | 11 % of one core |
| Preview redraw rate | 30 frames/s |
| Memory drift over 2 minutes idle | +0.1 MB |
| 240 fps run | 240.0 fps, 0 gaps |

## Design notes

* **Capture.** ffmpeg reads the DirectShow MJPEG pin and copies it (`-c:v copy`) into Matroska. Matroska keeps
  per-frame timestamps, so dropped frames can be found afterwards. The same ffmpeg process also writes a 640×400
  grey preview at 30 fps to stdout for the window.
* **Modes.** `ffmpeg -list_options` on the device yields one entry per MJPEG picture size, with its maximum rate.
  A saved size is kept if the camera offers it. A newly selected camera gets its fastest mode (larger picture on
  ties). The frame-rate list shows the maximum first, then common lower rates. Exposures longer than one frame
  are not offered, because the camera would slow down.
* **Exposure.** On Windows only DirectShow reaches the UVC exposure control (OpenCV's default MSMF backend cannot).
  GaitCam therefore sets manual exposure with `cv2.CAP_DSHOW`, releases the device and hands it to ffmpeg.
  DirectShow exposure values are integer log2(seconds), hence the 0.5 / 1 / 2 / 4 / 8 ms steps.
* **QC.**
  - A frame interval longer than 1.5× the nominal interval counts as at least one lost frame.
  - ✓ means at least 97 % of the target rate and at most 0.5 % gaps. ⚠ means at least 90 % and at most 2 %.
    Anything worse is ✗, and a run under 1 s is treated as a mis-click.
  - Exposure ramp: the mean brightness of the first 3 frames is compared with 0.375–0.5 s in. A ratio below 0.75 is
    ⚠. On the reference camera the automatic exposure / gain took about 0.5 s to settle (brightness 39 → 64).
* **Responsiveness.** All camera, ffmpeg and disk work runs on one background worker, in order, and the UI thread
  only draws.
  - The preview is painted straight from the numpy buffer.
  - Statistics update at 4 Hz.
  - Trial lists come from a per-day index rather than re-reading the JSON files on each keystroke.
  - OpenCV is imported behind the splash. Importing it later on the worker held the interpreter lock for ~0.2 s and
    froze the UI.
* **Launch.** `main.py` shows a plain frameless splash as soon as Qt is loaded. It avoids `QSplashScreen`, which
  blocks for up to 1 s waiting for an expose event.
* **Windows bundle.** It combines the python.org embeddable Python, prebuilt wheels and the BtbN FFmpeg build.
  It avoids PyInstaller because self-extracting single-file executables are the main source of antivirus false
  positives. Bytecode is precompiled with `--invalidation-mode unchecked-hash`, so the file times that unzipping
  changes do not force a recompile.

Reference camera: an Arducam OV9281 UVC camera at 1280×800 MJPEG, 120 fps, on Windows. It recorded 120.0 fps with
0 dropped frames at about 40 Mbit/s (5 MB/s).

## Open issue: exposure lock

Setting manual exposure through DirectShow and then handing the device to ffmpeg did not reliably stop the
automatic exposure / gain on the reference camera.

Next steps:
1. Find out whether automatic exposure or automatic gain is responsible (see the `exposure` lines in `gaitcam.log`).
2. Check whether unticking *Auto* in the driver's property page persists across sessions.
3. If it does not, the capture process itself should hold the exposure, for example a single OpenCV DirectShow
   session that writes the raw MJPEG.

## Releasing

1. Bump `APP_VERSION` in `app/engine.py` and `version` in `CITATION.cff`.
2. Run the tests.
3. Run `bash build/build_windows.sh` (`PYTHON=` a CPython 3.12) to get `build/dist/GaitCam-win64-<version>.zip`.
   Smoke-test it on Windows, with the demo camera and with a real camera.
4. Tag `v<version>`, create a GitHub release, and attach the zip. If the bundled FFmpeg or wheel versions changed,
   update `THIRD_PARTY_NOTICES.md` (FFmpeg's version string is in `ffmpeg -version`).
5. For a DOI, enable the repository in Zenodo's GitHub integration **before** creating the release; every release
   then gets an archived copy and a DOI. Add the DOI to `CITATION.cff` and the README.
