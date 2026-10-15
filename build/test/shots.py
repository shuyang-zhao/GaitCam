"""Screenshots of the UI states (demo camera): idle EN, recording, after a run in ZH, short window.
Written next to this script as shot_*.png."""
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="gaitcam_shots_"))
os.environ["APPDATA"] = str(tmp)                   # settings + log go to the temp folder, not the user's
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
out = Path(__file__).resolve().parent
sys.path.insert(0, str(out.parents[1] / "app"))
import engine as E  # noqa: E402

s = E.load_settings()
s["data_root"] = str(tmp / "data")
s["experiment"] = "gait_pilot"
E.save_settings(s)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import gaitcam as G  # noqa: E402

app = QApplication(sys.argv)
app.setStyleSheet(G.QSS)
w = G.Main(demo=True)
w.show()


def shot(name):
    app.processEvents()             # apply pending re-layouts first (e.g. the file-name label after setText)
    w.grab().save(str(out / name))


# (delay in ms, action): the delays leave time for the demo camera to connect / a run to be checked
seq = [(3500, lambda: (w.id_edit.setText("42"), shot("shot_idle_en.png"), w.start_record())),
       (3500, lambda: (shot("shot_recording_en.png"), w.stop_record())),
       (3000, lambda: w.set_lang("zh")),
       (1500, lambda: (shot("shot_after_zh.png"), w.resize(1100, 700))),
       (1200, lambda: (shot("shot_small_zh.png"), w.close(), app.quit()))]


def run(i=0):
    if i < len(seq):
        QTimer.singleShot(seq[i][0], lambda: (seq[i][1](), run(i + 1)))


run()
app.exec()
