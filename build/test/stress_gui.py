"""Headless GUI stress test (demo camera): drives the real Main window through many scenarios, then checks
invariants on disk. Takes ~8-9 min; run it on an otherwise idle machine (the UI-latency checks are timing-based).

    QT_QPA_PLATFORM=offscreen python build/test/stress_gui.py
"""
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="gaitcam_stress_"))
os.environ["APPDATA"] = str(tmp)                   # settings + log go to the temp folder, not the user's
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "app"))
import engine as E  # noqa: E402

s = E.load_settings()
s["data_root"] = str(tmp / "data")
s["experiment"] = "stress"
E.save_settings(s)
busy = E.session_dir(s)
busy.mkdir(parents=True)
for k in range(300):                               # a busy day: 300 earlier runs of 30 animals
    animal, trial = f"{500 + k % 30:03d}", k // 30 + 1
    stem = f"{animal}_t{trial:02d}_20260101-0000{k % 60:02d}"
    (busy / f"{stem}.mkv").write_bytes(b"")
    meta = {"file": stem + ".mkv", "animal": animal, "trial": trial, "started": "x", "status": "kept",
            "fps_target": 120, "exposure_ms": 2, "width": 1280, "height": 800, "camera": "x", "notes": "",
            "qc": {"verdict": "ok", "fps_mean": 120, "duration_s": 5}}
    (busy / f"{stem}.json").write_text(json.dumps(meta))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import gaitcam as G  # noqa: E402
# as in production: OpenCV is preloaded by gaitcam.run() behind the splash
import cv2  # noqa: E402,F401

random.seed(7)
app = QApplication(sys.argv)
app.setStyleSheet(G.QSS)
w = G.Main(demo=True)
w.show()
fails = []
lat_start, lat_stop = [], []

ticks = []                                         # UI responsiveness: every UI-timer tick (should come every ~33 ms)
_orig_tick = w._tick


def _tick_spy():
    ticks.append(time.monotonic())
    _orig_tick()


w.timer.timeout.disconnect()
w.timer.timeout.connect(_tick_spy)


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        fails.append(what)


def rss_mb():
    with open("/proc/self/status") as fh:
        return int(re.search(r"VmRSS:\s+(\d+)", fh.read()).group(1)) / 1024


def longest_pause():
    return max(b - a for a, b in zip(ticks, ticks[1:]))


# scenario steps are yielded to the event-loop driver below, so the UI keeps running while we wait
def until(cond, timeout):
    return ("until", cond, timeout)


def sleep(seconds):
    return ("sleep", seconds)


def ready():
    return w.rec_btn.isEnabled()


def live_ok():
    return w.chk["live"][0] == "ok"


def idle():
    return w.state == "idle"


def recording():
    return w.state == "recording"


