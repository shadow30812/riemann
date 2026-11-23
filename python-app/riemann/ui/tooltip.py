"""
Riemann's tooltips, drawn in the "keycaps" style.

Every tooltip in a Qt app, whether from setToolTip, an item view, a tab bar or
a web page, is shown through Qt's private QTipLabel window. Styling that window
with QSS cannot round its corners or give it a shadow, and it inherits the
stylesheet of the widget it belongs to (the AI search button's purple bold text
used to leak into its tooltip).

TooltipSkin filters the ToolTip events of Riemann's own widgets and shows one
TipWindow, a translucent, frameless window that paints the panel, its shadow
and the keycaps itself, with the text the widget (or the tab or list row under
the mouse) would have shown. Qt's tooltip window is never created for them.
Tooltips from web pages still use Qt's window, which the theme stylesheets
style to match as closely as QSS allows.

parse_tip turns the existing tooltip strings into parts without touching any
call site: "AI Semantic Search (Ctrl+I)" becomes the title "AI Semantic Search"
with keys Ctrl and I, and any further lines become a dimmer description. Rich
text tooltips are shown as they are.

On Wayland a tooltip must have a transient parent window, so TipWindow uses
the window of the widget it describes.

Colours follow set_dark, which the main window calls when the theme changes,
and the high-contrast setting (palette.high_contrast) while dark.

Since TooltipSkin sees every widget and every tooltip change, it also names
icon-only controls for screen readers from their tooltips
(ui/accessibility.py).
"""

import re

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtGui import Qt as GuiQt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .accessibility import name_from_tooltip
from .palette import high_contrast, load_ui_font, ui_font

SHORTCUT = re.compile(
    r"\s*\(((?:Ctrl|Alt|Shift|Meta|Cmd|Esc|F\d{1,2}|Page Up|Page Down|Home|End|Space)(?:\+[^()]+)?)\)\s*$"
)

COLOURS = {
    True: {
        "bg": "#2C2E34",
        "border": "#43464E",
        "text": "#ECEDEF",
        "muted": "#9A9FA8",
        "shadow": 110,
        "key_bg": "#3A3D44",
        "key_border": "#50545D",
        "key_text": "#D9DADE",
    },
    False: {
        "bg": "#FFFFFF",
        "border": "#D6D6D0",
        "text": "#1C1D20",
        "muted": "#5F646C",
        "shadow": 45,
        "key_bg": "#F2F2EF",
        "key_border": "#CBCBC4",
        "key_text": "#3A3C40",
    },
    "contrast": {
        "bg": "#000000",
        "border": "#9EA2AE",
        "text": "#FFFFFF",
        "muted": "#D6D8DE",
        "shadow": 0,
        "key_bg": "#141418",
        "key_border": "#9EA2AE",
        "key_text": "#FFFFFF",
    },
}


def _scheme():
    return "contrast" if _dark and high_contrast() else _dark

MAX_TEXT_WIDTH = 300
SHADOW = 12
RADIUS = 8

_dark = True


def set_dark(dark: bool) -> None:
    """Switches tooltip colours to the dark or light theme."""
    global _dark
    _dark = bool(dark)
    if TooltipSkin.instance is not None:
        TooltipSkin.instance.window.refresh()


def parse_tip(text: str) -> tuple[str, list[str], str]:
    """Splits a plain-text tooltip into (title, keys, body)."""
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    if not lines:
        return "", [], ""
    title = lines[0]
    body = " ".join(lines[1:])
    keys: list[str] = []
    match = SHORTCUT.search(title)
    if match:
        keys = re.split(r"\+(?=.)", match.group(1))
        title = title[: match.start()].rstrip()
    return title, keys, body


