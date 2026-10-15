"""Which UI-thread handler is slow during start/stop? Times every handler over 10 cycles (demo camera) and
samples the main thread's stack whenever the event loop stalls for more than 60 ms."""
import collections
import functools
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="gaitcam_prof_"))
os.environ["APPDATA"] = str(tmp)                   # settings + log go to the temp folder, not the user's
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
import engine as E  # noqa: E402

s = E.load_settings()
s["data_root"] = str(tmp / "data")
E.save_settings(s)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import gaitcam as G  # noqa: E402
# as in production: OpenCV is preloaded by gaitcam.run() behind the splash
import cv2  # noqa: E402,F401

app = QApplication(sys.argv)
app.setStyleSheet(G.QSS)
w = G.Main(demo=True)
w.show()

# ---- per-handler timing: [calls, total s, max s]
stats = {}


def wrap(name):
    f = getattr(w, name)

    @functools.wraps(f)
    def g(*a, **k):
        t0 = time.perf_counter()
        r = f(*a, **k)
        d = time.perf_counter() - t0
        st = stats.setdefault(name, [0, 0.0, 0.0])
        st[0] += 1
        st[1] += d
        st[2] = max(st[2], d)
        return r

    setattr(w, name, g)


for n in ["_on_job", "_on_preview", "_tick", "start_record", "stop_record", "_set_rec_button", "_render_checks",
          "_fields_changed", "_render_last", "_done_stop", "_done_record"]:
    wrap(n)
# signals were connected to the unwrapped methods in Main.__init__: reconnect them to the timed ones
w.bus.done.disconnect()
w.bus.done.connect(w._on_job)
w.bus.preview.disconnect()
w.bus.preview.connect(w._on_preview)

# ---- event-loop gaps: every UI-timer tick is recorded; a watchdog thread samples stacks during stalls
ticks, last_tick = [], [time.monotonic()]


def tick_spy():
    last_tick[0] = time.monotonic()
    ticks.append(last_tick[0])
    w._tick()


w.timer.timeout.disconnect()
w.timer.timeout.connect(tick_spy)
main_id = threading.main_thread().ident
samples = collections.Counter()


def where(st):
    return f"{st.name}:{os.path.basename(st.filename)}:{st.lineno}"


def watchdog():
    while True:
        time.sleep(0.01)
        if time.monotonic() - last_tick[0] > 0.06:
            fr = sys._current_frames().get(main_id)
            stk = traceback.extract_stack(fr) if fr else []
            sig = " <- ".join(where(f) for f in stk[-4:][::-1]) or "(no Python frame: inside Qt/C++)"
            others = []                            # what are the other threads doing?
            for tid, f2 in sys._current_frames().items():
                if tid != main_id:
                    st2 = traceback.extract_stack(f2)
                    if st2 and "watchdog" not in st2[-1].name:
                        others.append(where(st2[-1]))
            samples[(sig, tuple(sorted(set(others))))] += 1


threading.Thread(target=watchdog, daemon=True).start()

# ---- 10 start/stop cycles, then the report
cyc = [0]


def loop():
    if w.state == "idle" and w.rec_btn.isEnabled():
        if cyc[0] >= 10:
            g = [b - a for a, b in zip(ticks, ticks[1:])]
            print(f"longest event-loop pause {1000 * max(g):.0f} ms")
            for k, (n, tot, mx) in sorted(stats.items(), key=lambda x: -x[1][2]):
                print(f"  {k:<16} calls {n:5d}  total {1000 * tot:7.1f} ms  max {1000 * mx:6.1f} ms")
            print("stall samples (10 ms each): main thread | other threads")
            for (sig, oth), n in samples.most_common(8):
                print(f"  {n:4d}  {sig[:110]}\n        others: {', '.join(oth)[:240]}")
            app.quit()
            return
        w.id_edit.setText("7")
        w.start_record()
        cyc[0] += 1
    elif w.state == "recording" and time.monotonic() - w.sess.t_first > 1.0:
        w.stop_record()
    QTimer.singleShot(20, loop)


QTimer.singleShot(2500, loop)
app.exec()
w.close()