def scenario():
    yield until(lambda: w.sess is not None and live_ok(), 20)
    check(w.rec_btn.isEnabled(), "record enabled once checks are green")

    # 1. 30 rapid runs
    ids = ["1", "2", "003", "M-7"]
    for _ in range(30):
        w.id_edit.setText(random.choice(ids))
        if random.random() < 0.25:
            w.set_lang(random.choice(["en", "zh"]))
        yield until(ready, 15)
        t0 = time.monotonic()
        w.start_record()
        for _ in range(random.randint(0, 3)):      # button mashing while starting
            w.toggle_record()
        yield until(recording, 15)
        lat_start.append(time.monotonic() - t0)
        yield sleep(random.choice([0.3, 0.8, 1.5, 2.5]))
        t0 = time.monotonic()
        w.stop_record()
        yield until(lambda: idle() and w.sess is not None and w.sess.frame is not None, 20)
        lat_stop.append(time.monotonic() - t0)
        if random.random() < 0.2:
            w.reject_last()
    check(w.state == "idle", "30 rapid runs finished, app idle")
    pause = longest_pause()
    check(pause < 0.10, f"UI never froze during 30 start/stop cycles (longest pause {1000 * pause:.0f} ms)")
    print(f"INFO start latency median {statistics.median(lat_start):.2f} s (max {max(lat_start):.2f}); "
          f"stop -> preview back median {statistics.median(lat_stop):.2f} s (max {max(lat_stop):.2f})", flush=True)

    # 2. 'calib' typed as an animal ID must record normally (old bug: auto-stopped after 1.2 s)
    yield until(ready, 15)
    w.id_edit.setText("calib")
    w.start_record()
    yield until(recording, 15)
    yield sleep(2.5)
    w.stop_record()
    yield until(idle, 20)
    check(w.last and w.last[0] is not None and w.last[1].duration_s > 2.0 and w.last[0].parent.name != "calib",
          f"animal 'calib' recorded as a normal run ({w.last[1].duration_s:.1f} s)")

    # 3. calibration clip auto-stops
    yield until(ready, 15)
    w.start_record(calib=True)
    yield until(lambda: idle() and w.last[0] is not None and w.last[0].parent.name == "calib", 20)
    check(w.last[1].duration_s < 2.5, f"calibration clip auto-stopped ({w.last[1].duration_s:.1f} s)")

    # 4. long run, memory
    yield until(ready, 15)
    m0 = rss_mb()
    w.id_edit.setText("long")
    w.start_record()
    yield until(recording, 15)
    yield sleep(60)
    m1 = rss_mb()
    w.stop_record()
    yield until(idle, 30)
    q = w.last[1]
    check(q.verdict == "ok" and q.duration_s > 59, f"60-s run: {q.verdict}, {q.fps_mean:.1f} fps, {q.gaps} gaps")
    check(m1 - m0 < 60, f"memory stable during 60-s run ({m0:.0f} -> {m1:.0f} MB)")

    # 5. language switch is ignored while recording
    yield until(ready, 15)
    w.id_edit.setText("9")
    w.start_record()
    yield until(recording, 15)
    before = G.i18n.LANG
    w.set_lang("zh" if before == "en" else "en")
    check(G.i18n.LANG == before, "language switch ignored while recording")

    # 6. camera lost mid-run
    yield sleep(1.5)
    w.sess.proc.kill()
    yield until(idle, 20)
    check(w.last[2] is True and w.last[0] is not None and w.last[0].exists(),
          "camera lost mid-run: partial run saved + flagged")
    yield until(lambda: w.sess is not None and w.sess.frame is not None, 15)
    check(w.sess.running, "preview restarted after the lost run")

    # 7. camera lost while idle
    yield sleep(1)
    w.sess.proc.kill()
    yield until(lambda: w.chk["live"][0] == "fail", 10)
    check(w.chk["live"][1] == "c_cam_lost", "idle camera loss shown as 'camera stopped'")
    w.refresh_cameras()
    yield until(live_ok, 20)
    check(True, "refresh brings the camera back")

    # 8. start failure reported fast (bogus device; its preview is already gone, so call start_record directly)
    real = w.cam
    w.cam = E.Camera("/dev/does-not-exist", "v4l2")
    t0 = time.monotonic()
    w.id_edit.setText("5")
    w.start_record()
    yield until(idle, 15)
    check(time.monotonic() - t0 < 5 and w.last[0] is None and w.last[1] == "start_fail",
          f"start failure reported in {time.monotonic() - t0:.1f} s, in plain words")
    check(not list((tmp / "data").rglob("005_*.part.mkv")), "failed start leaves no .part file")
    w.cam = real
    w.refresh_cameras()
    yield until(live_ok, 20)

    # 9. unwritable data folder blocks recording with a message
    ro = tmp / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    w.s["data_root"] = str(ro / "x")
    w.chk["computer"] = E.check_computer(w.s)
    w._render_checks()
    check(not w.rec_btn.isEnabled() and "c_folder_bad" in [k for _, k, _ in w.chk["computer"]],
          "unwritable folder: record disabled + explained")
    ro.chmod(0o700)
    w.s["data_root"] = str(tmp / "data")
    w.chk["computer"] = E.check_computer(w.s)
    w._render_checks()
    check(w.rec_btn.isEnabled(), "record re-enabled after fixing the folder")

    # 10. typing on a busy day (300 runs in the folder): every keystroke must be instant
    lat = []
    for txt in ["5", "50", "501", "5012", "501", "50", "5", "", "M", "M-", "M-7"] * 5:
        t0 = time.perf_counter()
        w.id_edit.setText(txt)
        lat.append(time.perf_counter() - t0)
    w.id_edit.setText("501")
    check(max(lat[1:]) < 0.010 and w.trials.count() == 10,
          f"keystroke with 300 runs in the folder: max {1000 * max(lat[1:]):.1f} ms, "
          f"list shows {w.trials.count()} runs of 501")

    # 11. idle soak: CPU, preview redraw rate, memory
    paints = []
    _orig_paint = w.view.paintEvent

    def _paint_spy(ev):
        paints.append(time.monotonic())
        _orig_paint(ev)

    w.view.paintEvent = _paint_spy
    yield until(live_ok, 20)
    ticks.clear()
    c0, m0, t0 = time.process_time(), rss_mb(), time.monotonic()
    yield sleep(120)
    dt = time.monotonic() - t0
    cpu = 100 * (time.process_time() - c0) / dt
    rate = len([p for p in paints if p >= t0]) / dt
    pause = longest_pause()
    check(cpu < 15, f"idle preview CPU (UI + readers, excl. ffmpeg): {cpu:.1f}% of one core")
    check(25 <= rate <= 35, f"preview redraws {rate:.1f}/s (camera preview stream is 30/s)")
    check(pause < 0.10, f"2-min idle: longest UI pause {1000 * pause:.0f} ms")
    check(rss_mb() - m0 < 10, f"2-min idle memory drift {rss_mb() - m0:+.1f} MB")
    w.grab().save(str(HERE / f"ui_stress_{G.i18n.LANG}.png"))
    w.set_lang("zh" if G.i18n.LANG == "en" else "en")
    yield sleep(1.5)
    w.grab().save(str(HERE / f"ui_stress_{G.i18n.LANG}.png"))

    # 12. another video mode through the Settings dialog: the demo camera also offers 640x400 at 240 fps
    yield until(ready, 15)
    s12 = dict(w.s)
    dlg = G.SettingsDialog(w, s12, w.cam, w.modes)
    check(dlg.size_box.count() == 2 and dlg.fps_box.itemData(dlg.fps_box.currentIndex()) == 120,
          "settings list the camera's own modes, saved 120 fps selected")
    dlg.size_box.setCurrentIndex(1)
    check(s12["width"] == 640 and s12["fps"] == 240 and dlg.fps_box.itemData(0) == 240
          and s12["exposure_ms"] <= 1000 / 240,
          f"640x400 -> {s12['fps']} fps offered first, exposure {s12['exposure_ms']} ms fits in a frame")
    dlg.accept()
    w.s.update(s12)
    w._select_camera(w.cam_box.currentIndex())
    yield until(lambda: w.sess is not None and live_ok(), 20)
    check(w.chk["camera"][1] == "c_cam_ok" and w.sess.live_fps > 0.95 * 240,
          f"240-fps preview live ({w.sess.live_fps:.0f} fps)")
    w.id_edit.setText("hs")
    w.start_record()
    yield until(recording, 15)
    yield sleep(3)
    w.stop_record()
    yield until(lambda: idle() and w.last[0] is not None, 20)
    q = w.last[1]
    m = json.loads(w.last[0].with_suffix(".json").read_text())
    check(q.verdict == "ok" and q.fps_mean > 0.97 * 240
          and (m["width"], m["height"], m["fps_target"]) == (640, 400, 240),
          f"240-fps run: {q.verdict}, {q.fps_mean:.1f} fps, {q.gaps} gaps, "
          f"saved as {m['width']}x{m['height']} @ {m['fps_target']}")


