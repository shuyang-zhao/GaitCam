"""GaitCam engine (no Qt): camera discovery and video modes, ffmpeg capture (MJPEG stream copy, no
re-encode), exposure lock, post-trial QC and file naming.

Design notes (see README.md):
  * Works with any UVC camera that offers MJPEG; the picture size and frame rate come from the camera's own
    mode list. On UVC cameras high frame rates usually exist only in MJPEG (the tested reference, an Arducam
    OV9281, does 120 fps at 1280x800 in MJPEG but 10 fps in YUY2), so we ask for the MJPEG stream (DirectShow
    pin on Windows, V4L2 input format on Linux) and store it as-is (-c:v copy) in Matroska, which keeps
    per-frame timestamps -> dropped frames are detectable afterwards.
  * Exposure: Windows UVC controls are only reachable through DirectShow (OpenCV's default MSMF backend
    cannot set them), so we open the camera briefly with cv2.CAP_DSHOW, set manual exposure, release it,
    then hand the device to ffmpeg.
  * --demo uses a synthetic ffmpeg source so the whole app can be exercised without a camera.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import weakref
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

APP_VERSION = "1.1.0"
IS_WIN = sys.platform == "win32"
BUNDLE_ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = 0x08000000 if IS_WIN else 0          # CREATE_NO_WINDOW: no console flashes on Windows

DEFAULTS = {
    "data_root": str(Path.home() / "Documents" / "GaitCam"),
    "experiment": "gait",
    "fps": 120,
    "width": 1280,
    "height": 800,
    "exposure_ms": 2.0,       # 2 ms freezes a swinging paw; needs a bright (DC-driven) light
    "camera": "",
}
FPS_STEPS = (240, 200, 150, 120, 100, 60, 30)  # offered below the camera's maximum for the chosen size
EXPOSURE_STOPS_MS = (0.5, 1.0, 2.0, 4.0, 8.0)   # DirectShow exposure is log2(seconds), integer steps
PREVIEW_W, PREVIEW_H, PREVIEW_FPS = 640, 400, 30
Mode = tuple[int, int, float]                   # (width, height, max_fps) of one MJPEG picture size
DEMO_MODES: list[Mode] = [(1280, 800, 120), (640, 400, 240)]


def _num(x: float) -> float | int:
    """Frame rates as the camera reports them (120.0002 -> 120), so labels and file metadata stay clean."""
    x = round(float(x), 2)
    return int(x) if x.is_integer() else x


def fps_choices(max_fps: float) -> list[float]:
    """The camera's maximum for the chosen size first, then common lower rates."""
    return [_num(max_fps)] + [f for f in FPS_STEPS if f < max_fps - 0.5][:4]


def exposure_stops(fps: float) -> list[float]:
    """Exposures that fit in one frame: a longer exposure makes the camera drop to a lower frame rate."""
    return [e for e in EXPOSURE_STOPS_MS if e <= 1000 / fps] or [EXPOSURE_STOPS_MS[0]]


# ---------------------------------------------------------------- settings
def settings_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    return base / "GaitCam" / "settings.json"


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(settings_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=2), encoding="utf-8")


def log_path() -> Path:
    return settings_path().parent / "gaitcam.log"


def log(msg: str) -> None:
    """Append-only diagnostics log (pythonw has no console). Rotated at 2 MB."""
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > 2_000_000:
            p.replace(p.with_suffix(".old.log"))
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- tools
def tool(name: str) -> str:
    """Bundled ffmpeg/ffprobe (bundle/ffmpeg/), else the Python environment's own, else PATH."""
    exe = f"{name}.exe" if IS_WIN else name
    for p in (BUNDLE_ROOT / "ffmpeg" / exe, Path(sys.prefix) / "bin" / exe, Path(sys.prefix) / "Library" / "bin" / exe):
        if p.exists():
            return str(p)
    return shutil.which(name) or name


def _run(cmd: list[str], timeout: float = 15) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    return r.stdout + r.stderr


# ---------------------------------------------------------------- cameras
@dataclass
class Camera:
    name: str
    kind: str                 # dshow | v4l2 | demo
    index: int = 0            # DirectShow enumeration index (same order for ffmpeg and OpenCV)
    alt: str | None = None    # DirectShow "alternative name" (unique device path)

    @property
    def label(self) -> str:
        return self.name


