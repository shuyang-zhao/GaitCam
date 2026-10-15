"""GaitCam UI - a one-window recorder for high-speed bottom-view gait videos from a UVC MJPEG camera
(the camera's MJPEG stream is saved without re-encoding; see engine.py).

    python main.py             # normal start, with a splash (the Windows bundle's GaitCam.bat runs this)
    python main.py --demo      # synthetic camera, for trying the app without hardware
    python gaitcam.py [--demo] # the same window without the splash
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRect, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget)

import engine as E
import i18n
from i18n import t

CALIB_DIR = E.BUNDLE_ROOT / "calibration"          # printable calibration targets, shipped next to app/

# Material 3 (Google) palette
PRIMARY, ON_PRIMARY, PRIMARY_CONT, ON_PRIMARY_CONT = "#0B57D0", "#FFFFFF", "#D3E3FD", "#041E49"
SURFACE, SURFACE_LOW, OUTLINE, ON_SURFACE, ON_VARIANT = "#F8FAFD", "#FFFFFF", "#C4C7C5", "#1F1F1F", "#444746"
STOP_BG = "#B3261E"
BADGE = {"ok": ("#146C2E", "#C4EED0", "✓"), "warn": ("#7D5700", "#FFDEA6", "!"),
         "fail": ("#8C1D18", "#F9DEDC", "✕"), "wait": ("#444746", "#E3E3E3", "…")}
QSS = f"""
* {{ font-family: "Google Sans Text", "Google Sans", "Segoe UI Variable Text", "Segoe UI", Roboto, Arial;
     font-size: 14px; color: {ON_SURFACE}; }}
QMainWindow, QDialog {{ background: {SURFACE}; }}
QFrame#card {{ background: {SURFACE_LOW}; border: 1px solid #E1E3E1; border-radius: 24px; }}
QFrame#preview {{ background: #1F1F1F; border-radius: 24px; }}
QLabel#appname {{ font-family: "Google Sans", "Segoe UI Variable Display", "Segoe UI"; font-size: 22px; font-weight: 600; }}
QLabel#logo {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4285F4, stop:0.5 #34A853, stop:1 #FBBC04);
               border-radius: 9px; min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; }}
QLabel#h {{ font-size: 13px; font-weight: 600; color: {ON_VARIANT}; }}
QLabel#muted {{ color: {ON_VARIANT}; font-size: 12px; }}
QLabel#mono {{ font-family: "Cascadia Mono", Consolas, "Roboto Mono", monospace; font-size: 12px; color: {ON_VARIANT}; }}
QLabel#timer {{ font-family: "Google Sans", "Segoe UI Variable Display", "Segoe UI"; font-size: 34px; font-weight: 500; }}
QLabel#badge {{ border-radius: 12px; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px;
                font-weight: 700; font-size: 13px; }}
QLabel#pill {{ border-radius: 8px; padding: 4px 10px; font-size: 12px; font-weight: 600; }}
QLabel#chip {{ background: #2D2F31; color: #E3E3E3; border-radius: 8px; padding: 4px 10px; font-size: 12px; }}
QLabel#placeholder {{ color: #C4C7C5; font-size: 15px; }}
QLineEdit, QComboBox {{ background: {SURFACE_LOW}; border: 1px solid {OUTLINE}; border-radius: 12px; padding: 9px 12px; }}
QLineEdit:focus, QComboBox:focus {{ border: 2px solid {PRIMARY}; padding: 8px 11px; }}
QLineEdit:disabled, QComboBox:disabled {{ color: #9AA0A6; background: #F1F3F4; }}
QLineEdit#big {{ font-family: "Google Sans", "Segoe UI Variable Display", "Segoe UI"; font-size: 24px; font-weight: 500; padding: 11px 14px; }}
QLineEdit#big:focus {{ padding: 10px 13px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QPushButton {{ background: transparent; border: 1px solid {OUTLINE}; border-radius: 17px; padding: 6px 18px;
               min-height: 22px; color: {PRIMARY}; font-weight: 600; }}
QPushButton:hover {{ background: #EDF2FA; }}
QPushButton:disabled {{ color: #9AA0A6; border-color: #E3E3E3; }}
QPushButton#tonal {{ background: {PRIMARY_CONT}; color: {ON_PRIMARY_CONT}; border: none; }}
QPushButton#tonal:hover {{ background: #C2D7FA; }}
QPushButton#filled {{ background: {PRIMARY}; color: {ON_PRIMARY}; border: none; }}
QPushButton#filled:hover {{ background: #0A4FBF; }}
QPushButton#text {{ border: none; border-radius: 10px; padding: 6px 6px; text-align: left; }}
QPushButton#text:hover {{ background: #EDF2FA; border-radius: 10px; }}
QPushButton#fab {{ background: {PRIMARY}; color: {ON_PRIMARY}; border: none; border-radius: 20px;
                   font-family: "Google Sans", "Segoe UI Variable Display", "Segoe UI"; font-size: 18px; font-weight: 600;
                   min-height: 64px; }}
QPushButton#fab:hover {{ background: #0A4FBF; }}
QPushButton#fab[recording="true"] {{ background: {STOP_BG}; }}
QPushButton#fab:disabled {{ background: #E3E3E3; color: #9AA0A6; }}
QPushButton#langchip {{ border: 1px solid {OUTLINE}; border-radius: 15px; padding: 4px 14px; min-height: 22px;
                        color: {ON_SURFACE}; font-weight: 500; }}
