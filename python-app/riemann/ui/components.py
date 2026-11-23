"""
Reusable UI Components.

This module provides custom Qt widgets used throughout the Riemann application,
including a draggable tab system and a specialized annotation toolbar.

Notes:

__init__: The flags belong to the tab bar, so set them after replacing it.

DraggableTabBar: Qt draws West-side tabs with rotated text. In vertical mode
the tabs keep a fixed width and their labels and close buttons are laid out
horizontally, like a browser's vertical tab strip.

_paint_vertical: Labels are painted by hand: the style would centre them (and
on West-side tabs, rotate them).

insert_dragged_tab: The tab stays in its strip for the whole drag, so a drop
back into the same strip is a move, and a drop elsewhere takes it out first.

set_vertical_tabs: The bar must know it is vertical before the position
changes, because QTabBar measures the tabs then and keeps those sizes until
something else makes it measure again (switching tabs did, which is why the
widths used to correct themselves on the next click).

mouseMoveEvent: Pulled downwards out of a horizontal strip, or sideways out of
a vertical one. End Qt's own in-strip move before the tab is taken out.

RepaintingLineEdit: The blinking caret repaints only a narrow strip around
itself. At a fractional scale (Riemann's interface scale sets
QT_SCALE_FACTOR, and desktops scale too) that strip is rounded to whole
pixels and can miss a sliver of the caret, which then stays on screen beside
it as a second caret. Any partial repaint is widened to the whole field.
"""

import os
import shutil
import subprocess
from typing import Optional

import shiboken6
from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyleOptionTab,
    QStylePainter,
    QTabBar,
    QTabWidget,
    QToolButton,
    QWidget,
)

from .palette import CONTENT, menu_style, qcolor, tokens
from ..core.assets import icon_path
from ..core.binary import program_kind


_BROWSERS = [
    ("Google Chrome", ["google-chrome-stable", "google-chrome", "chrome"]),
    ("Mozilla Firefox", ["firefox"]),
    ("Chromium", ["chromium-browser", "chromium"]),
    ("Brave Browser", ["brave-browser", "brave"]),
    ("Microsoft Edge", ["microsoft-edge-stable", "microsoft-edge", "msedge"]),
]

_FOLDER_APPS = [
    ("Files (Nautilus)", ["nautilus"]),
    ("Dolphin", ["dolphin"]),
    ("Nemo", ["nemo"]),
    ("Thunar", ["thunar"]),
    ("Visual Studio Code", ["code"]),
]


class RepaintingLineEdit(QLineEdit):
    def paintEvent(self, event) -> None:
        if event.rect() != self.rect():
            self.update()
        super().paintEvent(event)


def _launch(parent: Optional[QWidget], cmd: str, arg: str) -> None:
    try:
        subprocess.Popen([cmd, arg])
    except Exception as e:
        QMessageBox.warning(parent, "Launch Error", f"Could not launch {cmd}:\n{e}")


def populate_open_with_menu(
    menu: QMenu, target: str, parent: Optional[QWidget] = None
) -> None:
    """
    Fills menu with ways to open target outside Riemann: the system default
    application and the browsers (or, for folders, file managers and editors)
    found on this machine. target is a local file, a folder or a URL. A
    program (core.binary.program_kind) gets no way to open it, since the
    system's default application for a program runs it.
    """
    menu.clear()
    is_url = "://" in target and not target.startswith("file://")
    if target.startswith("file://"):
        target = QUrl(target).toLocalFile()
    if not target or (not is_url and not os.path.exists(target)):
        menu.addAction("Nothing to open").setEnabled(False)
        return
    if not is_url and os.path.isfile(target) and program_kind(target):
        menu.addAction("Riemann doesn't start programs").setEnabled(False)
        return

    is_folder = not is_url and os.path.isdir(target)
    arg = target if is_url else os.path.abspath(target)
    url = QUrl(target) if is_url else QUrl.fromLocalFile(arg)

    default_label = (
        "System File Manager" if is_folder else "System Default Browser" if is_url else "System Default Application"
    )
    menu.addAction(default_label).triggered.connect(lambda: QDesktopServices.openUrl(url))
    menu.addSeparator()

    apps = _FOLDER_APPS if is_folder else _BROWSERS
    found = False
    for label, binaries in apps:
        cmd = next((b for b in binaries if shutil.which(b)), None)
        if cmd:
            menu.addAction(label).triggered.connect(
                lambda _=False, c=cmd: _launch(parent, c, arg)
            )
            found = True
    if not found:
        menu.addAction(
            "No file managers detected" if is_folder else "No web browsers detected"
        ).setEnabled(False)

    def choose_other():
        start = "/usr/bin" if os.path.isdir("/usr/bin") else ""
        app_path, _ = QFileDialog.getOpenFileName(parent, "Choose Application", start, "Executables (*)")
        if app_path:
            _launch(parent, app_path, arg)

    menu.addSeparator()
    menu.addAction("Choose Other Application...").triggered.connect(choose_other)