# ---- driver: runs the scenario generator from Qt timers (never blocks the event loop)
gen = scenario()


def step(cmd=None, t0=None):
    try:
        if cmd is None:
            cmd, t0 = next(gen), time.monotonic()
        kind, arg = cmd[0], cmd[1]
        if kind == "sleep":
            QTimer.singleShot(int(arg * 1000), lambda: step())
        elif arg():
            QTimer.singleShot(10, lambda: step())
        elif time.monotonic() - t0 > cmd[2]:
            check(False, f"TIMEOUT waiting ({w.state}, live={w.chk['live']})")
            app.quit()
        else:
            QTimer.singleShot(50, lambda: step(cmd, t0))
    except StopIteration:
        app.quit()


QTimer.singleShot(300, step)
app.exec()
w.close()
w._stop_session()
time.sleep(2)

# ---- invariants on disk
root = tmp / "data"
sess = next(root.glob("*_stress"))
vids = sorted([*sess.glob("*.mkv"), *sess.glob("_rejected/*.mkv")])
jsons = sorted([*sess.glob("*.json"), *sess.glob("_rejected/*.json")])
metas = [json.loads(j.read_text()) for j in jsons]
check(not list(root.rglob("*.part.mkv")), "no .part files left")
check({v.with_suffix("").name for v in vids} == {j.with_suffix("").name for j in jsons},
      f"every video has its JSON ({len(vids)} runs)")
rows = (sess / "trials.csv").read_text().strip().splitlines()[1:]
check(len(rows) == len(jsons), f"trials.csv rows = runs ({len(rows)})")
by = {}
for m in metas:
    by.setdefault(m["animal"], []).append(m["trial"])
check(all(sorted(v) == list(range(1, len(v) + 1)) for v in by.values()),
      f"trial numbers continuous per animal {dict((k, len(v)) for k, v in by.items() if not k.startswith('5'))}")
new = [m for m in metas if m["started"] != "x"]   # runs recorded by this test (not the busy-day fixtures)
verd = [m["qc"]["verdict"] for m in new]
nshort = sum(m["qc"].get("key") == "q_short" for m in metas)
check(all(m["qc"]["verdict"] == "ok" for m in new
          if m["qc"]["duration_s"] >= 1.2 and "disconnected" not in m["notes"]),
      f"all runs >= 1.2 s pass QC (verdicts: {dict((v, verd.count(v)) for v in set(verd))}, short {nshort})")
log = E.log_path().read_text()
check("UNCAUGHT" not in log, "no uncaught exceptions in the log")
procs = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
stray = [ln for ln in procs.splitlines() if "ffmpeg" in ln and ("testsrc2" in ln or str(tmp) in ln)]
check(not stray, f"no ffmpeg processes left behind {stray[:2]}")
print(f"\n{len(fails)} failure(s); data in {tmp}")
sys.exit(1 if fails else 0)