QPushButton#langchip:checked {{ background: {PRIMARY_CONT}; color: {ON_PRIMARY_CONT}; border-color: {PRIMARY_CONT}; }}
QListWidget {{ border: none; background: transparent; }}
QListWidget::item {{ padding: 6px 4px; border-radius: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {PRIMARY_CONT}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PRIMARY}; width: 20px; height: 20px; margin: -8px 0; border-radius: 10px; }}
QWidget#sidebar, QWidget#sideviewport, QScrollArea#sidescroll {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: #C4C7C5; border-radius: 4px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: #9AA0A6; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: none; }}
QToolTip {{ background: #303030; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}
"""


def card(*items, spacing=12, margins=20) -> QFrame:
    f = QFrame(objectName="card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    for w in items:
        lay.addWidget(w) if isinstance(w, QWidget) else lay.addLayout(w)
    return f


def label(text="", name=None, wrap=False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def set_text(lb: QLabel, text: str) -> None:
    if lb.text() != text:                  # setText on an unchanged label still re-lays-out its parents
        lb.setText(text)


def paint_pill(lb: QLabel, status: str, text: str) -> None:
    if getattr(lb, "_painted", None) == (status, text):
        return
    lb._painted = (status, text)
    fg, bg, _ = BADGE[status]
    lb.setText(text)
    lb.setStyleSheet(f"background: {bg}; color: {fg};")     # re-styling is costly: only on change


def paint_badge(lb: QLabel, status: str) -> None:
    if getattr(lb, "_painted", None) == status:
        return
    lb._painted = status
    fg, bg, sym = BADGE[status]
    lb.setText(sym)
    lb.setAlignment(Qt.AlignCenter)
    lb.setStyleSheet(f"background: {bg}; color: {fg};")


class VideoView(QWidget):
    """Draws the latest preview frame straight from the numpy buffer: no QPixmap conversion, no QLabel
    re-layout per frame (v0.3 rebuilt and smooth-scaled a pixmap 30x per second on the UI thread)."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._arr = None
        self._img = None
        self._msg = ""

    def set_frame(self, arr: np.ndarray) -> None:
        self._arr = arr                    # keep the buffer alive while QImage points at it
        self._img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], QImage.Format_Grayscale8)
        self._msg = ""
        self.update()

    def set_message(self, text: str) -> None:
        if self._img is None and self._msg == text:
            return
        self._arr, self._img, self._msg = None, None, text
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#1F1F1F"))
        if self._img is not None:
            iw, ih = self._img.width(), self._img.height()
            k = min(r.width() / iw, r.height() / ih)
            w, h = int(iw * k), int(ih * k)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(QRect(r.x() + (r.width() - w) // 2, r.y() + (r.height() - h) // 2, w, h), self._img)
        elif self._msg:
            p.setPen(QColor("#C4C7C5"))
            f = p.font()
            f.setPixelSize(15)
            p.setFont(f)
            p.drawText(r.adjusted(48, 0, -48, 0), Qt.AlignCenter | Qt.TextWordWrap, self._msg)
        p.end()


def worst(statuses) -> str:
    order = {"fail": 3, "warn": 2, "wait": 1, "ok": 0}
    return max(statuses, key=lambda x: order[x], default="ok")


class Bus(QObject):
    done = Signal(str, object)                     # (background job kind, result)
    preview = Signal(object)                       # new preview session, sent before the slower QC finishes


class SettingsDialog(QDialog):
    def __init__(self, parent, s: dict, cam: E.Camera | None, modes: list[E.Mode] | None):
        super().__init__(parent)
        self.setWindowTitle(t("set_title"))
        self.setMinimumWidth(460)
        self.s = s
        self.modes = modes or []             # [(w, h, max_fps)] reported by the camera
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)

        v.addWidget(label(t("set_size"), "h"))
        self.size_box = QComboBox()
        for w, h, f in self.modes:
            self.size_box.addItem(t("mode_item", w=w, h=h, fps=f))
        if self.modes:
            self.size_box.setCurrentIndex(next((i for i, m in enumerate(self.modes)
                                                if m[:2] == (s["width"], s["height"])), 0))
        else:
            self.size_box.addItem(f"{s['width']}×{s['height']}")
            self.size_box.setEnabled(False)
        v.addWidget(self.size_box)
        if not self.modes:
            v.addWidget(label(t("set_size_na", w=s["width"], h=s["height"]), "muted", wrap=True))

        v.addSpacing(6)
        v.addWidget(label(t("set_fps"), "h"))
        self.fps_box = QComboBox()
        v.addWidget(self.fps_box)
        v.addWidget(label(t("set_fps_help"), "muted", wrap=True))

        v.addSpacing(6)
        v.addWidget(label(t("set_exp"), "h"))
        self.exp = QSlider(Qt.Horizontal, minimum=0)
        self.exp_lbl = label()
        self.exp.valueChanged.connect(self._exp_changed)
        r2 = QHBoxLayout()
        r2.addWidget(self.exp, 1)
        r2.addWidget(self.exp_lbl)
        v.addLayout(r2)
        v.addWidget(label(t("set_exp_help"), "muted", wrap=True))
        self.exp_cap = label("", "muted", wrap=True)
        v.addWidget(self.exp_cap)
        self._size_changed(self.size_box.currentIndex(), keep_fps=True)
        self.size_box.currentIndexChanged.connect(self._size_changed)
        self.fps_box.currentIndexChanged.connect(self._fps_changed)

        v.addSpacing(6)
        v.addWidget(label(t("set_folder"), "h"))
        self.root_lbl = label(s["data_root"], "mono", wrap=True)
        v.addWidget(self.root_lbl)
        pick = QPushButton(t("set_choose"))
        pick.clicked.connect(self._pick_root)
        v.addWidget(pick, 0, Qt.AlignLeft)

        native = QPushButton(t("set_native"), objectName="text")
        native.setEnabled(cam is not None and cam.kind == "dshow")
        native.clicked.connect(lambda: self.done(2))
        v.addWidget(native)

        v.addSpacing(4)
        v.addWidget(label(t("set_log"), "muted", wrap=True))
        v.addWidget(label(str(E.log_path()), "mono", wrap=True))
        ok = QPushButton(t("done"), objectName="filled")
        ok.clicked.connect(self.accept)
        v.addWidget(ok, 0, Qt.AlignRight)

    def _size_changed(self, i, keep_fps=False):
        """New size: offer its frame rates and start at its maximum (on opening: keep the saved rate)."""
        if self.modes:
            w, h, mx = self.modes[i]
            self.s["width"], self.s["height"] = w, h
            choices = E.fps_choices(mx)
        else:
            choices = sorted({E._num(self.s["fps"]), *E.FPS_STEPS}, reverse=True)
        fps = self.s["fps"] if keep_fps and self.s["fps"] in choices else choices[0]
        self.fps_box.blockSignals(True)
        self.fps_box.clear()
        for f in choices:
            self.fps_box.addItem(f"{f:g} fps", f)
        self.fps_box.setCurrentIndex(choices.index(fps))
        self.fps_box.blockSignals(False)
        self._fps_changed(self.fps_box.currentIndex())

    def _fps_changed(self, i):
        fps = self.s["fps"] = self.fps_box.itemData(i)
        self.stops = E.exposure_stops(fps)
        self.exp.blockSignals(True)
        self.exp.setMaximum(len(self.stops) - 1)
        self.exp.setValue(min(range(len(self.stops)), key=lambda k: abs(self.stops[k] - self.s["exposure_ms"])))
        self.exp.blockSignals(False)
        self._exp_changed(self.exp.value())
        capped = len(self.stops) < len(E.EXPOSURE_STOPS_MS)
        self.exp_cap.setText(t("set_exp_cap", fps=fps, ms=1000 / fps) if capped else "")
        self.exp_cap.setVisible(capped)

    def _exp_changed(self, i):
        self.s["exposure_ms"] = self.stops[i]
        self.exp_lbl.setText(f"{self.stops[i]:g} ms")

    def _pick_root(self):
        d = QFileDialog.getExistingDirectory(self, t("set_folder"), self.s["data_root"])
        if d:
            self.s["data_root"] = d
            self.root_lbl.setText(d)


class Main(QMainWindow):
    def __init__(self, demo: bool):
        super().__init__()
        self.demo = demo
        first_run = not E.settings_path().exists()
        self.s = E.load_settings()
        if first_run:     # the real Documents folder (README: prefer a local, non-OneDrive drive)
            docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
            if docs:
                self.s["data_root"] = str(Path(docs) / "GaitCam")
        i18n.LANG = self.s.get("lang", "en")
        self.cams: list[E.Camera] = []
        self.cam: E.Camera | None = None
        self.modes: list[E.Mode] | None = None
        self.sess: E.Session | None = None
        self.sess_t0 = 0.0
        self.state = "idle"                  # idle | starting | recording | stopping
        self.rec_part: Path | None = None
        self.rec_meta: E.TrialRecord | None = None
        self.rec_calib = False
        self.t_start = 0.0
        self.last: tuple | None = None       # (final_path | None, QC | error str, lost)
        self.last_video: Path | None = None
        self.chk = {"computer": [], "camera": ("wait", "c_live_wait", {}), "live": ("wait", "c_live_wait", {})}
        self.exp_state: tuple[str, float | None] = ("na", None)
        self.banner = ""
        self.bus = Bus()
        self.bus.done.connect(self._on_job)
        # every camera / ffmpeg / disk call runs here, one at a time and in order, so the UI never waits
        # (v0.2 did them on the UI thread: start / stop froze for seconds on the real camera)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = 0
        self.cam_gen = 0
        self.closing = False
        self.bus.preview.connect(self._on_preview)
        self._shown = -1                     # preview frame number on screen
        self._next_stats = 0.0               # chips / live check run at 4 Hz, not per frame
        self._next_live = 0.0
        self._btn_state = None
        self._idx_path, self._idx = None, {"rows": {}, "used": {}}
        self.setWindowTitle("GaitCam")
        self.resize(1320, 820)
        self.setMinimumSize(1100, 700)
        self._build()
        QShortcut(QKeySequence(Qt.Key_F9), self, activated=self.toggle_record)
        self.timer = QTimer(self, interval=33, timeout=self._tick)
        self.timer.start()
        QTimer.singleShot(50, self._startup)

    # ------------------------------------------------------------ layout (rebuilt on language change)
    def _build(self):
        keep = {}
        if hasattr(self, "id_edit"):
            keep = {"id": self.id_edit.text(), "notes": self.notes.text()}
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 24)
        outer.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(label("", "logo"))
        top.addWidget(label("GaitCam", "appname"))
        top.addSpacing(18)
        self.cam_box = QComboBox(minimumWidth=300)
        self.cam_box.currentIndexChanged.connect(self._camera_changed)
        top.addWidget(self.cam_box)
        self.refresh_btn = QPushButton(t("refresh"))
        self.refresh_btn.clicked.connect(self.refresh_cameras)
        top.addWidget(self.refresh_btn)
        self.cam_pill = label("", "pill")
        top.addWidget(self.cam_pill)
        top.addStretch(1)
        seg = QHBoxLayout()
        seg.setSpacing(4)
        self.lang_btns = {}
        grp = QButtonGroup(self)
        for code, text in (("en", "EN"), ("zh", "中文")):
            b = QPushButton(text, checkable=True, objectName="langchip")
            b.setChecked(i18n.LANG == code)
            b.clicked.connect(lambda _=False, c=code: self.set_lang(c))
            grp.addButton(b)
            seg.addWidget(b)
            self.lang_btns[code] = b
        top.addLayout(seg)
        self.settings_btn = QPushButton("⚙  " + t("settings"), objectName="tonal")
        self.settings_btn.clicked.connect(self.open_settings)
        top.addWidget(self.settings_btn)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(18)
        outer.addLayout(body, 1)

        # ---- left: preview + chips + ready check
        left = QVBoxLayout()
        left.setSpacing(16)
        body.addLayout(left, 1)
        pv = QFrame(objectName="preview")
        pl = QVBoxLayout(pv)
        pl.setContentsMargins(14, 14, 14, 14)
        self.view = VideoView()
        pl.addWidget(self.view, 1)
        chips = QHBoxLayout()
        self.chip_fps, self.chip_exp, self.chip_light, self.chip_sharp = (label("", "chip") for _ in range(4))
        for c in (self.chip_fps, self.chip_exp, self.chip_light, self.chip_sharp):
            chips.addWidget(c)
        chips.addStretch(1)
        self.rec_dot = label("", "chip")
        self.rec_dot.setStyleSheet("color: #F28B82;")
        chips.addWidget(self.rec_dot)
        pl.addLayout(chips)
        left.addWidget(pv, 1)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.chk_rows = {}
        for r, (key, title) in enumerate((("computer", "chk_computer"), ("camera", "chk_camera"),
                                          ("live", "chk_live"))):
            b = label("", "badge")
            ttl = label(t(title), "h")
            ttl.setFixedWidth(110)
            msg = label("", "muted", wrap=True)
            grid.addWidget(b, r, 0, Qt.AlignTop)
            grid.addWidget(ttl, r, 1, Qt.AlignTop)
            grid.addWidget(msg, r, 2)
            self.chk_rows[key] = (b, msg)
        grid.setColumnStretch(2, 1)
        self.banner_lbl = label("", "muted", wrap=True)
        left.addWidget(card(label(t("checks"), "h"), grid, self.banner_lbl, spacing=10, margins=18))

        # ---- right: sidebar (scrollable, so a short window never squeezes the inputs)
        sidew = QWidget(objectName="sidebar")
        side = QVBoxLayout(sidew)
        side.setContentsMargins(0, 0, 6, 0)
        side.setSpacing(16)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame, objectName="sidescroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("sideviewport")
        scroll.setWidget(sidew)
        scroll.setFixedWidth(416)
        body.addWidget(scroll)
        W = 400
        self.exp_edit = QLineEdit(self.s["experiment"], placeholderText=t("experiment_ph"))
        self.exp_edit.editingFinished.connect(self._experiment_changed)
        self.id_edit = QLineEdit(objectName="big", placeholderText=t("animal_ph"))
        self.id_edit.setMaxLength(40)
        self.id_edit.setMinimumHeight(56)
        self.exp_edit.setMinimumHeight(42)
        self.id_edit.textChanged.connect(self._fields_changed)
        self.id_edit.returnPressed.connect(self.toggle_record)
        self.next_lbl = label("", "mono", wrap=True)
        self.notes = QLineEdit(placeholderText=t("notes_ph"))
        self.notes.setMinimumHeight(42)
        c1 = card(label(t("experiment"), "h"), self.exp_edit, label(t("animal"), "h"), self.id_edit,
                  self.next_lbl, self.notes)
        c1.setFixedWidth(W)
        side.addWidget(c1)

        self.rec_btn = QPushButton(objectName="fab")
        self.rec_btn.clicked.connect(self.toggle_record)
        self.timer_lbl = label("00:00.0", "timer")
        self.timer_lbl.setAlignment(Qt.AlignCenter)
        self.timer_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)   # text width changes: no re-layout
        hint = label(t("shortcut_hint"), "muted")
        hint.setAlignment(Qt.AlignCenter)
        c2 = card(self.rec_btn, self.timer_lbl, hint, spacing=8)
        c2.setFixedWidth(W)
        side.addWidget(c2)

        self.qc_pill = label("", "pill")
        self.qc_text = label("", "muted", wrap=True)
        self.reject_btn = QPushButton(t("discard"), objectName="text")
        self.reject_btn.clicked.connect(self.reject_last)
        head = QHBoxLayout()
        head.addWidget(label(t("last_run"), "h"))
        head.addStretch(1)
        head.addWidget(self.qc_pill)
        self.trials = QListWidget()
        self.trials.setMinimumHeight(90)
        self.calib_btn = QPushButton(t("calib_btn"), objectName="text")
        self.calib_btn.clicked.connect(lambda: self.start_record(calib=True))
        self.targets_btn = QPushButton(t("calib_targets"), objectName="text")
        self.targets_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(CALIB_DIR))))
        self.folder_btn = QPushButton(t("open_folder"), objectName="text")
        self.folder_btn.clicked.connect(self.open_folder)
        c3 = card(head, self.qc_text, self.reject_btn, label(t("today"), "h"), self.trials,
                  self.calib_btn, self.targets_btn, self.folder_btn, spacing=8)
        c3.setFixedWidth(W)
        side.addWidget(c3)
        side.addStretch(1)

        if keep:
            self.id_edit.setText(keep["id"])
            self.notes.setText(keep["notes"])
        self._set_rec_button()
        self._render_last()
        self._render_checks()
        self._btn_state = None
        self._shown_items = None              # new list widget after a rebuild: repopulate
        self._fields_changed()
        self.view.set_message(t("c_live_wait"))

    def set_lang(self, code: str):
        if self.state != "idle" or self.pending or code == i18n.LANG:
            self.lang_btns[i18n.LANG].setChecked(True)
            return
        i18n.LANG = self.s["lang"] = code
        E.save_settings(self.s)
        cam_i = self.cam_box.currentIndex()
        self._build()
        self.cam_box.blockSignals(True)
        for c in self.cams:
            self.cam_box.addItem(c.label)
        self.cam_box.setCurrentIndex(max(0, cam_i))
        self.cam_box.blockSignals(False)
        self._camera_pill()
        self._chip_exposure()
        if self.cam is None and not self.cams:
            self.view.set_message(t("c_no_camera"))

    # ------------------------------------------------------------ self-checks
    def _render_checks(self):
        for key, (badge, msg) in self.chk_rows.items():
            v = self.chk[key]
            if key == "computer":
                items = v or [("wait", "c_live_wait", {})]
                bad = [x for x in items if x[0] != "ok"]
                st = worst(x[0] for x in items)
                txt = "\n".join(t(k, **p) for _, k, p in (bad or items))
            else:
                st, k, p = v
                txt = t(k, **p)
                if key == "camera" and st != "fail":
                    es, ms = self.exp_state
                    if es == "ok":
                        txt += "  " + t("c_exp_ok", ms=round(ms, 2))
                    elif es == "bad":
                        st, txt = worst([st, "warn"]), txt + "\n" + t("c_exp_bad")
            paint_badge(badge, st)
            set_text(msg, txt)
        set_text(self.banner_lbl, self.banner)
        if self.banner_lbl.isVisibleTo(self) != bool(self.banner):
            self.banner_lbl.setVisible(bool(self.banner))
        self._set_rec_button()

    def _blocking(self) -> bool:
        return (self.cam is None or any(x[0] == "fail" for x in self.chk["computer"])
                or self.chk["camera"][0] == "fail")

    def _startup(self):
        self._job("startup", self._w_startup, dict(self.s), self.demo)

    # ------------------------------------------------------------ background jobs (worker side: no widgets here)
    def _job(self, kind, fn, *args):
        if self.closing:
            return
        self.pending += 1

        def work():
            try:
                res = fn(*args)
            except Exception as exc:                       # reported, never fatal
                E.log(f"job {kind}: {type(exc).__name__}: {exc}")
                res = exc
            self.bus.done.emit(kind, res)

        self.pool.submit(work)
        self._set_rec_button()

    def _on_job(self, kind, res):
        self.pending -= 1
        if self.closing:
            return
        getattr(self, f"_done_{kind}")(res)
        self._set_rec_button()

    @staticmethod
    def _w_startup(s, demo):
        return {"checks": E.check_computer(s), "recovered": E.recover_parts(s), "cams": E.list_cameras(demo)}

    @staticmethod
    def _w_cams(old, demo):
        if old is not None:
            old.stop()
        return E.list_cameras(demo)

    @staticmethod
    def _w_camera(old, cam, s, gen, native=False):
        if old is not None:
            old.stop()
        if native:
            E.open_native_settings(cam, s)
        modes = E.list_mjpeg_modes(cam)
        s.update(E.pick_mode(modes, s))        # s is this job's copy: the UI takes the mode over in _done_camera
        mode = {k: s[k] for k in ("width", "height", "fps", "exposure_ms")}
        return {"gen": gen, "cam": cam, "modes": modes, "mode": mode, "chk": E.check_camera(cam, modes, s),
                "exp": E.apply_exposure(cam, s), "checks": E.check_computer(s), "sess": E.Session(cam, s)}

    @staticmethod
    def _w_record(old, cam, s, part):
        if old is not None:
            old.stop()
        return E.Session(cam, s, out_path=part)

    @staticmethod
    def _w_stop(sess, part, meta, lost, cam, s, bus):
        out = {"lost": lost}
        if sess is not None:
            sess.stop()
        # preview first: the camera is back on screen while the file is being checked
        bus.preview.emit(E.Session(cam, s) if cam is not None else None)
        try:
            if part is None or not part.exists():
                raise FileNotFoundError(getattr(part, "name", "recording"))
            q = E.check_video(part, meta.fps_target)
            if lost:
                meta.notes = (meta.notes + "; " if meta.notes else "") + "camera disconnected during run"
            out.update(final=E.finalize(part, meta, q), q=q)
        except Exception as exc:
            E.log(f"finish {part}: {type(exc).__name__}: {exc}")
            out.update(final=None, q=f"{type(exc).__name__}: {exc}")
        out["checks"] = E.check_computer(s)
        day = E.session_dir(s)
        out["index"] = (day, E.session_index(day))             # rebuilt here, not on the UI thread
        return out

    @staticmethod
    def _w_fail_start(sess, part, cam, s, bus):
        if sess is not None:
            sess.stop()
        try:                      # nothing useful was written; do not leave a .part behind
            if part is not None and part.exists():
                part.unlink()
        except OSError:
            pass
        bus.preview.emit(E.Session(cam, s) if cam is not None else None)
        return {"lost": True, "final": None, "q": "start_fail", "checks": E.check_computer(s)}

    @staticmethod
    def _w_reject(video, s):
        E.reject(video)
        day = E.session_dir(s)
        return {"index": (day, E.session_index(day))}

    @staticmethod
    def _w_discard(sess):
        if sess is not None:
            sess.stop()

    # ------------------------------------------------------------ job results (UI side)
    def _done_startup(self, res):
        if isinstance(res, Exception):
            self.banner = t("q_error", err=res)
            self._render_checks()
            return
        self.chk["computer"] = res["checks"]
        if res["recovered"]:
            self.banner = t("recovered", n=res["recovered"])
        self._set_cams(res["cams"])

    def _done_cams(self, res):
        self._set_cams([] if isinstance(res, Exception) else res)

    def _done_discard(self, res):
        pass

    def _done_reject(self, res):
        if isinstance(res, dict):
            self._idx_path, self._idx = res["index"]
        else:
            self._idx_path = None
        self._render_last()
        self._fields_changed()

    def _on_preview(self, sess):
        if self.closing or self.sess is not None:
            if sess is not None:
                self._job("discard", self._w_discard, sess)
            return
        self.sess, self.sess_t0 = sess, time.monotonic()
        self.chk["live"] = ("wait", "c_live_wait", {})
        self._render_checks()

    def _done_camera(self, res):
        if isinstance(res, Exception):
            self.chk["camera"] = ("fail", "q_start_fail", {})
            self.view.set_message(t("q_start_fail"))
            self._render_checks()
            return
        if res["gen"] != self.cam_gen:                       # user switched again meanwhile: drop this one
            self._job("discard", self._w_discard, res["sess"])
            return
        self.cam, self.modes, self.exp_state = res["cam"], res["modes"], res["exp"]
        self.chk["camera"], self.chk["computer"] = res["chk"], res["checks"]
        self.sess, self.sess_t0 = res["sess"], time.monotonic()
        self.s.update(res["mode"])
        self.s["camera"] = self.cam.name
        E.save_settings(self.s)
        self._camera_pill()
        self._chip_exposure()
        self._render_checks()

    def _done_record(self, res):
        if isinstance(res, Exception):
            self.state = "stopping"
            self._job("fail_start", self._w_fail_start, None, self.rec_part, self.cam, dict(self.s), self.bus)
            return
        self.sess, self.sess_t0 = res, time.monotonic()
        self.t_start = time.monotonic()                      # start-up timeout counts from here

    def _done_stop(self, res):
        self.state = "idle"
        if isinstance(res, Exception):
            res = {"final": None, "q": str(res), "lost": False, "checks": self.chk["computer"]}
        self.last = (res["final"], res["q"], res["lost"])
        if res["final"] is not None:
            self.last_video = res["final"]
        self.chk["computer"] = res["checks"]
        if "index" in res:
            self._idx_path, self._idx = res["index"]
        else:
            self._idx_path = None
        self._render_last()
        self.notes.clear()
        self._fields_changed()
        self.id_edit.setFocus()
        self.id_edit.selectAll()
        self._render_checks()
        if self.sess is None and self.cams:                    # preview never came back: reconnect
            self._select_camera(self.cam_box.currentIndex())

    _done_fail_start = _done_stop

    # ------------------------------------------------------------ cameras / preview
    def refresh_cameras(self):
        if self.state != "idle" or self.pending:
            return
        old, self.sess = self.sess, None
        self.view.set_message(t("c_connecting"))
        self._job("cams", self._w_cams, old, self.demo)

    def _set_cams(self, cams):
        self.cams = cams
        self.cam_box.blockSignals(True)
        self.cam_box.clear()
        for c in self.cams:
            self.cam_box.addItem(c.label)
        pref = next((i for i, c in enumerate(self.cams) if c.name == self.s.get("camera")), 0)
        self.cam_box.blockSignals(False)
        if self.cams:
            self.cam_box.blockSignals(True)
            self.cam_box.setCurrentIndex(pref)
            self.cam_box.blockSignals(False)
            self._select_camera(pref)
        else:
            self.cam, self.modes = None, None
            self.chk["camera"] = E.check_camera(None, [], self.s)
            self.chk["live"] = ("wait", "c_live_wait", {})
            self._camera_pill()
            self.view.set_message(t("c_no_camera"))
            self._render_checks()

    def _select_camera(self, i, native=False):
        if not (0 <= i < len(self.cams)):
            return
        old, self.sess = self.sess, None
        self.cam_gen += 1
        self.chk["live"] = ("wait", "c_live_wait", {})
        self.view.set_message(t("c_connecting"))
        self._render_checks()
        self._job("camera", self._w_camera, old, self.cams[i], dict(self.s), self.cam_gen, native)

    def _camera_changed(self, i):
        if self.state == "idle":
            self._select_camera(i)

    def _camera_pill(self):
        if self.cam is None:
            paint_pill(self.cam_pill, "fail", "—")
            return
        paint_pill(self.cam_pill, self.chk["camera"][0],
                   f"{self.s['width']}×{self.s['height']} · MJPEG · {self.s['fps']} fps")

    def _chip_exposure(self):
        es, ms = self.exp_state
        set_text(self.chip_exp, t("chip_exp", ms=round(ms, 2)) if es == "ok"
                 else t("chip_exp_na") if es == "na" else "⚠ " + t("set_exp"))

    def _stop_session(self):
        if self.sess is not None:
            self.sess.stop()
            self.sess = None

    def _live_check(self, s: E.Session, mean: float, clip: float) -> tuple[str, str, dict]:
        if time.monotonic() - self.sess_t0 < 2.0:
            return "wait", "c_live_wait", {}
        target = self.s["fps"]
        if s.live_fps < 0.85 * target:
            return "fail", "c_live_slow", {"fps": round(s.live_fps), "target": target}
        if mean < 35:
            return "warn", "c_dark", {}
        if clip > 0.02:
            return "warn", "c_bright", {}
        if s.live_fps < 0.95 * target:
            return "warn", "c_live_slow", {"fps": round(s.live_fps), "target": target}
        return "ok", "c_live_ok", {"fps": round(s.live_fps)}

    def _tick(self):
        s = self.sess
        now = time.monotonic()
        if s is not None and s.frame is not None and s.running:
            if (id(s), s.frame_no) != self._shown:               # repaint only when a new frame arrived
                self._shown = (id(s), s.frame_no)
                self.view.set_frame(s.frame)
            if now >= self._next_stats:
                self._next_stats = now + 0.25
                f = s.frame[::2, ::2]
                mean, clip = float(f.mean()), float((f >= 250).mean())
                g = f.astype(np.int16)
                sharp = float(np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean())
                set_text(self.chip_light, t("chip_light", v=mean))
                set_text(self.chip_sharp, t("chip_sharp", v=sharp))
                set_text(self.chip_fps, t("chip_fps", fps=s.live_fps))
                if now >= self._next_live:
                    self._next_live = now + 0.5
                    new = self._live_check(s, mean, clip)
                    if new != self.chk["live"]:
                        self.chk["live"] = new
                        self._render_checks()
        elif s is not None and not s.running and self.state == "idle":
            self.sess = None                                     # camera gone while idle
            self.view.set_message(t("c_cam_lost"))
            self.chk["live"] = ("fail", "c_cam_lost", {})
            self._render_checks()

        if self.state == "starting":
            if s is not None and s.t_first is not None:
                self.state = "recording"
                self._set_rec_button()
            elif s is not None and (now - self.t_start > 8 or not s.running):   # no frames: give up cleanly
                self.state = "stopping"
                self.sess = None
                E.log(f"start failed: {s.log[-3:]}")
                self._job("fail_start", self._w_fail_start, s, self.rec_part, self.cam, dict(self.s), self.bus)
        elif self.state == "recording" and s is not None:
            el = now - s.t_first
            set_text(self.timer_lbl, f"{int(el // 60):02d}:{el % 60:04.1f}")
            set_text(self.rec_dot, "●  REC" if int(el * 2) % 2 == 0 else "○  REC")
            if not s.running:                                    # camera unplugged / ffmpeg died mid-run
                self.stop_record(lost=True)
            elif self.rec_calib and el >= 1.2:
                self.stop_record()

    # ------------------------------------------------------------ recording
    def _set_rec_button(self):
        rec = self.state in ("starting", "recording")
        ready = self.state == "idle" and not self.pending and self.sess is not None and not self._blocking()
        idle = self.state == "idle" and not self.pending
        key = (self.state, ready, idle, i18n.LANG)
        if key == self._btn_state:
            return
        self._btn_state = key
        b = self.rec_btn
        b.setText({"idle": "●   " + t("record"), "starting": t("starting"), "recording": "■   " + t("stop"),
                   "stopping": t("checking")}[self.state])
        if b.property("recording") != rec:
            b.setProperty("recording", rec)
            b.style().polish(b)
        b.setEnabled(self.state == "recording" or ready)
        for w in (self.id_edit, self.exp_edit, self.cam_box, self.settings_btn, self.refresh_btn,
                  *self.lang_btns.values()):
            w.setEnabled(idle)
        self.calib_btn.setEnabled(ready)
        self.targets_btn.setEnabled(idle and CALIB_DIR.is_dir())
        self.rec_dot.setVisible(rec)

    def _experiment_changed(self):
        self.s["experiment"] = E.clean_name(self.exp_edit.text())
        E.save_settings(self.s)
        self._fields_changed()

    def _index(self):
        sess = E.session_dir(self.s)
        if sess != self._idx_path:                 # folder changed (experiment, new day) or a run was added
            self._idx_path, self._idx = sess, E.session_index(sess)
        return sess, self._idx

    def _fields_changed(self):
        if not hasattr(self, "next_lbl"):
            return
        raw = self.id_edit.text().strip()
        animal = E.clean_id(raw)
        sess, idx = self._index()
        if animal:
            n = max(idx["used"].get(animal, {0})) + 1
            txt = f"{t('next_file')}:\n{sess.name}/{E.trial_stem(animal, n, datetime.now())}.mkv"
            if not raw.isdigit() and animal != raw:
                txt += "\n" + t("id_ascii")
            set_text(self.next_lbl, txt)
        else:
            set_text(self.next_lbl, t("id_ascii") if raw else t("type_id_first"))
        self._refresh_trials()

    def _refresh_trials(self):
        animal = E.clean_id(self.id_edit.text())
        _, idx = self._index()
        sym = {"ok": "✓", "warn": "!", "fail": "✕"}
        items = [f"t{m['trial']:02d}    {sym.get(m['qc'].get('verdict'), '?')}    "
                 f"{m['qc'].get('fps_mean', 0):.1f} fps    {m['qc'].get('duration_s', 0):.1f} s"
                 for m in idx["rows"].get(animal, [])] if animal else []
        if items != getattr(self, "_shown_items", None):
            self._shown_items = items
            self.trials.clear()
            self.trials.addItems(items)

    def toggle_record(self):
        if self.state == "idle":
            self.start_record()
        elif self.state == "recording":
            self.stop_record()

    def start_record(self, calib=False):
        if self.state != "idle" or self.pending or self.cam is None or self._blocking():
            return
        animal = "calib" if calib else E.clean_id(self.id_edit.text())
        if not animal:
            self.id_edit.setFocus()
            self.next_lbl.setText(t("id_ascii") if self.id_edit.text().strip() else t("type_id_first"))
            return
        sess = E.session_dir(self.s)
        folder = sess / "calib" if calib else sess
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.chk["computer"] = E.check_computer(self.s)
            self._render_checks()
            return
        now = datetime.now()
        trial = E.next_trial(folder, animal)
        stem = E.trial_stem(animal, trial, now)
        self.rec_part, self.rec_calib = folder / f"{stem}.part.mkv", calib
        self.rec_meta = E.TrialRecord(file="", animal=animal, trial=trial, started=now.isoformat(timespec="seconds"),
                                      camera=self.cam.name, width=self.s["width"], height=self.s["height"],
                                      fps_target=self.s["fps"], exposure_ms=self.s["exposure_ms"],
                                      notes=self.notes.text().strip())
        old, self.sess = self.sess, None
        self.state, self.t_start = "starting", time.monotonic()
        self.timer_lbl.setText("00:00.0")
        paint_pill(self.qc_pill, "fail", t("recording"))
        self.qc_text.setText(stem)
        self.reject_btn.setEnabled(False)
        self.s["experiment"] = E.clean_name(self.exp_edit.text())
        E.save_settings(self.s)
        self._job("record", self._w_record, old, self.cam, dict(self.s), self.rec_part)

    def stop_record(self, lost: bool = False):
        if self.state != "recording":
            return
        self.state = "stopping"
        sess, self.sess = self.sess, None
        self._job("stop", self._w_stop, sess, self.rec_part, self.rec_meta, lost, self.cam, dict(self.s), self.bus)

    def _render_last(self):
        if self.last is None:
            self.qc_pill.setText("")
            self.qc_pill.setStyleSheet("")
            self.qc_text.setText(t("no_run_yet"))
            self.reject_btn.setEnabled(False)
            return
        final, q, lost = self.last
        if final is None:
            paint_pill(self.qc_pill, "fail", t("fail"))
            self.qc_text.setText(t("q_start_fail") if q == "start_fail" else t("q_error", err=q))
            self.reject_btn.setEnabled(False)
            return
        if not final.exists():      # discarded
            paint_pill(self.qc_pill, "wait", t("discarded"))
            self.qc_text.setText(t("moved_rejected", name=final.name))
            self.reject_btn.setEnabled(False)
            return
        paint_pill(self.qc_pill, q.verdict, {"ok": "✓  ", "warn": "!  ", "fail": "✕  "}[q.verdict] + t(q.verdict))
        lines = [final.name, t("run_line", fps=q.fps_mean, frames=q.frames, dur=q.duration_s, gaps=q.gaps),
                 t(q.key, **q.params)]
        if lost:
            lines.append(t("q_lost"))
        self.qc_text.setText("\n".join(lines))
        self.reject_btn.setEnabled(self.state == "idle" and final.parent.name != "calib")

    def reject_last(self):
        if self.state == "idle" and not self.pending and self.last_video and self.last_video.exists() \
                and self.last_video.parent.name != "calib":
            self.reject_btn.setEnabled(False)
            self._job("reject", self._w_reject, self.last_video, dict(self.s))

    # ------------------------------------------------------------ misc
    def open_settings(self):
        if self.state != "idle" or self.pending:
            return
        s = dict(self.s)
        r = SettingsDialog(self, s, self.cam, self.modes).exec()
        if r in (QDialog.Accepted, 2):
            self.s.update(s)
            E.save_settings(self.s)
            self._fields_changed()
            if self.cams:
                self._select_camera(self.cam_box.currentIndex(), native=(r == 2))

    def open_folder(self):
        sess = E.session_dir(self.s)
        target = sess if sess.exists() else Path(self.s["data_root"])
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def closeEvent(self, ev):
        if self.state in ("recording", "starting", "stopping"):
            if QMessageBox.question(self, "GaitCam", t("quit_while_rec")) != QMessageBox.Yes:
                ev.ignore()
                return
        self.closing = True
        self.timer.stop()
        if self.sess is not None:
            self.sess.stop()          # a recording leaves a .part file; it is recovered and checked on next start
            self.sess = None
        self.pool.shutdown(wait=True, cancel_futures=True)   # let a running stop / QC job finish
        E.Session.stop_all()          # and stop any preview a background job started meanwhile
        ev.accept()


def run(app: QApplication, splash=None):
    demo = "--demo" in sys.argv
    try:
        # load OpenCV now, behind the splash: importing it later from the worker holds the interpreter lock
        # for ~0.2 s and froze the UI once per session (found with a stall profiler, 2026-10-06)
        import cv2  # noqa: F401
    except ImportError:
        pass
    E.log(f"GaitCam {E.APP_VERSION} start (demo={demo}, python {sys.version.split()[0]}, {sys.platform})")
    app.setStyleSheet(QSS)

    def excepthook(tp, v, tb):                    # pythonw has no console: log + tell the user
        import traceback
        E.log("UNCAUGHT " + "".join(traceback.format_exception(tp, v, tb)))
        QMessageBox.critical(None, "GaitCam", t("crash", err=v, path=E.log_path()))

    sys.excepthook = excepthook
    w = Main(demo)
    w.show()
    if splash is not None:
        app.processEvents()           # let the main window paint once before the splash goes away
        splash.close()
    sys.exit(app.exec())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GaitCam")
    run(app)


if __name__ == "__main__":
    main()
