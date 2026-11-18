"""Colour tokens shared by the home pages, dialogs and the QSS themes.

The same values live in assets/theme/modern_*.css and assets/homepage/homepage.css;
change all three together.

"system" is the teal accent of System tabs (Settings, History, Downloads...),
kept apart from the blue accent so those tabs read as part of Riemann, not
documents or web pages. "private" is the purple of incognito address bars, which
are always dark, so only DARK["private"] is used. "success" and "warning" (with
"danger" and "accent") colour status banners such as the signature check; the
light values keep 4.5:1 contrast under white text, the dark ones under
accent_text. The CSS files use none of these three.

toast_style, link_tip_style, incognito_url_style, banner_style,
overlay_style and scrollbar_style are the small stylesheets several widgets
share. overlay_style
is for labels drawn over the page itself (the page-number popup), which sit on
document content in either theme, so it always uses the dark tokens.

CONTENT holds colours that belong to the document or the projector rather than
the interface theme, so they don't change with it: the presentation stage and
its black and white blanks, the laser pointer, the default pen and text ink,
search hits, note markers, text selection, the read-aloud highlight, the
outline of a redaction mark and the box a redaction leaves, and the glow of a
tab playing media. qcolor turns any of these values into a QColor
with alpha.

dialog_icon puts a Lucide icon on a dialog, tinted for the theme of the
window it belongs to (window_is_dark reads that window's dark_mode: the
theme is a stylesheet on the window, so the application palette doesn't
know it). The cog icons mark settings dialogs, so they read apart from
document ones.

The interface font is Inter, bundled in assets/fonts (SIL Open Font License,
Inter-OFL.txt) so every machine renders the same face. load_ui_font registers it
once at startup; FONT_STACK keeps system fallbacks for anything Inter lacks.
"""

import os

from PySide6.QtCore import QByteArray, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QSizePolicy, QVBoxLayout, QWidget

from ..core.assets import asset_path, icon_path

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
    "success": "#4CC38A",
    "warning": "#E5A54B",
    "system": "#4FD1C5",
    "system_soft": "rgba(79, 209, 197, 0.12)",
    "private": "#9B6BE0",
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
    "success": "#1A7F45",
    "warning": "#A16207",
    "system": "#0E8C7F",
    "system_soft": "rgba(14, 140, 127, 0.10)",
    "private": "#7A3FC9",
    "grid": "rgba(0, 0, 0, 0.035)",
    "glow": "rgba(47, 111, 235, 0.05)",
}

CONTENT = {
    "stage": "#000000",
    "paper": "#FFFFFF",
    "ink": "#000000",
    "pen": "#FF0000",
    "laser": "#FF2828",
    "search_hit": "#FFFF00",
    "note": "#FFFF00",
    "selection": LIGHT["accent"],
    "redact_mark": "#E53935",
    "redaction": "#000000",
    "spoken": DARK["system"],
    "playing_from": "#FF4500",
    "playing_to": "#8A2BE2",
}

FONT_STACK = "'Inter', 'Segoe UI', system-ui, sans-serif"


def qcolor(value: str, alpha: int = 255) -> QColor:
    """A QColor from a token value, with alpha 0-255."""
    color = QColor(value)
    color.setAlpha(alpha)
    return color


