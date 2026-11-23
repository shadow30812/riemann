"""
The frame shared by System tabs: a header with the page's icon, title and a
short description, then the page itself, optionally split into sections with
a navigation list on the left.

System tabs use the palette's teal "system" accent instead of the blue one, so
they are told apart from documents and web pages at a glance; the window also
tints their tab in the tab bar. Colours come from riemann.ui.palette and follow
apply_ui_theme, which the window calls when the theme changes.

WheelGuard keeps the scroll wheel from changing settings by accident. A
drop-down, number box or slider used to take every wheel turn over it, so
scrolling down Settings changed whatever passed under the pointer (the
reader toolbar layout switched to Custom that way). Now such a control takes
the wheel only once it has focus (clicked or tabbed to); otherwise the turn
scrolls the page or list it sits in. It is installed on the page's widgets and
reaches rows added later through ChildPolished, as TooltipSkin does.
"""

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QScrollBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..palette import tinted_icon, tinted_pixmap, tokens, ui_font


WHEEL_CONTROLS = (QComboBox, QAbstractSpinBox, QAbstractSlider)


class WheelGuard(QObject):
    def attach(self, widget) -> None:
        widget.installEventFilter(self)
        if self._guarded(widget) and widget.focusPolicy() == Qt.FocusPolicy.WheelFocus:
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            self.attach(child)

    @staticmethod
    def _guarded(widget) -> bool:
        return isinstance(widget, WHEEL_CONTROLS) and not isinstance(widget, QScrollBar)

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ChildPolished:
            child = event.child()
            if child is not None and child.isWidgetType():
                self.attach(child)
            return False
        if kind != QEvent.Type.Wheel or not self._guarded(obj) or obj.hasFocus():
            return False
        parent = obj.parentWidget()
        while parent is not None and not isinstance(parent, QAbstractScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            QApplication.sendEvent(parent.verticalScrollBar(), event)
        return True


class SystemTab(QWidget):
    """Base class of the pages Riemann opens as tabs instead of separate windows."""

    page = ""
    title = ""
    subtitle = ""
    icon_name = "settings"

    def __init__(self, window, parent=None) -> None:
        super().__init__(parent)
        self._window = window
        self.is_system_tab = True
        self.setObjectName("systemTab")
        self._dark = bool(getattr(window, "dark_mode", True))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("systemHeader")
        head = QHBoxLayout(self.header)
        head.setContentsMargins(28, 20, 28, 16)
        head.setSpacing(14)
        self.header_icon = QLabel()
        self.header_icon.setFixedSize(36, 36)
        self.header_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self.header_icon, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.header_title = QLabel(self.title)
        self.header_title.setObjectName("systemTitle")
        self.header_title.setFont(ui_font(22, QFont.Weight.DemiBold))
        self.header_subtitle = QLabel(self.subtitle)
        self.header_subtitle.setObjectName("systemSubtitle")
        self.header_subtitle.setWordWrap(True)
        text.addWidget(self.header_title)
        text.addWidget(self.header_subtitle)
        head.addLayout(text, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        head.addLayout(self.header_actions)
        outer.addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName("systemBody")
        outer.addWidget(self.body, 1)
        self.build(self.body)
        self.apply_ui_theme(self._dark)
        self.wheel_guard = WheelGuard(self)
        self.wheel_guard.attach(self)

    @property
    def window_ref(self):
        """The window the tab is in now (tabs can be dragged between windows)."""
        top = self.window()
        return top if hasattr(top, "tabs_main") else self._window

    def build(self, body: QWidget) -> None:
        raise NotImplementedError

    def show_section(self, key: str) -> None:
        pass

    def release(self) -> None:
        """Called when the tab closes, before it is deleted."""

    def refresh(self) -> None:
        """Re-reads what the page shows; called when the tab is shown again."""

    def tab_icon(self, dark: bool):
        return tinted_icon(self.icon_name, tokens(dark)["system"], 18)

    def apply_ui_theme(self, dark: bool) -> None:
        self._dark = bool(dark)
        t = tokens(self._dark)
        self.header_icon.setPixmap(tinted_pixmap(self.icon_name, t["system"], 26))
        self.setStyleSheet(
            f"""
            QWidget#systemTab, QWidget#systemBody, QWidget#systemPage {{ background: {t["canvas"]}; }}
            QFrame#systemHeader {{
                background: {t["surface"]};
                border: none;
                border-bottom: 1px solid {t["border"]};
                border-top: 3px solid {t["system"]};
            }}
            QLabel#systemTitle {{ color: {t["text"]}; font-size: 22px; font-weight: 600; }}
            QWidget#settingRow {{ background: transparent; }}
            QLabel#systemSubtitle {{ color: {t["muted"]}; }}
            QLabel#sectionTitle {{ color: {t["text"]}; font-size: 16px; font-weight: 600; padding-top: 6px; }}
            QLabel#hint {{ color: {t["muted"]}; font-size: 12px; }}
            QLabel#restartNote {{ color: {t["system"]}; font-size: 12px; }}
            QFrame#card {{
                background: {t["surface"]};
                border: 1px solid {t["border"]};
                border-radius: 10px;
            }}
            QListWidget#systemNav {{
                background: {t["surface"]};
                border: none;
                border-right: 1px solid {t["border"]};
                padding: 10px 8px;
                outline: none;
            }}
            QListWidget#systemNav::item {{
                color: {t["muted"]};
                padding: 8px 12px;
                border-radius: 7px;
                margin: 1px 0;
            }}
            QListWidget#systemNav::item:hover {{ background: {t["raised"]}; color: {t["text"]}; }}
            QListWidget#systemNav::item:selected {{
                background: {t["system_soft"]};
                color: {t["system"]};
                font-weight: 600;
            }}
            """
        )
        self.on_theme(self._dark)

    def on_theme(self, dark: bool) -> None:
        pass


class SectionedSystemTab(SystemTab):
    """A System tab whose page is split into sections listed on the left."""

    def build(self, body: QWidget) -> None:
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("systemNav")
        self.nav.setFixedWidth(220)
        self.nav.setIconSize(QSize(16, 16))
        self.stack = QStackedWidget()
        row.addWidget(self.nav)
        row.addWidget(self.stack, 1)
        self._sections: dict[str, int] = {}
        self._section_icons: dict[str, str] = {}
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.build_sections()
        if self.nav.count():
            self.nav.setCurrentRow(0)

    def build_sections(self) -> None:
        raise NotImplementedError

    def add_section(self, key: str, label: str, icon_name: str = "") -> QVBoxLayout:
        """Adds a scrollable section and returns the layout to fill."""
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.nav.addItem(item)
        if icon_name:
            self._section_icons[key] = icon_name
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("systemPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 32)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(page)
        self._sections[key] = self.stack.addWidget(scroll)
        return layout

    def show_section(self, key: str) -> None:
        index = self._sections.get(key)
        if index is not None:
            self.nav.setCurrentRow(index)

    def current_section(self) -> str:
        item = self.nav.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def on_theme(self, dark: bool) -> None:
        t = tokens(dark)
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            name = self._section_icons.get(item.data(Qt.ItemDataRole.UserRole))
            if name:
                item.setIcon(tinted_icon(name, t["muted"], 16))


def card(layout: QVBoxLayout, title: str = "", hint: str = "") -> QVBoxLayout:
    """Adds a titled card to a section and returns the card's own layout."""
    if title:
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
    if hint:
        note = QLabel(hint)
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)
    frame = QFrame()
    frame.setObjectName("card")
    inner = QVBoxLayout(frame)
    inner.setContentsMargins(18, 14, 18, 14)
    inner.setSpacing(10)
    layout.addWidget(frame)
    return inner


def row_container() -> QWidget:
    """A plain widget for laying out several controls in one row of a card."""
    w = QWidget()
    w.setObjectName("settingRow")
    return w


def setting_row(layout: QVBoxLayout, label: str, control: QWidget, hint: str = "") -> QWidget:
    """A labelled control inside a card: the label (and optional hint) left, the control right."""
    row = QWidget()
    row.setObjectName("settingRow")
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(1)
    name = QLabel(label)
    text.addWidget(name)
    if hint:
        note = QLabel(hint)
        note.setObjectName("hint")
        note.setWordWrap(True)
        text.addWidget(note)
    h.addLayout(text, 1)
    h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(row)
    return row