class KeyCap(QLabel):
    """One keyboard key, drawn as a small raised cap."""

    def __init__(self, key: str, colours: dict, parent=None) -> None:
        super().__init__(key, parent)
        self._c = colours
        self.setFont(ui_font(11, QFont.Weight.Medium))
        width = QFontMetrics(self.font()).horizontalAdvance(key) + 10
        self.setFixedSize(max(18, width), 18)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -1.5)
        p.setPen(QPen(QColor(self._c["key_border"]), 1))
        p.setBrush(QColor(self._c["key_border"]))
        p.drawRoundedRect(r.translated(0, 1), 4, 4)
        p.setBrush(QColor(self._c["key_bg"]))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QColor(self._c["key_text"]))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class TipWindow(QWidget):
    """The on-screen tooltip: a rounded panel with a soft shadow."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.BypassGraphicsProxyWidget,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setObjectName("riemannTooltip")
        self._text = ""
        self._dark = None
        self._content: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        self._layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text == self._text and self._dark == _scheme():
            return
        self._text = text
        self._dark = _scheme()
        if self._content is not None:
            self._layout.removeWidget(self._content)
            self._content.hide()
            self._content.deleteLater()
        self._content = self._build(text, COLOURS[_scheme()])
        self._layout.addWidget(self._content)
        self._content.show()
        self._layout.activate()
        self.adjustSize()
        self.update()

    def refresh(self) -> None:
        if self._text:
            self.set_text(self._text)

    def _build(self, text: str, c: dict) -> QWidget:
        box = QWidget(self)
        box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 7, 10, 7)
        outer.setSpacing(3)
        rich = GuiQt.mightBeRichText(text)
        title, keys, body = (text, [], "") if rich else parse_tip(text)

        row = QHBoxLayout()
        row.setSpacing(10)
        title_label = self._label(title, 13, c["text"], QFont.Weight.Medium, rich)
        row.addWidget(title_label, 1)
        if keys:
            caps = QHBoxLayout()
            caps.setSpacing(3)
            for key in keys:
                caps.addWidget(KeyCap(key, c), 0, Qt.AlignmentFlag.AlignTop)
            row.addLayout(caps)
        outer.addLayout(row)
        if body:
            outer.addWidget(self._label(body, 12, c["muted"], QFont.Weight.Normal, False))
        return box

    @staticmethod
    def _label(text: str, px: int, colour: str, weight: QFont.Weight, rich: bool) -> QLabel:
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        label.setText(text)
        label.setFont(ui_font(px, weight))
        label.setStyleSheet(f"color: {colour}; background: transparent; border: none;")
        plain = label.text() if not rich else re.sub(r"<[^>]+>", "", text)
        widest = max((QFontMetrics(label.font()).horizontalAdvance(line) for line in plain.split("\n")), default=0)
        if widest > MAX_TEXT_WIDTH:
            label.setWordWrap(True)
            label.setFixedWidth(MAX_TEXT_WIDTH)
        return label

    def panel_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)

    def place(self, panel_top_left: QPoint) -> None:
        """Moves the window so the panel's corner sits at panel_top_left, kept on screen."""
        size = self.sizeHint().expandedTo(self.size())
        target = QRect(panel_top_left - QPoint(SHADOW, SHADOW), size)
        screen = QGuiApplication.screenAt(panel_top_left) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry().adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW)
            if target.right() > area.right():
                target.moveRight(area.right())
            if target.bottom() > area.bottom():
                target.moveBottom(area.bottom())
            if target.left() < area.left():
                target.moveLeft(area.left())
            if target.top() < area.top():
                target.moveTop(area.top())
        self.move(target.topLeft())

    def attach_to(self, parent_window) -> None:
        """Gives the window a transient parent, which Wayland needs for popups."""
        if parent_window is None:
            return
        self.winId()
        handle = self.windowHandle()
        if handle is not None and handle.transientParent() is not parent_window:
            if self.isVisible():
                self.hide()
            handle.setTransientParent(parent_window)

    def paintEvent(self, event) -> None:
        c = COLOURS[_scheme()]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.panel_rect()
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(SHADOW, 0, -1):
            alpha = c["shadow"] * (1 - i / SHADOW) ** 2 / SHADOW
            p.setBrush(QColor(0, 0, 0, round(alpha)))
            p.drawRoundedRect(r.adjusted(-i, -i + 3, i, i + 3), RADIUS + i, RADIUS + i)
        p.setBrush(QColor(c["bg"]))
        p.setPen(QPen(QColor(c["border"]), 1))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        p.end()