def list_cameras(demo: bool = False) -> list[Camera]:
    if demo:
        return [Camera("Demo camera (synthetic)", "demo")]
    if IS_WIN:
        out = _run([tool("ffmpeg"), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"])
        cams: list[Camera] = []
        section = None
        for ln in out.splitlines():
            if "DirectShow video devices" in ln:      # ffmpeg <= 5 layout
                section = "video"
            elif "DirectShow audio devices" in ln:
                section = "audio"
            m = re.search(r'"(.+?)"(?:\s*\((video|audio|none)\))?\s*$', ln)
            if not m or "Alternative name" in ln:
                alt = re.search(r'Alternative name\s+"(.+?)"', ln)
                if alt and cams and cams[-1].alt is None:
                    cams[-1].alt = alt.group(1)
                continue
            kind = m.group(2) or section
            if kind == "video":
                cams.append(Camera(m.group(1), "dshow", index=len(cams)))
        return cams
    return [Camera(p, "v4l2") for p in sorted(map(str, Path("/dev").glob("video*")))]


_MODES: dict[str, list[Mode]] = {}


def parse_mjpeg_modes(out: str) -> list[Mode] | None:
    """ffmpeg -list_options output -> [(width, height, max_fps)], one entry per size, largest picture first.
    None = no mode list at all (device busy / not listed); [] = listed, but the camera has no MJPEG."""
    best: dict[tuple[int, int], float] = {}
    for m in re.finditer(r"vcodec=mjpeg\s+min s=\d+x\d+ fps=[\d.]+\s+max s=(\d+)x(\d+) fps=([\d.]+)", out):
        size = (int(m.group(1)), int(m.group(2)))
        best[size] = max(best.get(size, 0.0), float(m.group(3)))
    if not best and not re.search(r"(pixel_format|vcodec)=", out):
        return None
    return sorted(((w, h, _num(f)) for (w, h), f in best.items()), key=lambda m: (-m[0] * m[1], -m[2]))


def list_mjpeg_modes(cam: Camera) -> list[Mode] | None:
    """(width, height, max_fps) for the MJPEG pin, see parse_mjpeg_modes. Cached per device."""
    if cam.kind == "demo":
        return list(DEMO_MODES)
    if cam.kind != "dshow":
        return None
    key = cam.alt or cam.name
    if key in _MODES:
        return _MODES[key]
    out = _run([tool("ffmpeg"), "-hide_banner", "-list_options", "true", "-f", "dshow",
                "-i", f"video={cam.alt or cam.name}"])
    modes = parse_mjpeg_modes(out)
    if modes is None:
        log(f"no mode list for {cam.name}: {' | '.join(out.strip().splitlines()[-3:])}")
    else:
        _MODES[key] = modes
        log(f"MJPEG modes of {cam.name}: {modes}")
    return modes


def pick_mode(modes: list[Mode] | None, s: dict) -> dict:
    """Settings changes for a newly selected camera. The saved picture size is kept when the camera offers it
    (a too-high frame rate is then reported by check_camera, not silently lowered); otherwise the fastest
    mode, largest picture on ties. The exposure is shortened to fit one frame."""
    out = {}
    if modes and not any((w, h) == (s["width"], s["height"]) for w, h, _ in modes):
        w, h, f = max(modes, key=lambda m: (m[2], m[0] * m[1]))
        out = {"width": w, "height": h, "fps": f}
    stops = exposure_stops(out.get("fps", s["fps"]))
    if s["exposure_ms"] > stops[-1]:
        out["exposure_ms"] = stops[-1]
    return out


def _dshow_open(cam: Camera, s: dict):
    import cv2                               # imported lazily: only needed on Windows
    cap = cv2.VideoCapture(cam.index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, s["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s["height"])
    cap.set(cv2.CAP_PROP_FPS, s["fps"])
    return cap


def apply_exposure(cam: Camera, s: dict) -> tuple[str, float | None]:
    """Lock manual exposure through DirectShow (UVC exposure lives on the camera, so it should survive
    handing the device to ffmpeg - to be confirmed on hardware). Returns (status, applied_ms):
    status ok | bad | na."""
    if cam.kind != "dshow":
        return "na", None
    try:
        import cv2
    except ImportError:
        return "bad", None
    cap = _dshow_open(cam, s)
    if cap is None:
        log("exposure: could not open camera through DirectShow")
        return "bad", None
    want = round(math.log2(s["exposure_ms"] / 1000.0))      # DirectShow units: log2(seconds)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)                  # manual
    cap.set(cv2.CAP_PROP_EXPOSURE, want)
    got = cap.get(cv2.CAP_PROP_EXPOSURE)
    cap.release()
    log(f"exposure: wanted log2 {want}, camera reports {got}")
    return ("ok", 1000 * 2 ** want) if got == want else ("bad", None)


def open_native_settings(cam: Camera, s: dict) -> None:
    """The camera's own DirectShow property page (fallback for anything we do not expose). Blocks."""
    if cam.kind != "dshow":
        return
    import cv2
    cap = _dshow_open(cam, s)
    if cap is not None:
        cap.set(cv2.CAP_PROP_SETTINGS, 1)
        cap.release()


# ---------------------------------------------------------------- capture session
class Session:
    """One ffmpeg process: counter/record output + a small gray preview on stdout."""
    _all: weakref.WeakSet[Session] = weakref.WeakSet()

    @classmethod
    def stop_all(cls) -> None:
        """App exit: no ffmpeg may outlive the window (it would keep the camera busy)."""
        for sess in list(cls._all):
            sess.stop(timeout=3)

    def __init__(self, cam: Camera, s: dict, out_path: Path | None = None):
        self.cam, self.s, self.out_path = cam, s, out_path
        self.frame: np.ndarray | None = None      # latest preview frame (PREVIEW_H x PREVIEW_W, uint8)
        self.frame_no = 0                          # increments per preview frame (UI repaints only on change)
        self.frames_in = 0                         # frames seen by the first output (= camera frames)
        self.t_first: float | None = None
        self.live_fps = 0.0
        self.log: list[str] = []
        self._hist: list[tuple[float, int]] = []
        cmd = self._cmd()
        log(f"start ffmpeg: {subprocess.list2cmdline(cmd)}")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=NO_WINDOW, bufsize=0)
        Session._all.add(self)
        threading.Thread(target=self._read_preview, daemon=True).start()
        threading.Thread(target=self._read_progress, daemon=True).start()

    def _cmd(self) -> list[str]:
        s, cam = self.s, self.cam
        size, fps = f"{s['width']}x{s['height']}", str(s["fps"])
        if cam.kind == "dshow":
            # no stream analysis (the DirectShow pin already states codec + size): opens the camera faster
            src = ["-f", "dshow", "-rtbufsize", "1024M", "-thread_queue_size", "1024", "-analyzeduration", "0",
                   "-probesize", "5000000", "-vcodec", "mjpeg", "-video_size", size, "-framerate", fps,
                   "-i", f"video={cam.alt or cam.name}"]
        elif cam.kind == "v4l2":
            src = ["-f", "v4l2", "-input_format", "mjpeg", "-video_size", size, "-framerate", fps,
                   "-i", cam.name]
        else:   # demo: real-time synthetic gray source
            src = ["-re", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps},format=gray"]
        # the synthetic source is raw video, so in demo mode we encode MJPEG; real cameras: stream copy
        venc = ["-c:v", "mjpeg", "-q:v", "3"] if cam.kind == "demo" else ["-c:v", "copy"]
        first = (["-map", "0:v", *venc, "-f", "matroska", str(self.out_path)] if self.out_path
                 else ["-map", "0:v", *venc, "-f", "null", "-"])
        preview = ["-map", "0:v", "-vf", f"fps={PREVIEW_FPS},scale={PREVIEW_W}:{PREVIEW_H},format=gray",
                   "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        return [tool("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-progress", "pipe:2",
                "-stats_period", "0.25", "-y", *src, *first, *preview]

    def _read_preview(self) -> None:
        n = PREVIEW_W * PREVIEW_H
        rd = self.proc.stdout
        while True:
            buf = bytearray(n)                      # new buffer per frame: the UI may still be drawing the last one
            view, got = memoryview(buf), 0
            while got < n:
                k = rd.readinto(view[got:])
                if not k:
                    return
                got += k
            self.frame = np.frombuffer(buf, np.uint8).reshape(PREVIEW_H, PREVIEW_W)
            self.frame_no += 1

    def _read_progress(self) -> None:
        for raw in iter(self.proc.stderr.readline, b""):
            ln = raw.decode("utf-8", "replace").strip()
            if ln.startswith("frame="):
                try:
                    f = int(ln.split("=", 1)[1])
                except ValueError:
                    continue
                now = time.monotonic()
                if f > 0 and self.t_first is None:
                    self.t_first = now
                self.frames_in = f
                self._hist = [(t, k) for t, k in self._hist if now - t < 1.5] + [(now, f)]
                if len(self._hist) > 1 and self._hist[-1][0] > self._hist[0][0]:
                    (t0, f0), (t1, f1) = self._hist[0], self._hist[-1]
                    self.live_fps = (f1 - f0) / (t1 - t0)
            elif ln and "=" not in ln and "deprecated pixel format" not in ln:
                self.log = (self.log + [ln])[-30:]

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float | None = None) -> int:
        # a recording needs time to finalise its file; a preview has nothing to save
        timeout = timeout if timeout is not None else (8 if self.out_path else 1.5)
        if self.running:
            try:
                self.proc.stdin.write(b"q")      # graceful: ffmpeg finalises the Matroska index
                self.proc.stdin.flush()
            except OSError:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        log(f"ffmpeg exit {self.proc.returncode}, frames {self.frames_in}" +
            (f"; last messages: {' | '.join(self.log[-3:])}" if self.log else ""))
        return self.proc.returncode


# ---------------------------------------------------------------- QC
@dataclass
class QC:
    frames: int = 0
    duration_s: float = 0.0
    fps_mean: float = 0.0
    gaps: int = 0               # frame intervals > 1.5x the nominal interval (= at least one frame lost)
    max_gap_ms: float = 0.0
    verdict: str = "fail"       # ok | warn | fail
    key: str = "q_empty"        # i18n message key
    params: dict = field(default_factory=dict)


def check_video(path: Path, target_fps: float) -> QC:
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    t = np.array([float(x.strip(",")) for x in out.split() if x.strip(",")
                  and x.strip(",").replace(".", "", 1).isdigit()])
    q = QC()
    if t.size < 2:
        log(f"QC {path.name}: no frames")
        return q
    t.sort()
    dt = np.diff(t)
    q.frames, q.duration_s = int(t.size), float(t[-1] - t[0])
    q.fps_mean = (t.size - 1) / q.duration_s if q.duration_s > 0 else 0.0
    nominal = 1.0 / target_fps
    q.gaps = int((dt > 1.5 * nominal).sum())
    q.max_gap_ms = float(dt.max() * 1000)
    gap_frac = q.gaps / max(1, dt.size)
    if q.duration_s < 1.0:
        q.verdict, q.key = "fail", "q_short"
    elif q.fps_mean >= 0.97 * target_fps and gap_frac <= 0.005:
        q.verdict, q.key = "ok", "q_ok"
    elif q.fps_mean >= 0.90 * target_fps and gap_frac <= 0.02:
        q.verdict, q.key = "warn", "q_warn"
    else:
        q.verdict, q.key = "fail", "q_slow"
        q.params = {"fps": round(q.fps_mean, 1), "target": int(target_fps)}
    ramp = brightness_ramp(path, target_fps)
    if ramp is not None and ramp < 0.75 and q.verdict == "ok":
        q.verdict, q.key, q.params = "warn", "q_exp_ramp", {}
    log(f"QC {path.name}: {q.verdict} {q.fps_mean:.2f} fps, {q.frames} frames, {q.gaps} gaps, "
        f"max gap {q.max_gap_ms:.1f} ms, start/settled brightness {ramp}")
    return q


def brightness_ramp(path: Path, fps: float = 120) -> float | None:
    """Mean brightness of the first 3 frames / of 0.375-0.5 s in (frames 45-60 at 120 fps). Real-camera test
    2026-10-06: 0.6 on 2 of 3 runs (39 -> 64 within half a second, same scene) = automatic exposure / gain
    still converging -> warn."""
    try:
        import cv2
    except ImportError:
        return None
    a, b = round(0.375 * fps), round(0.5 * fps)
    cap = cv2.VideoCapture(str(path))
    vals = []
    for i in range(b + 1):
        ok, f = cap.read()
        if not ok:
            break
        if i < 3 or i >= a:
            vals.append((i, float(f.mean())))
    cap.release()
    head = [v for i, v in vals if i < 3]
    tail = [v for i, v in vals if i >= a]
    if not head or not tail or np.mean(tail) < 5:
        return None
    return round(float(np.mean(head) / np.mean(tail)), 3)


# ---------------------------------------------------------------- naming & organisation
def clean_id(s: str) -> str:
    """Animal ID: letters, digits and '-' only; purely numeric IDs are zero-padded to 3 digits."""
    s = re.sub(r"[^A-Za-z0-9-]", "", s.strip())[:24]
    return s.zfill(3) if s.isdigit() else s


def clean_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s.strip()).strip("-") or "gait"


def session_dir(s: dict, day: datetime | None = None) -> Path:
    day = day or datetime.now()
    return Path(s["data_root"]) / f"{day:%Y-%m-%d}_{clean_name(s['experiment'])}"


def next_trial(sess: Path, animal: str) -> int:
    pat = re.compile(rf"^{re.escape(animal)}_t(\d+)_")
    used = [int(m.group(1)) for p in [*sess.glob("*.mkv"), *sess.glob("_rejected/*.mkv"),
                                      *sess.glob("*.part.mkv")]
            if (m := pat.match(p.name))]
    return max(used, default=0) + 1


def trial_stem(animal: str, trial: int, when: datetime) -> str:
    return f"{animal}_t{trial:02d}_{when:%Y%m%d-%H%M%S}"


@dataclass
class TrialRecord:
    file: str
    animal: str
    trial: int
    started: str
    camera: str
    width: int
    height: int
    fps_target: float
    exposure_ms: float
    notes: str = ""
    status: str = "kept"           # kept | rejected
    app_version: str = APP_VERSION
    qc: dict = field(default_factory=dict)


def finalize(part: Path, rec: TrialRecord, q: QC) -> Path:
    """.part.mkv -> final name, write the JSON sidecar, rebuild the session log."""
    final = part.with_name(part.name.replace(".part.mkv", ".mkv"))
    k = 1
    while final.exists():                       # never overwrite
        final = part.with_name(part.name.replace(".part.mkv", f"_{k}.mkv"))
        k += 1
    part.replace(final)
    rec.file, rec.qc = final.name, asdict(q)
    final.with_suffix(".json").write_text(json.dumps(asdict(rec), indent=2), encoding="utf-8")
    rebuild_log(final.parent)
    return final


def reject(video: Path) -> Path:
    """Move a trial (video + sidecar) into _rejected/ - nothing is ever deleted."""
    dst = video.parent / "_rejected"
    dst.mkdir(exist_ok=True)
    side = video.with_suffix(".json")
    if side.exists():
        meta = json.loads(side.read_text(encoding="utf-8"))
        meta["status"] = "rejected"
        (dst / side.name).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        side.unlink()
    target = dst / video.name
    video.replace(target)
    rebuild_log(video.parent)
    return target


_META: dict[str, tuple[int, dict]] = {}


def read_meta(p: Path) -> dict | None:
    """Sidecar JSON, cached by modification time: after a run only the new file is actually read."""
    try:
        mt = p.stat().st_mtime_ns
        hit = _META.get(str(p))
        if hit and hit[0] == mt:
            return hit[1]
        m = json.loads(p.read_text(encoding="utf-8"))
        _META[str(p)] = (mt, m)
        return m
    except (OSError, ValueError):
        return None


def rebuild_log(sess: Path) -> None:
    """trials.csv is regenerated from the JSON sidecars (single source of truth)."""
    rows = []
    for p in [*sess.glob("*.json"), *sess.glob("_rejected/*.json")]:
        m = read_meta(p)
        if m is None:
            continue
        q = m.get("qc", {})
        rows.append([m["file"], m["animal"], m["trial"], m["started"], m["status"], q.get("verdict", ""),
                     f"{q.get('fps_mean', 0):.2f}", m["fps_target"], q.get("frames", 0),
                     f"{q.get('duration_s', 0):.2f}", q.get("gaps", 0), f"{q.get('max_gap_ms', 0):.1f}",
                     m["exposure_ms"], m["width"], m["height"], m["camera"], m.get("notes", "")])
    rows.sort(key=lambda r: (str(r[1]), int(r[2])))
    head = ["file", "animal", "trial", "started", "status", "qc", "fps_mean", "fps_target", "frames",
            "duration_s", "gaps", "max_gap_ms", "exposure_ms", "width", "height", "camera", "notes"]
    with open(sess / "trials.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        w.writerows(rows)


def session_trials(sess: Path, animal: str | None = None) -> list[dict]:
    out = []
    for p in sorted(sess.glob("*.json")):
        m = read_meta(p)
        if m is None:
            continue
        if animal is None or m.get("animal") == animal:
            out.append(m)
    return sorted(out, key=lambda m: (m["animal"], m["trial"]))


# ---------------------------------------------------------------- self-checks (level 1 = computer, 2 = camera)
_TOOLS_OK = False
MB_PER_S = 5.5          # measured on the reference camera (1280x800 MJPEG, 120 fps: ~40 Mbit/s); for free-space hints


def mb_per_s(s: dict) -> float:
    """Rough data rate of the chosen mode: the measured one, scaled by pixels per second."""
    return MB_PER_S * s["width"] * s["height"] * s["fps"] / (1280 * 800 * 120)


def check_computer(s: dict) -> list[tuple[str, str, dict]]:
    """[(status ok|warn|fail, i18n key, params)]"""
    global _TOOLS_OK
    out = []
    if not _TOOLS_OK:                          # launching ffmpeg twice costs ~0.3 s on Windows: once per app run
        try:
            _TOOLS_OK = all(subprocess.run([tool(n), "-version"], capture_output=True, timeout=10,
                                           creationflags=NO_WINDOW).returncode == 0 for n in ("ffmpeg", "ffprobe"))
        except (OSError, subprocess.SubprocessError):
            _TOOLS_OK = False
    out.append(("ok", "c_ffmpeg_ok", {}) if _TOOLS_OK else ("fail", "c_ffmpeg_bad", {}))
    root = Path(s["data_root"])
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".gaitcam_write_test"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        out.append(("fail", "c_folder_bad", {"path": str(root)}))
        return out
    free_gb = shutil.disk_usage(root).free / 1e9
    minutes = free_gb * 1000 / mb_per_s(s) / 60
    if free_gb < 5:
        out.append(("fail" if free_gb < 1 else "warn", "c_disk_low", {"gb": free_gb, "min": minutes}))
    else:
        out.append(("ok", "c_disk_ok", {"gb": free_gb, "h": minutes / 60}))
    if "onedrive" in str(root).lower():
        out.append(("warn", "c_onedrive", {}))
    return out


def check_camera(cam: Camera | None, modes: list[Mode] | None, s: dict) -> tuple[str, str, dict]:
    if cam is None:
        return "fail", "c_no_camera", {}
    if modes is None:
        return "ok", "c_cam_unknown", {"name": cam.name}
    if not modes:
        return "fail", "c_no_mjpeg", {}
    w, h = s["width"], s["height"]
    hit = [m for m in modes if (m[0], m[1]) == (w, h)]
    if not hit:
        return "fail", "c_mode_missing", {"w": w, "h": h}
    mx = max(m[2] for m in hit)
    if mx < s["fps"] - 0.5:
        return "warn", "c_fps_cap", {"max": mx}
    return "ok", "c_cam_ok", {"name": cam.name, "w": w, "h": h, "max": mx}


def recover_parts(s: dict, older_than_s: float = 10) -> int:
    """Finish .part.mkv files left by a crash / power cut: QC + finalize with a note. Returns how many."""
    n = 0
    root = Path(s["data_root"])
    if not root.exists():
        return 0
    for part in [*root.glob("*/*.part.mkv"), *root.glob("*/calib/*.part.mkv")]:
        if time.time() - part.stat().st_mtime < older_than_s:
            continue
        m = re.match(r"^(.+)_t(\d+)_(\d{8}-\d{6})\.part\.mkv$", part.name)
        if not m:
            continue
        rec = TrialRecord(file="", animal=m.group(1), trial=int(m.group(2)),
                          started=datetime.strptime(m.group(3), "%Y%m%d-%H%M%S").isoformat(),
                          camera="unknown", width=s["width"], height=s["height"], fps_target=s["fps"],
                          exposure_ms=s["exposure_ms"], notes="recovered: app closed before the run was finished")
        try:
            finalize(part, rec, check_video(part, s["fps"]))
            n += 1
        except OSError as exc:
            log(f"recover {part}: {exc}")
    if n:
        log(f"recovered {n} unfinished recording(s)")
    return n


def session_index(sess: Path) -> dict:
    """{"rows": {animal: [meta, ...]}, "used": {animal: {trial numbers incl. rejected / unfinished}}}.
    Built once per folder change (the UI looks things up per keystroke)."""
    rows, used = {}, {}
    for m in session_trials(sess) if sess.exists() else []:
        rows.setdefault(m["animal"], []).append(m)
    pat = re.compile(r"^(.+?)_t(\d+)_\d{8}-\d{6}")
    if sess.exists():
        for p in [*sess.glob("*.mkv"), *sess.glob("_rejected/*.mkv")]:
            if (m := pat.match(p.name)):
                used.setdefault(m.group(1), set()).add(int(m.group(2)))
    return {"rows": rows, "used": used}
