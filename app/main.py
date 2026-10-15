"""GaitCam launcher: show a splash the moment Qt is loaded, then import / build the real window behind it.

The heavy part (numpy, the UI module, first camera scan) used to run before anything was visible, so a
double-click looked like nothing was happening for a few seconds on Windows.
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel


def splash_pixmap() -> QPixmap:
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    w, h = 320, 140
    pm = QPixmap(int(w * ratio), int(h * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(0, 0, w, h, 24, 24)
    for i, c in enumerate(("#4285F4", "#34A853", "#FBBC04", "#EA4335")):
        p.setBrush(QColor(c))
        p.drawEllipse(32 + i * 16, 40, 12, 12)
    f = QFont("Segoe UI Variable Display")
    f.setPixelSize(26)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor("#1F1F1F"))
    p.drawText(32, 88, "GaitCam")
    f.setPixelSize(13)
    f.setWeight(QFont.Normal)
    p.setFont(f)
    p.setPen(QColor("#444746"))
    p.drawText(33, 114, "starting…  正在启动…")
    p.end()
    return pm


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GaitCam")
    # a plain frameless window, not QSplashScreen: that one blocks up to 1 s waiting for an "exposed" event
    splash = QLabel(windowFlags=Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    splash.setAttribute(Qt.WA_TranslucentBackground)
    splash.setPixmap(splash_pixmap())
    splash.show()
    app.processEvents()

    import gaitcam                     # numpy, engine, widgets: loaded while the splash is visible
    gaitcam.run(app, splash)


if __name__ == "__main__":
    main()
