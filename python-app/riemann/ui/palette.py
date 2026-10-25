"""Colour tokens shared by the home pages, dialogs and the QSS themes.

The same values live in assets/theme/modern_*.css and assets/homepage/homepage.css;
change all three together.
"""

import os
import sys

from PySide6.QtCore import QByteArray, QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

DARK = {
    "canvas": "#1C1D20",
    "surface": "#232428",
    "raised": "#2A2C31",
    "border": "#303237",
    "border_strong": "#3D4047",
    "text": "#E6E7EA",
    "muted": "#9A9FA8",
    "faint": "#6E737C",
    "accent": "#6B9BFF",
    "accent_hover": "#86AEFF",
    "accent_text": "#0F1626",
    "accent_soft": "rgba(107, 155, 255, 0.14)",
    "danger": "#F07070",
    "danger_soft": "rgba(240, 112, 112, 0.12)",
    "grid": "rgba(255, 255, 255, 0.028)",
    "glow": "rgba(107, 155, 255, 0.07)",
}

LIGHT = {
    "canvas": "#F4F4F1",
    "surface": "#FFFFFF",
    "raised": "#ECECE8",
    "border": "#E0E0DB",
    "border_strong": "#CBCBC4",
    "text": "#1C1D20",
    "muted": "#5F646C",
    "faint": "#8C9098",
    "accent": "#2F6FEB",
    "accent_hover": "#1F5BD3",
    "accent_text": "#FFFFFF",
    "accent_soft": "rgba(47, 111, 235, 0.10)",
    "danger": "#D33C3C",
    "danger_soft": "rgba(211, 60, 60, 0.08)",
    "grid": "rgba(0, 0, 0, 0.035)",
    "glow": "rgba(47, 111, 235, 0.05)",
}

FONT_STACK = "'Inter', 'Segoe UI', system-ui, sans-serif"


def tokens(dark: bool) -> dict:
    """Returns the colour tokens for the dark or light theme."""
    return DARK if dark else LIGHT


def icon_path(filename: str) -> str:
    """Absolute path of a file in assets/icons, frozen or not."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "riemann", "assets", "icons", filename)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "icons", filename)


def tinted_pixmap(name: str, color: str, size: int, dpr: float = 2.0) -> QPixmap:
    """Renders a Lucide icon from assets/icons (stroke="currentColor") in `color`."""
    try:
        with open(icon_path(f"{name}.svg"), encoding="utf-8") as f:
            svg = f.read().replace("currentColor", color)
    except OSError:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def tinted_icon(name: str, color: str, size: int = 24) -> QIcon:
    return QIcon(tinted_pixmap(name, color, size))


def _qcolor(value: str) -> QColor:
    if value.startswith("rgba("):
        r, g, b, a = (p.strip() for p in value[5:-1].split(","))
        return QColor(int(r), int(g), int(b), round(float(a) * 255))
    return QColor(value)


class GridCanvas(QWidget):
    """Home page background: the canvas colour, a faint graph-paper grid like the
    app icon's, and a soft accent glow at the top."""

    CELL = 32

    def __init__(self, dark: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.set_dark(dark)

    def set_dark(self, dark: bool) -> None:
        self._t = tokens(dark)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), _qcolor(self._t["canvas"]))

        p.setPen(QPen(_qcolor(self._t["grid"]), 1))
        w, h = self.width(), self.height()
        for x in range(self.CELL, w, self.CELL):
            p.drawLine(x, 0, x, h)
        for y in range(self.CELL, h, self.CELL):
            p.drawLine(0, y, w, y)

        glow = QRadialGradient(QPointF(w / 2, 0), max(w, h) * 0.55)
        glow.setColorAt(0.0, _qcolor(self._t["glow"]))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)
        p.end()


def short_folder(path: str, keep: int = 3) -> str:
    """A folder path for display: ~ for the home folder, and only the last `keep`
    parts of long paths ("…/benchmarks/corpus")."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    parts = [p for p in path.split(os.sep) if p]
    if len(parts) > keep:
        return "…" + os.sep + os.sep.join(parts[-keep:])
    return path


def path_row(icon_pm, name: str, folder: str) -> QWidget:
    """Two-line list row: icon, name, and the containing folder in a quieter colour."""
    row = QWidget()
    row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(14, 6, 14, 6)
    lay.setSpacing(12)
    icon = QLabel()
    icon.setPixmap(icon_pm)
    icon.setFixedWidth(20)
    text = QVBoxLayout()
    text.setSpacing(1)
    lbl_name = QLabel(name)
    lbl_name.setObjectName("recentName")
    lbl_dir = QLabel(folder)
    lbl_dir.setObjectName("recentDir")
    for lbl in (lbl_name, lbl_dir):
        lbl.setMinimumWidth(1)
        lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    text.addWidget(lbl_name)
    text.addWidget(lbl_dir)
    lay.addWidget(icon)
    lay.addLayout(text, 1)
    return row