def build_open_with_menu(
    parent: Optional[QWidget], target: str, title: str = "Open in External Application"
) -> QMenu:
    """A submenu offering populate_open_with_menu's choices for target."""
    menu = QMenu(title, parent)
    populate_open_with_menu(menu, target, parent)
    return menu


class SplitPaneBadge(QFrame):
    """
    The chip floating at the bottom of the split-view pane not in use: it
    names the pane's tab, brings the pane into focus when clicked, and its
    close button ends the split.
    """

    activate_requested = Signal()
    close_requested = Signal()

    def __init__(self, pane: QWidget):
        super().__init__(pane)
        self.setObjectName("SplitPaneBadge")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 4, 4)
        layout.setSpacing(6)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.label = QLabel()
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setToolTip("Close split view")
        self.btn_close.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.icon)
        layout.addWidget(self.label)
        layout.addWidget(self.btn_close)
        pane.installEventFilter(self)
        self.hide()

    def update_theme(self, t: dict) -> None:
        sheet = (
            f"#SplitPaneBadge {{ background: {t['surface']}; border: 1px solid {t['border_strong']};"
            f" border-radius: 15px; }}"
            f"#SplitPaneBadge QLabel {{ color: {t['text']}; background: transparent; font-size: 12px; }}"
            f"#SplitPaneBadge QPushButton {{ color: {t['muted']}; background: transparent; border: none;"
            f" border-radius: 11px; }}"
            f"#SplitPaneBadge QPushButton:hover {{ background: {t['raised']}; color: {t['text']}; }}"
        )
        if self.styleSheet() != sheet:
            self.setStyleSheet(sheet)

    def show_for(self, title: str, icon: QIcon) -> None:
        metrics = self.label.fontMetrics()
        self.label.setText(metrics.elidedText(title, Qt.TextElideMode.ElideMiddle, 240))
        self.icon.setPixmap(icon.pixmap(16, 16) if not icon.isNull() else QPixmap())
        self.icon.setVisible(not icon.isNull())
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()

    def _place(self) -> None:
        pane = self.parentWidget()
        self.move((pane.width() - self.width()) // 2, pane.height() - self.height() - 14)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activate_requested.emit()
        super().mousePressEvent(event)


class DraggableTabWidget(QTabWidget):
    """
    A QTabWidget subclass that supports reordering tabs via drag-and-drop
    and accepts file drops to open new documents.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setTabBar(DraggableTabBar(self))
        self.setMovable(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setUsesScrollButtons(True)

    def set_vertical_tabs(self, vertical: bool) -> None:
        """Moves the tab strip to the left edge (vertical) or back to the top."""
        bar = self.tabBar()
        if isinstance(bar, DraggableTabBar):
            bar.set_vertical(vertical)
        self.setTabPosition(
            QTabWidget.TabPosition.West if vertical else QTabWidget.TabPosition.North
        )
        if isinstance(bar, DraggableTabBar):
            bar.relayout()

    def is_vertical_tabs(self) -> bool:
        return self.tabPosition() == QTabWidget.TabPosition.West

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        """Accepts file paths and Riemann tabs."""
        if e.mimeData().hasText() or e.mimeData().hasFormat(
            "application/x-riemann-tab"
        ):
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """
        A dropped Riemann tab switches the split view; a dropped file path opens a new
        document.
        """
        if event.mimeData().hasFormat("application/x-riemann-tab"):
            bar = self.tabBar()
            pos = bar.mapFrom(self, event.position().toPoint())
            if bar.rect().adjusted(0, -8, 0, 8).contains(pos):
                index = bar.drop_index(pos)
            elif DraggableTabBar._dragged_source is self:
                index = DraggableTabBar._dragged_index
            else:
                index = self.currentIndex() + 1
            if DraggableTabBar.insert_dragged_tab(self, index):
                event.acceptProposedAction()
            return

        if event.mimeData().hasText():
            file_path = event.mimeData().text()
            file_path = file_path.replace("file://", "").strip()

            if os.path.exists(file_path):
                from .reader import ReaderTab

                reader = ReaderTab()
                reader.load_document(file_path)
                self.addTab(reader, os.path.basename(file_path))
                self.setCurrentWidget(reader)
                event.acceptProposedAction()


class DraggableTabBar(QTabBar):
    """
    A custom QTabBar that allows dragging tabs out of the window or
    reordering them visually.
    """

    _dragged_widget = None
    _dragged_title = ""
    _dragged_icon = QIcon()
    _dragged_data = None
    _dragged_tooltip = ""
    _dragged_source = None
    _dragged_index = -1

    VERTICAL_TAB_WIDTH = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._vertical = False


    def set_vertical(self, vertical: bool) -> None:
        self._vertical = vertical
        self.relayout()

    def relayout(self) -> None:
        """Makes QTabBar measure every tab again; it keeps sizes from before the switch otherwise."""
        self.setElideMode(self.elideMode())
        self.updateGeometry()
        self.update()

    def is_vertical(self) -> bool:
        return self._vertical

    def _vertical_tab_height(self) -> int:
        return max(32, self.fontMetrics().height() + 18)

    def tabSizeHint(self, index: int) -> QSize:
        if self._vertical:
            return QSize(self.VERTICAL_TAB_WIDTH, self._vertical_tab_height())
        return super().tabSizeHint(index)

    def minimumTabSizeHint(self, index: int) -> QSize:
        if self._vertical:
            return QSize(self.VERTICAL_TAB_WIDTH, self._vertical_tab_height())
        return super().minimumTabSizeHint(index)

    def _tab_close_button(self, index: int) -> Optional[QWidget]:
        for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
            btn = self.tabButton(index, side)
            if btn is not None:
                return btn
        return None

    def _position_vertical_buttons(self) -> None:
        for i in range(self.count()):
            btn = self._tab_close_button(i)
            if btn is None:
                continue
            r = self.tabRect(i)
            target = QPoint(
                r.right() - btn.width() - 8, r.center().y() - btn.height() // 2 + 1
            )
            if btn.pos() != target:
                btn.move(target)

    def tabLayoutChange(self) -> None:
        super().tabLayoutChange()
        if self._vertical:
            self._position_vertical_buttons()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._vertical:
            self._position_vertical_buttons()

    def _paint_vertical(self) -> None:
        painter = QStylePainter(self)
        base_font = self.font()
        text_color = self.palette().color(QPalette.ColorRole.WindowText)
        muted_color = QColor(text_color)
        muted_color.setAlphaF(0.7)
        icon_size = self.iconSize()

        for i in range(self.count()):
            opt = QStyleOptionTab()
            self.initStyleOption(opt, i)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, opt)

            rect = QRect(self.tabRect(i))
            btn = self._tab_close_button(i)
            reserved = (btn.width() + 14) if btn is not None and btn.isVisible() else 8
            x = rect.left() + 12
            if not opt.icon.isNull():
                pix = opt.icon.pixmap(icon_size, self.devicePixelRatioF())
                painter.drawPixmap(
                    x, rect.center().y() - icon_size.height() // 2 + 1, pix
                )
                x += icon_size.width() + 8

            selected = i == self.currentIndex()
            font = QFont(base_font)
            font.setBold(selected)
            painter.setFont(font)
            painter.setPen(text_color if selected else muted_color)
            text_rect = QRect(x, rect.top(), rect.right() - reserved - x, rect.height())
            text = QFontMetrics(font).elidedText(
                self.tabText(i), Qt.TextElideMode.ElideRight, max(10, text_rect.width())
            )
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text
            )
        painter.end()
        self._position_vertical_buttons()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Remembers where a left-click started, so mouseMoveEvent can tell a drag from a click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def drop_index(self, pos: QPoint) -> int:
        """The index a tab dropped at pos should take: before the first tab whose middle is past pos."""
        for i in range(self.count()):
            center = self.tabRect(i).center()
            if (pos.y() < center.y()) if self._vertical else (pos.x() < center.x()):
                return i
        return self.count()

    @classmethod
    def insert_dragged_tab(cls, tab_widget: QTabWidget, index: int) -> bool:
        """Inserts the tab being dragged into tab_widget at index."""
        widget = cls._dragged_widget
        if widget is None:
            return False
        current = tab_widget.indexOf(widget)
        if current != -1:
            target = max(0, min(index, tab_widget.count()))
            if target > current:
                target -= 1
            tab_widget.tabBar().moveTab(current, min(target, tab_widget.count() - 1))
            tab_widget.setCurrentWidget(widget)
            cls._dragged_widget = None
            return True
        source = cls._dragged_source
        if source is not None and shiboken6.isValid(source) and source.indexOf(widget) != -1:
            source.removeTab(source.indexOf(widget))
        index = max(0, min(index, tab_widget.count()))
        idx = tab_widget.insertTab(index, widget, cls._dragged_icon, cls._dragged_title)
        tab_widget.tabBar().setTabData(idx, cls._dragged_data)
        if cls._dragged_tooltip:
            tab_widget.setTabToolTip(idx, cls._dragged_tooltip)
        tab_widget.setCurrentIndex(idx)
        cls._dragged_widget = None
        return True

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """
        Reorders tabs within the strip (Qt's movable tabs) and tears a tab off into a
        drag once it is pulled away from the strip.
        """
        super().mouseMoveEvent(event)
        if event.buttons() != Qt.MouseButton.LeftButton or not hasattr(self, "drag_start_pos"):
            return

        pos = event.position().toPoint()
        if self._vertical:
            pulled = not (-40 <= pos.x() <= self.width() + 40)
        else:
            pulled = not (-30 <= pos.y() <= self.height() + 30)
        if not pulled:
            return

        tab_index = self.tabAt(self.drag_start_pos)
        tab_widget = self.parent()
        if tab_index < 0 or not isinstance(tab_widget, QTabWidget):
            return
        widget = tab_widget.widget(tab_index)
        if not widget:
            return
        del self.drag_start_pos

        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            event.position(),
            event.globalPosition(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            event.modifiers(),
        )
        super().mouseReleaseEvent(release)
        tab_index = tab_widget.indexOf(widget)

        cls = DraggableTabBar
        cls._dragged_widget = widget
        cls._dragged_title = self.tabText(tab_index)
        cls._dragged_icon = self.tabIcon(tab_index)
        cls._dragged_data = self.tabData(tab_index)
        cls._dragged_tooltip = self.tabToolTip(tab_index)
        cls._dragged_source = tab_widget
        cls._dragged_index = tab_index

        mime = QMimeData()
        mime.setData("application/x-riemann-tab", b"tab")
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(
            widget.grab().scaled(
                200, 150, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        drag.setHotSpot(QPoint(100, 20))

        result = drag.exec(Qt.DropAction.MoveAction)

        if result == Qt.DropAction.IgnoreAction and cls._dragged_widget is widget:
            main_window = self.window()
            global_pos = QCursor.pos()
            if not main_window.geometry().contains(global_pos):
                new_window = type(main_window)()
                new_window.setGeometry(
                    global_pos.x() - 100,
                    global_pos.y() - 100,
                    main_window.width(),
                    main_window.height(),
                )
                new_window.show()
                target = new_window.findChild(QTabWidget)
                if target is not None:
                    cls.insert_dragged_tab(target, target.count())

        cls._dragged_widget = None
        cls._dragged_tooltip = ""
        cls._dragged_source = None

    def dragEnterEvent(self, event) -> None:
        """Accept the drag if it is an internal Riemann tab."""
        if event.mimeData().hasFormat("application/x-riemann-tab"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-riemann-tab"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        """Drops a dragged tab into this strip, between the tabs under the cursor."""
        if event.mimeData().hasFormat("application/x-riemann-tab"):
            tab_widget = self.parent()
            if isinstance(tab_widget, QTabWidget) and DraggableTabBar.insert_dragged_tab(
                tab_widget, self.drop_index(event.position().toPoint())
            ):
                event.acceptProposedAction()
            return
        super().dropEvent(event)

    def paintEvent(self, event) -> None:
        """
        Draws native tabs, then overlays a gradient on media-playing tabs and the
        teal tint and edge that mark System tabs.
        """
        if self._vertical:
            self._paint_vertical()
        else:
            super().paintEvent(event)
        painter = QPainter(self)
        system = QColor(tokens(bool(getattr(self.window(), "dark_mode", True)))["system"])
        for i in range(self.count()):
            if self.tabData(i) == "system":
                rect = self.tabRect(i)
                tint = QColor(system)
                tint.setAlpha(28)
                painter.fillRect(rect, tint)
                bar = rect.adjusted(6, 0, -6, 0)
                bar.setHeight(2)
                if self._vertical:
                    bar = rect.adjusted(0, 6, 0, -6)
                    bar.setWidth(3)
                painter.fillRect(bar, system)
            if self.tabData(i) == "playing":
                rect = self.tabRect(i)
                gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
                gradient.setColorAt(0, qcolor(CONTENT["playing_from"], 50))
                gradient.setColorAt(1, qcolor(CONTENT["playing_to"], 50))
                painter.fillRect(rect, gradient)
        painter.end()

    def contextMenuEvent(self, event):
        """The tab context menu, including rename."""
        tab_index = self.tabAt(event.pos())
        if tab_index < 0:
            return

        widget = self.parent().widget(tab_index)
        menu = QMenu(self)

        rename_action = menu.addAction("Rename Tab (Custom)")
        revert_action = menu.addAction("Revert to Original Name")
        meta_action = None

        if hasattr(widget, "document_metadata") and widget.document_metadata.get(
            "title"
        ):
            meta_title = widget.document_metadata["title"]
            meta_action = menu.addAction(f"Rename to '{meta_title[:30]}...'")

        action = menu.exec(event.globalPos())

        if action == rename_action:
            current_name = self.tabText(tab_index)
            new_name, ok = QInputDialog.getText(
                self, "Rename Tab", "Enter new tab name:", text=current_name
            )
            if ok and new_name.strip():
                self.setTabText(tab_index, new_name.strip())

        elif action == revert_action:
            if hasattr(widget, "current_path") and widget.current_path:
                original_name = os.path.basename(widget.current_path)
                self.setTabText(tab_index, original_name)
            elif hasattr(widget, "view") and hasattr(widget.view, "title"):
                original_name = widget.view.title()
                if not original_name:
                    original_name = "New Tab"
                self.setTabText(tab_index, original_name)

        elif meta_action and action == meta_action:
            self.setTabText(tab_index, widget.document_metadata["title"])


class AnnotationToolbar(QWidget):
    """
    Toolbar for the annotation tools. Emits tool_changed (a tool id), color_changed (a
    hex colour), thickness_changed (pixels) and apply_redactions_requested.
    """

    tool_changed = Signal(str)
    color_changed = Signal(str)
    thickness_changed = Signal(int)
    undo_requested = Signal()
    redo_requested = Signal()
    apply_redactions_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(45)
        self.update_theme(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(4)

        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)
        icon_size = QSize(20, 20)

        self.btn_nav = self._add_tool_btn(
            "cursor.svg", "nav", "Navigate / Select", layout, checked=True
        )
        self._add_separator(layout)
        self.btn_note = self._add_tool_btn(
            "sticky-note.svg", "note", "Sticky Note", layout
        )
        self.btn_text = self._add_tool_btn("type.svg", "text", "Text Label", layout)
        self.btn_pen = self._add_tool_btn("pen-line.svg", "pen", "Freehand Pen", layout)
        self.btn_highlighter = self._add_tool_btn(
            "highlighter.svg", "highlight", "Highlighter", layout
        )
        self._add_separator(layout)

        self.btn_markup_h = self._add_tool_btn(
            "highlighter.svg", "markup_highlight", "Text Highlight", layout
        )
        self.btn_markup_u = self._add_tool_btn(
            "underline.svg", "markup_underline", "Text Underline", layout
        )
        self.btn_markup_s = self._add_tool_btn(
            "strikethrough.svg", "markup_strikeout", "Text Strikeout", layout
        )

        self._add_separator(layout)

        self.btn_shapes = QToolButton()
        self.btn_shapes.setIcon(self._get_icon("square-dashed.svg"))
        self.btn_shapes.setIconSize(icon_size)
        self.btn_shapes.setToolTip("Shapes")
        self.btn_shapes.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        shape_menu = QMenu(self.btn_shapes)
        self._add_menu_action(
            shape_menu, "Rectangle", "square-dashed.svg", "rect", self.btn_shapes
        )
        self._add_menu_action(
            shape_menu, "Oval", "circle-slash.svg", "oval", self.btn_shapes
        )
        self.btn_shapes.setMenu(shape_menu)
        layout.addWidget(self.btn_shapes)

        self.btn_stamps = QToolButton()
        self.btn_stamps.setIcon(self._get_icon("check.svg"))
        self.btn_stamps.setIconSize(icon_size)
        self.btn_stamps.setToolTip("Stamps")
        self.btn_stamps.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        stamp_menu = QMenu(self.btn_stamps)
        self._add_menu_action(
            stamp_menu, "Tick", "check.svg", "stamp_tick", self.btn_stamps
        )
        self._add_menu_action(
            stamp_menu, "Cross", "x.svg", "stamp_cross", self.btn_stamps
        )
        self.btn_stamps.setMenu(stamp_menu)
        layout.addWidget(self.btn_stamps)

        self._add_separator(layout)

        self.btn_redact = self._add_tool_btn(
            "redact.svg", "redact", "Mark for redaction (drag a box)", layout
        )
        self.btn_apply_redactions = QToolButton()
        self.btn_apply_redactions.setText("Apply redactions…")
        self.btn_apply_redactions.setToolTip(
            "Remove everything under the redaction marks (takes effect on Save)"
        )
        self.btn_apply_redactions.clicked.connect(self.apply_redactions_requested.emit)
        layout.addWidget(self.btn_apply_redactions)

        self._add_separator(layout)

        self.btn_eraser = self._add_tool_btn("eraser.svg", "eraser", "Eraser", layout)

        self.btn_undo = QToolButton()
        self.btn_undo.setIcon(self._get_icon("undo.svg"))
        self.btn_undo.setIconSize(icon_size)
        self.btn_undo.setToolTip("Undo")
        self.btn_undo.clicked.connect(self.undo_requested.emit)
        layout.addWidget(self.btn_undo)

        self.btn_redo = QToolButton()
        self.btn_redo.setIcon(self._get_icon("redo.svg"))
        self.btn_redo.setIconSize(icon_size)
        self.btn_redo.setToolTip("Redo")
        self.btn_redo.clicked.connect(self.redo_requested.emit)
        layout.addWidget(self.btn_redo)

        self._add_separator(layout)

        self.btn_color = QToolButton()
        self.btn_color.setIcon(self._get_icon("palette.svg"))
        self.btn_color.setIconSize(icon_size)
        self.btn_color.setToolTip("Change Color")
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        self.spin_thick = QSpinBox()
        self.spin_thick.setRange(1, 20)
        self.spin_thick.setValue(3)
        self.spin_thick.setToolTip("Line Thickness")
        self.spin_thick.setFixedWidth(50)
        self.spin_thick.valueChanged.connect(self.thickness_changed.emit)
        layout.addWidget(self.spin_thick)

        layout.addStretch()

        for widget_class in (QPushButton, QToolButton, QComboBox):
            for w in self.findChildren(widget_class):
                w.setCursor(Qt.CursorShape.PointingHandCursor)

    def _get_icon(self, filename: str) -> QIcon:
        """The icon for filename in the window's current theme."""
        is_dark = False
        if self.parent() and hasattr(self.parent(), "theme_mode"):
            is_dark = self.parent().theme_mode != 0

        return QIcon(icon_path(filename, is_dark))

    def update_theme(self, is_dark: bool) -> None:
        t = tokens(is_dark)
        bg, fg, menu_bg, border = t["raised"], t["text"], t["surface"], t["border"]
        btn_hover, btn_checked = t["border"], t["accent_soft"]

        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg}; border-bottom: 1px solid {border}; color: {fg}; }}
            QToolButton {{ border: none; padding: 4px; border-radius: 4px; font-size: 16px; color: {fg}; }}
            QToolButton:hover {{ background-color: {btn_hover}; }}
            QToolButton:checked {{ background-color: {btn_checked}; border: 1px solid {t['accent']}; }}
            QToolButton::menu-indicator {{ image: none; }}
            QSpinBox {{ background-color: {menu_bg}; color: {fg}; selection-background-color: {t['accent']}; selection-color: {t['accent_text']}; }}
        """ + menu_style(t))

    def _update_icons(self) -> None:
        is_dark = False
        current_parent = self.parent()
        
        while current_parent:
            if hasattr(current_parent, "theme_mode"):
                is_dark = current_parent.theme_mode != 0
                break
            current_parent = current_parent.parent()

        self.update_theme(is_dark)

        self.btn_nav.setIcon(self._get_icon("browser.svg"))
        self.btn_note.setIcon(self._get_icon("sticky-note.svg"))
        self.btn_text.setIcon(self._get_icon("type.svg"))
        self.btn_pen.setIcon(self._get_icon("pen-line.svg"))
        self.btn_highlighter.setIcon(self._get_icon("highlighter.svg"))
        self.btn_markup_h.setIcon(self._get_icon("highlighter.svg"))
        self.btn_markup_u.setIcon(self._get_icon("underline.svg"))
        self.btn_markup_s.setIcon(self._get_icon("strikethrough.svg"))

        self.btn_shapes.setIcon(self._get_icon("square-dashed.svg"))
        self.btn_stamps.setIcon(self._get_icon("check.svg"))
        self.btn_redact.setIcon(self._get_icon("redact.svg"))
        self.btn_eraser.setIcon(self._get_icon("eraser.svg"))
        self.btn_undo.setIcon(self._get_icon("undo.svg"))
        self.btn_redo.setIcon(self._get_icon("redo.svg"))
        self.btn_color.setIcon(self._get_icon("palette.svg"))

        for action in self.btn_shapes.menu().actions():
            if "Rectangle" in action.text():
                action.setIcon(self._get_icon("square-dashed.svg"))
            if "Oval" in action.text():
                action.setIcon(self._get_icon("circle-slash.svg"))

        for action in self.btn_stamps.menu().actions():
            if "Tick" in action.text():
                action.setIcon(self._get_icon("check.svg"))
            if "Cross" in action.text():
                action.setIcon(self._get_icon("x.svg"))

    def _add_tool_btn(
        self,
        icon: str,
        tool_id: str,
        tooltip: str,
        layout: QHBoxLayout,
        checked: bool = False,
    ) -> QToolButton:
        btn = QToolButton()
        btn.setIcon(self._get_icon(icon))
        btn.setIconSize(QSize(20, 20))
        btn.setCheckable(True)
        btn.setToolTip(tooltip)
        btn.setChecked(checked)
        btn.clicked.connect(lambda: self.tool_changed.emit(tool_id))
        self.btn_group.addButton(btn)
        layout.addWidget(btn)
        return btn

    def _add_menu_action(
        self, menu: QMenu, text: str, icon: str, tool_id: str, parent_btn: QToolButton
    ) -> None:
        action = QAction(self._get_icon(icon), text, self)
        action.triggered.connect(lambda: self._set_menu_tool(parent_btn, tool_id, icon))
        menu.addAction(action)

    def _set_menu_tool(self, btn: QToolButton, tool_id: str, icon: str) -> None:
        """Shows the chosen sub-tool on its dropdown button and emits it."""
        btn.setIcon(self._get_icon(icon))

        if not btn.isChecked():
            btn.setChecked(True)
            for b in self.btn_group.buttons():
                if b != btn:
                    b.setChecked(False)

        self.tool_changed.emit(tool_id)

    def _add_separator(self, layout: QHBoxLayout) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

    def _pick_color(self) -> None:
        """
        Opens a system color picker dialog and emits the result if a valid selection is made.
        """
        color = QColorDialog.getColor(Qt.GlobalColor.red, self, "Select Tool Color")
        if color.isValid():
            self.color_changed.emit(color.name())