class TooltipSkin(QObject):
    """
    Shows TipWindow instead of Qt's tooltip for the widgets it is attached to.

    It filters events of Riemann's own widgets only: attach() installs it on a
    widget tree, ChildPolished extends it to children as they appear, and new
    windows are picked up when they first take focus. Web views and everything
    inside them are skipped. An application-wide Python event filter made
    QtWebEngine crash at startup (PySide wraps every object an event reaches,
    including WebEngine's private render widgets), so there is none.
    """

    instance: "TooltipSkin | None" = None
    HIDE_AFTER_MS = 10_000
    HIDE_ON = {
        QEvent.Type.Leave,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.Wheel,
        QEvent.Type.KeyPress,
        QEvent.Type.Hide,
        QEvent.Type.FocusOut,
        QEvent.Type.WindowDeactivate,
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.window = TipWindow()
        self._owner = None
        self._attached: set[int] = set()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    @classmethod
    def install(cls, app: QApplication) -> "TooltipSkin":
        """Loads the UI font and starts skinning tooltips; idempotent."""
        if cls.instance is None:
            load_ui_font()
            cls.instance = cls(app)
            app.focusWindowChanged.connect(cls.instance._on_focus_window)
            for widget in app.topLevelWidgets():
                cls.instance.attach(widget)
        return cls.instance

    @staticmethod
    def _skipped(widget) -> bool:
        from PySide6.QtWebEngineWidgets import QWebEngineView

        return isinstance(widget, QWebEngineView) or widget.metaObject().className() == "QTipLabel"

    def attach(self, widget) -> None:
        """Skins tooltips of widget and all its descendants (web views excepted)."""
        if widget is None or not shiboken6.isValid(widget) or self._skipped(widget):
            return
        key = id(widget)
        name_from_tooltip(widget)
        if key not in self._attached:
            widget.installEventFilter(self)
            self._attached.add(key)
            widget.destroyed.connect(lambda _=None, k=key: self._attached.discard(k))
        for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            self.attach(child)

    def _on_focus_window(self, window) -> None:
        if window is None:
            return
        for widget in QApplication.topLevelWidgets():
            if widget.windowHandle() is window:
                self.attach(widget)

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            text = tooltip_text(obj, event.pos())
            if not text:
                self.hide()
                return False
            self.show_tip(obj, text, event.globalPos())
            return True
        if kind == QEvent.Type.ToolTipChange:
            name_from_tooltip(obj)
            return False
        if kind == QEvent.Type.ChildPolished:
            child = event.child()
            if child is not None and child.isWidgetType():
                self.attach(child)
            return False
        if kind in self.HIDE_ON and obj is self._owner:
            self.hide()
        return False

    def show_tip(self, owner, text: str, global_pos: QPoint) -> None:
        self._owner = owner
        window = owner.window().windowHandle() if owner.window() is not None else None
        self.window.attach_to(window)
        self.window.set_text(text)
        self.window.place(global_pos + QPoint(2, 18))
        if not self.window.isVisible():
            self.window.show()
        self._hide_timer.start(self.HIDE_AFTER_MS)

    def hide(self) -> None:
        self._owner = None
        self._hide_timer.stop()
        if self.window.isVisible():
            self.window.hide()


def tooltip_text(widget, pos: QPoint) -> str:
    """The tooltip a widget would show at pos: its own, a tab's, or an item view row's."""
    from PySide6.QtWidgets import QAbstractItemView, QTabBar

    if isinstance(widget, QTabBar):
        index = widget.tabAt(pos)
        return widget.tabToolTip(index) if index >= 0 else ""
    view = widget.parent()
    if isinstance(view, QAbstractItemView) and widget is view.viewport():
        index = view.indexAt(pos)
        if index.isValid():
            value = index.data(Qt.ItemDataRole.ToolTipRole)
            return str(value) if value else ""
        return ""
    return widget.toolTip()