def app_is_dark() -> bool:
    """Whether the running application's window colour is dark."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return app is not None and app.palette().window().color().lightness() < 128


def tokens(dark: bool) -> dict:
    """Returns the colour tokens for the dark or light theme."""
    return DARK if dark else LIGHT


def scrollbar_style(t: dict) -> str:
    """The QSS themes' scroll bars in t's colours, for widgets themed apart from the app."""
    return (
        "QScrollBar:vertical { border: none; background: transparent; width: 16px; margin: 0px; }"
        "QScrollBar:horizontal { border: none; background: transparent; height: 16px; margin: 0px; }"
        f"QScrollBar::handle:vertical {{ background: {t['border_strong']}; min-height: 24px; border-radius: 5px; margin: 2px; }}"
        f"QScrollBar::handle:horizontal {{ background: {t['border_strong']}; min-width: 24px; border-radius: 5px; margin: 2px; }}"
        f"QScrollBar::handle:hover {{ background: {t['faint']}; }}"
        "QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: none; }"
    )


def toast_style(t: dict) -> str:
    return (
        f"background: {t['raised']}; color: {t['text']}; border: 1px solid {t['border_strong']}; "
        "padding: 10px; border-radius: 5px;"
    )


def link_tip_style(t: dict) -> str:
    return (
        f"background: {t['raised']}; color: {t['text']}; padding: 3px 7px; "
        f"border: 1px solid {t['border_strong']}; border-radius: 3px; font-size: 11px;"
    )


def incognito_url_style() -> str:
    t = DARK
    return (
        f"QLineEdit {{ border: 2px solid {t['private']}; background-color: {t['raised']}; "
        f"color: {t['text']}; border-radius: 4px; padding: 4px; }}"
    )


def menu_style(t: dict) -> str:
    """The QSS themes' menus in t's colours, for menus opened from widgets themed apart from the app."""
    return (
        f"QMenu {{ background-color: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']};"
        " border-radius: 10px; padding: 6px 4px; }"
        "QMenu::item { padding: 6px 32px 6px 16px; border-radius: 6px; margin: 1px 4px;"
        f" background: transparent; color: {t['text']}; }}"
        f"QMenu::item:selected {{ background-color: {t['raised']}; color: {t['text']}; }}"
        f"QMenu::item:disabled {{ color: {t['faint']}; }}"
        f"QMenu::separator {{ height: 1px; background: {t['border']}; margin: 6px 12px; }}"
    )


def banner_style(t: dict, kind: str) -> str:
    """kind is "accent", "success", "warning", "danger" or "neutral"."""
    if kind == "neutral":
        return f"background-color: {t['raised']}; color: {t['text']};"
    return f"background-color: {t[kind]}; color: {t['accent_text']};"


def overlay_style() -> str:
    t = DARK
    return (
        f"background-color: {t['raised']}; color: {t['text']}; font-size: 13px; font-weight: bold; "
        f"border-radius: 6px; padding: 5px 12px; border: 1px solid {t['border_strong']};"
    )


UI_FONT = "Inter"


_ui_font_loaded: bool | None = None


def load_ui_font() -> bool:
    """
    Registers the bundled Inter font with Qt; safe to call more than once.

    Returns False when the file is missing or unreadable, in which case Qt falls
    back along FONT_STACK.
    """
    global _ui_font_loaded
    if _ui_font_loaded is None:
        font_id = QFontDatabase.addApplicationFont(asset_path("fonts", "Inter-Variable.ttf"))
        _ui_font_loaded = font_id != -1
    return _ui_font_loaded


def ui_font(pixel_size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """The interface font at a pixel size and weight."""
    font = QFont(UI_FONT)
    font.setFamilies([UI_FONT, "Segoe UI", "Noto Sans", "sans-serif"])
    font.setPixelSize(max(1, round(pixel_size)))
    font.setWeight(weight)
    return font


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


def window_is_dark(widget: QWidget) -> bool:
    """The theme of the Riemann window a dialog belongs to (its dark_mode), else the application's."""
    parent = widget.parentWidget() if widget is not None else None
    dark = getattr(parent.window() if parent is not None else None, "dark_mode", None)
    return dark if isinstance(dark, bool) else app_is_dark()


def dialog_icon(dialog: QWidget, name: str, tone: str = "accent") -> QPixmap:
    """
    Gives a dialog a Lucide icon in its window's theme, coloured by the token
    tone: beside the text of a QMessageBox, otherwise above the dialog's
    content (call it once the dialog's layout exists).
    """
    pm = tinted_pixmap(name, tokens(window_is_dark(dialog))[tone], 36)
    dialog.setWindowIcon(QIcon(pm))
    if isinstance(dialog, QMessageBox):
        dialog.setIconPixmap(pm)
    elif dialog.layout() is not None:
        badge = QLabel()
        badge.setObjectName("dialogIcon")
        badge.setPixmap(pm)
        dialog.layout().insertWidget(0, badge, 0, Qt.AlignmentFlag.AlignLeft)
    return pm


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
