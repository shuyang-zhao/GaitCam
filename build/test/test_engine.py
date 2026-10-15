"""Engine unit tests: naming, numbering, QC verdicts (incl. synthetic frame gaps), reject, crash recovery,
video modes, i18n completeness. Plain script: python build/test/test_engine.py (exit code 1 on any failure)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="gaitcam_unit_"))
os.environ["APPDATA"] = str(tmp)                   # settings + log go to the temp folder, not the user's
APP = Path(__file__).resolve().parents[2] / "app"
sys.path.insert(0, str(APP))
import engine as E  # noqa: E402
import i18n  # noqa: E402

fails = []


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        fails.append(what)


# --- IDs
for raw, want in [("42", "042"), (" 7 ", "007"), ("1234", "1234"), ("M-12", "M-12"), ("m 12!", "m12"),
                  ("小鼠", ""), ("", ""), ("x" * 50, "x" * 24), ("calib", "calib"), ("0", "000")]:
    check(E.clean_id(raw) == want, f"clean_id({raw!r}) -> {E.clean_id(raw)!r} (want {want!r})")
check(E.clean_name("gait pilot!") == "gait-pilot" and E.clean_name("   ") == "gait", "clean_name")

# --- synthetic clips: clean / with gaps / slow / short / brightness ramp
ff = E.tool("ffmpeg")


def make(name, dur, fps=120, keep=None, fade=None):
    """MJPEG test clip; keep = frame-select expression (drops frames), fade = fade-in length in frames."""
    out = tmp / name
    vf = f"select='{keep}'" if keep else f"fade=in:0:{fade}" if fade else "null"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x200:rate={fps}:duration={dur}",
                    "-vf", vf, "-fps_mode", "passthrough", "-c:v", "mjpeg", "-f", "matroska", str(out)], check=True)
    return out


q = E.check_video(make("clean.mkv", 3), 120)
check(q.verdict == "ok" and q.gaps == 0, f"QC clean -> {q.verdict} {q.fps_mean:.1f}")
q = E.check_video(make("gappy.mkv", 3, keep="not(eq(mod(n\\,200)\\,5))"), 120)
check(q.verdict == "warn" and q.gaps >= 1, f"QC 0.5% dropped -> {q.verdict} gaps={q.gaps}")
q = E.check_video(make("lossy.mkv", 3, keep="mod(n\\,3)"), 120)
check(q.verdict == "fail" and q.key == "q_slow", f"QC 1/3 dropped -> {q.verdict} {q.fps_mean:.0f} fps")
q = E.check_video(make("slow.mkv", 3, fps=60), 120)
check(q.verdict == "fail", f"QC 60 fps vs 120 -> {q.verdict}")
q = E.check_video(make("short.mkv", 0.5), 120)
check(q.verdict == "fail" and q.key == "q_short", "QC <1 s -> short")
# auto-exposure ramp (real camera 2026-10-06: first frames ~0.6x the settled brightness) -> warn
q = E.check_video(make("ramp.mkv", 3, fade=50), 120)
check(q.verdict == "warn" and q.key == "q_exp_ramp", f"QC brightness ramp -> {q.verdict} {q.key}")
check(E.brightness_ramp(tmp / "clean.mkv") > 0.9, "steady clip: no ramp")

# higher frame rates: the same QC at 240 fps (gap threshold and ramp window scale with the rate)
q = E.check_video(make("clean240.mkv", 3, fps=240), 240)
check(q.verdict == "ok" and q.gaps == 0, f"QC 240 fps clean -> {q.verdict} {q.fps_mean:.1f}")
q = E.check_video(make("gappy240.mkv", 3, fps=240, keep="not(eq(mod(n\\,200)\\,5))"), 240)
check(q.verdict == "warn" and q.gaps >= 1, f"QC 240 fps, 0.5% dropped -> {q.verdict} gaps={q.gaps}")
q = E.check_video(make("half240.mkv", 3, fps=240, keep="mod(n\\,2)"), 240)
check(q.verdict == "fail" and q.key == "q_slow",
      f"QC 240 fps, every 2nd frame lost -> {q.verdict} {q.fps_mean:.0f} fps")
q = E.check_video(make("ramp240.mkv", 3, fps=240, fade=100), 240)
check(q.key == "q_exp_ramp", f"QC 240 fps brightness ramp (same 0.4 s) -> {q.key}")
(tmp / "empty.mkv").write_bytes(b"")
q = E.check_video(tmp / "empty.mkv", 120)
check(q.key == "q_empty", "QC empty file")

# --- numbering, finalize (no overwrite), reject, log
s = dict(E.DEFAULTS, data_root=str(tmp / "data"), experiment="t")
sess = E.session_dir(s)
sess.mkdir(parents=True)
check(E.next_trial(sess, "042") == 1, "next_trial empty")


def finalize_copy(part, animal, n, started):
    """A finished run: the clean test clip copied to `part`, then finalized like the app does."""
    shutil.copy(tmp / "clean.mkv", part)
    rec = E.TrialRecord("", animal, n, started, "demo", 1280, 800, 120, 2.0)
    return E.finalize(part, rec, E.check_video(part, 120))


def fake_trial(animal, n):
    part = sess / f"{E.trial_stem(animal, n, datetime.now())}.part.mkv"
    return finalize_copy(part, animal, n, "2026-01-01T00:00:00")


fake_trial("042", 1)
b = fake_trial("042", 2)
check(E.next_trial(sess, "042") == 3, "next_trial after 2")
E.reject(b)
check(E.next_trial(sess, "042") == 3, "rejected trial number is not reused")
check((sess / "_rejected" / b.name).exists() and not b.exists(), "reject moves video")
side = sess / "_rejected" / b.with_suffix(".json").name
check(json.loads(side.read_text())["status"] == "rejected", "reject marks json")
rows = (sess / "trials.csv").read_text().strip().splitlines()
check(len(rows) == 3 and ",rejected," in rows[2], "trials.csv has kept + rejected rows")
check(E.next_trial(sess, "04") == 1 and E.next_trial(sess, "0421") == 1, "prefix IDs do not collide")
# same-second collision -> suffix, never overwrite
f1 = finalize_copy(sess / "099_t01_20260101-000000.part.mkv", "099", 1, "x")
f2 = finalize_copy(sess / "099_t01_20260101-000000.part.mkv", "099", 1, "x")
check(f1 != f2 and f1.exists() and f2.exists(), f"finalize never overwrites ({f2.name})")

# --- crash recovery
left = sess / "043_t01_20260101-120000.part.mkv"
shutil.copy(tmp / "clean.mkv", left)
os.utime(left, (time.time() - 60, time.time() - 60))
n = E.recover_parts(s)
rec = sess / "043_t01_20260101-120000.mkv"
check(n == 1 and rec.exists() and "recovered" in json.loads(rec.with_suffix(".json").read_text())["notes"],
      "recover_parts")
fresh = sess / "044_t01_20260101-120001.part.mkv"
shutil.copy(tmp / "clean.mkv", fresh)
check(E.recover_parts(s) == 0 and fresh.exists(), "recover_parts leaves a file that is still being written")

# --- computer checks
check(all(st == "ok" for st, k, _ in E.check_computer(s) if k.startswith("c_ffmpeg")), "ffmpeg/ffprobe found")
ro = tmp / "readonly"
ro.mkdir()
ro.chmod(0o500)
s2 = dict(s, data_root=str(ro / "sub"))
check(any(st == "fail" and k == "c_folder_bad" for st, k, _ in E.check_computer(s2)), "unwritable folder -> fail")
ro.chmod(0o700)
onedrive = dict(s, data_root=str(tmp / "OneDrive" / "x"))
check(any(k == "c_onedrive" for _, k, _ in E.check_computer(onedrive)), "OneDrive warning")

# --- camera checks
check(E.check_camera(None, [], s)[0] == "fail", "no camera -> fail")
cam = E.Camera("cam", "dshow")
check(E.check_camera(cam, [(1280, 800, 100)], s)[1] == "c_fps_cap", "100-fps firmware -> fps cap warning")
check(E.check_camera(cam, [(640, 480, 120)], s)[1] == "c_mode_missing", "saved size not offered -> fail")
check(E.check_camera(cam, [], s)[1] == "c_no_mjpeg", "camera without MJPEG -> fail")
check(E.check_camera(cam, None, s)[1] == "c_cam_unknown", "no mode list -> connected, unknown")
s240 = dict(s, width=640, height=400, fps=240)
check(E.check_camera(cam, [(640, 400, 240)], s240)[1] == "c_cam_ok", "240 fps mode -> ok")
check(E.mb_per_s(s240) == E.MB_PER_S / 2, "data rate scales with pixels per second")

# --- camera modes: ffmpeg 8 dshow -list_options layout (one size can be listed several times)
LIST = """[dshow @ 0000] DirectShow video device options (from video devices)
[dshow @ 0000]  Pin "Capture" (alternative pin name "0")
[dshow @ 0000]   vcodec=mjpeg  min s=1280x800 fps=5 max s=1280x800 fps=120.0002
[dshow @ 0000]   vcodec=mjpeg  min s=1280x800 fps=5 max s=1280x800 fps=120.0002 (pc, bt470bg/bt709/unknown, center)
[dshow @ 0000]   vcodec=mjpeg  min s=640x400 fps=5 max s=640x400 fps=210
[dshow @ 0000]   vcodec=mjpeg  min s=640x400 fps=5 max s=640x400 fps=100
[dshow @ 0000]   pixel_format=yuyv422  min s=1280x800 fps=10 max s=1280x800 fps=10
"""
modes = E.parse_mjpeg_modes(LIST)
check(modes == [(1280, 800, 120), (640, 400, 210)], f"parse MJPEG modes -> {modes}")
check(E.parse_mjpeg_modes(LIST.replace("vcodec=mjpeg", "pixel_format=nv12")) == [], "camera without MJPEG -> []")
check(E.parse_mjpeg_modes("Could not enumerate video device options.") is None, "no list -> None")
check(E.pick_mode(modes, s) == {}, "saved size offered -> keep the saved mode")
s4k = dict(s, width=3840, height=2160, fps=30, exposure_ms=8.0)
check(E.pick_mode(modes, s4k) == {"width": 640, "height": 400, "fps": 210, "exposure_ms": 4.0},
      f"new camera -> fastest mode, exposure shortened to fit a frame: {E.pick_mode(modes, s4k)}")
check(E.pick_mode([(1920, 1080, 60), (1280, 720, 60)], s4k) == {"width": 1920, "height": 1080, "fps": 60},
      "tie on frame rate -> larger picture")
check(E.fps_choices(120) == [120, 100, 60, 30] and E.fps_choices(330) == [330, 240, 200, 150, 120],
      f"frame-rate choices {E.fps_choices(120)} / {E.fps_choices(330)}")
check(E.exposure_stops(120)[-1] == 8.0 and E.exposure_stops(240)[-1] == 4.0 and E.exposure_stops(500)[-1] == 2.0
      and E.exposure_stops(5000) == [0.5], "exposure stops fit in one frame")

# --- i18n completeness: every key has both languages with the same placeholders (reported only when broken),
#     and every key the UI uses exists
for k, (en, zh) in i18n.S.items():
    if not en or not zh or en.count("{") != zh.count("{"):
        check(False, f"i18n {k}")
used = set(re.findall(r'(?<![A-Za-z_.])t\("([a-z_0-9]+)"', (APP / "gaitcam.py").read_text()))
used |= {"q_ok", "q_warn", "q_slow", "q_short", "q_empty"} | {k for _, k, _ in E.check_computer(s)}
check(not (used - set(i18n.S)), f"all used i18n keys exist {sorted(used - set(i18n.S))}")

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
