"""
Reusable UI Components.

This module provides custom Qt widgets used throughout the Riemann application,
including a draggable tab system and a specialized annotation toolbar.
"""

import os
import sys
from typing import Optional

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
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
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMenu,
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


class DraggableTabWidget(QTabWidget):
    """
    A QTabWidget subclass that supports reordering tabs via drag-and-drop
    and accepts file drops to open new documents.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """
        Initializes the draggable tab widget.

        Args:
            parent (Optional[QWidget]): The parent widget, if any.
        """
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setTabBar(DraggableTabBar(self))

    def set_vertical_tabs(self, vertical: bool) -> None:
        """Moves the tab strip to the left edge (vertical) or back to the top."""
        self.setTabPosition(
            QTabWidget.TabPosition.West if vertical else QTabWidget.TabPosition.North
        )
        bar = self.tabBar()
        if isinstance(bar, DraggableTabBar):
            bar.set_vertical(vertical)

    def is_vertical_tabs(self) -> bool:
        return self.tabPosition() == QTabWidget.TabPosition.West

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        """
        Accepts drag events that contain text representing file paths
        or internal Riemann tabs.

        Args:
            e (QDragEnterEvent): The drag enter event instance.
        """
        if e.mimeData().hasText() or e.mimeData().hasFormat(
            "application/x-riemann-tab"
        ):
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """
        Handles dropping either an internal tab (to switch split views)
        or an external file path (to create a new document).

        Args:
            event (QDropEvent): The drop event containing the file path payload.
        """
        if event.mimeData().hasFormat("application/x-riemann-tab"):
            widget = DraggableTabBar._dragged_widget
            if widget:
                idx = self.addTab(
                    widget,
                    DraggableTabBar._dragged_icon,
                    DraggableTabBar._dragged_title,
                )
                self.tabBar().setTabData(idx, DraggableTabBar._dragged_data)
                self.setCurrentIndex(idx)
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

    VERTICAL_TAB_WIDTH = 220

    def __init__(self, parent=None):
        """
        Initializes the draggable tab bar component.

        Args:
            parent (Optional[QWidget]): The parent widget context, if any. Defaults to None.
        """
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._vertical = False

    # ---- Vertical mode -------------------------------------------------
    # Qt draws West-side tabs with rotated text. In vertical mode the tabs
    # keep a fixed width and their labels and close buttons are laid out
    # horizontally, like a browser's vertical tab strip.

    def set_vertical(self, vertical: bool) -> None:
        self._vertical = vertical
        self.setElideMode(
            Qt.TextElideMode.ElideRight if vertical else Qt.TextElideMode.ElideNone
        )
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

            # Labels are painted by hand: the style would centre them (and on
            # West-side tabs, rotate them).
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
        """
        Handles mouse press events to initialize drag tracking.

        Records the localized starting coordinates of a left-click, which is subsequently
        used by `mouseMoveEvent` to determine whether the user has moved the mouse far
        enough to trigger a tab drag operation.

        Args:
            event (QMouseEvent): The Qt mouse event containing interaction details.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """
        Initiates a drag operation when a tab is actively dragged by the user.

        Args:
            event (QMouseEvent): The mouse move event triggering the check.
        """
        if event.buttons() != Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return

        super().mouseMoveEvent(event)

        if hasattr(self, "drag_start_pos"):
            # Tear a tab off by dragging it away from the strip: downwards for
            # a horizontal bar, sideways for a vertical one.
            if self._vertical:
                pulled = abs(event.pos().x() - self.drag_start_pos.x()) > 60
            else:
                pulled = abs(event.pos().y() - self.drag_start_pos.y()) > 40
            if pulled:
                tab_index = self.tabAt(self.drag_start_pos)
                if tab_index < 0:
                    return

                tab_widget = self.parent()
                if not isinstance(tab_widget, QTabWidget):
                    return

                widget = tab_widget.widget(tab_index)
                if not widget:
                    return

                tab_text = self.tabText(tab_index)
                tab_icon = self.tabIcon(tab_index)
                tab_data = self.tabData(tab_index)
                tab_tooltip = self.tabToolTip(tab_index)

                DraggableTabBar._dragged_widget = widget
                DraggableTabBar._dragged_title = tab_text
                DraggableTabBar._dragged_icon = tab_icon
                DraggableTabBar._dragged_data = tab_data
                DraggableTabBar._dragged_tooltip = tab_tooltip

                mime = QMimeData()
                mime.setData("application/x-riemann-tab", b"tab")

                drag = QDrag(self)
                drag.setMimeData(mime)

                pixmap = widget.grab()
                drag.setPixmap(
                    pixmap.scaled(200, 150, Qt.AspectRatioMode.KeepAspectRatio)
                )
                drag.setHotSpot(QPoint(100, 75))

                tab_widget.removeTab(tab_index)
                result = drag.exec(Qt.DropAction.MoveAction)

                if result == Qt.DropAction.IgnoreAction:
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

                        target_tab_widget = new_window.findChild(QTabWidget)
                        if target_tab_widget:
                            new_idx = target_tab_widget.addTab(
                                widget, tab_icon, tab_text
                            )
                            target_tab_widget.tabBar().setTabData(new_idx, tab_data)
                            target_tab_widget.setTabToolTip(new_idx, tab_tooltip)
                            target_tab_widget.setCurrentIndex(new_idx)
                        else:
                            tab_widget.insertTab(tab_index, widget, tab_icon, tab_text)
                            tab_widget.setTabToolTip(tab_index, tab_tooltip)
                            tab_widget.setCurrentIndex(tab_index)

                    else:
                        tab_widget.insertTab(tab_index, widget, tab_icon, tab_text)
                        self.setTabData(tab_index, tab_data)
                        tab_widget.setTabToolTip(tab_index, tab_tooltip)
                        tab_widget.setCurrentIndex(tab_index)

                DraggableTabBar._dragged_widget = None
                DraggableTabBar._dragged_tooltip = ""

    def dragEnterEvent(self, event) -> None:
        """Accept the drag if it is an internal Riemann tab."""
        if event.mimeData().hasFormat("application/x-riemann-tab"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        """Handle dropping a tab from another window directly onto this tab bar."""
        if event.mimeData().hasFormat("application/x-riemann-tab"):
            widget = DraggableTabBar._dragged_widget
            if widget:
                tab_widget = self.parent()
                if isinstance(tab_widget, QTabWidget):
                    idx = tab_widget.addTab(
                        widget,
                        DraggableTabBar._dragged_icon,
                        DraggableTabBar._dragged_title,
                    )
                    self.setTabData(idx, DraggableTabBar._dragged_data)
                    if DraggableTabBar._dragged_tooltip:
                        tab_widget.setTabToolTip(idx, DraggableTabBar._dragged_tooltip)
                    tab_widget.setCurrentIndex(idx)
                    event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def paintEvent(self, event) -> None:
        """Draws native tabs, then overlays a gradient on media-playing tabs."""
        if self._vertical:
            self._paint_vertical()
        else:
            super().paintEvent(event)
        painter = QPainter(self)
        for i in range(self.count()):
            if self.tabData(i) == "playing":
                rect = self.tabRect(i)
                gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
                gradient.setColorAt(0, QColor(255, 69, 0, 50))
                gradient.setColorAt(1, QColor(138, 43, 226, 50))
                painter.fillRect(rect, gradient)
        painter.end()

    def contextMenuEvent(self, event):
        """
        Displays a context menu for tab manipulation, such as intelligent renaming logic.

        Args:
            event: The context menu event containing the cursor trigger location.
        """
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
            title = widget.document_metadata["title"]
            display_title = (title[:25] + "..") if len(title) > 25 else title
            self.setTabText(tab_index, display_title)


class AnnotationToolbar(QWidget):
    """
    A context-aware toolbar for PDF annotation tools.
    Emits signals when tools are selected or properties change.

    Attributes:
        tool_changed (Signal): Emitted with tool ID string when selected.
        color_changed (Signal): Emitted with hex color code string.
        thickness_changed (Signal): Emitted with integer thickness.
        undo_requested (Signal): Emitted when undo is clicked.
        redo_requested (Signal): Emitted when redo is clicked.
    """

    tool_changed = Signal(str)
    color_changed = Signal(str)
    thickness_changed = Signal(int)
    undo_requested = Signal()
    redo_requested = Signal()

    STYLESHEET = """
        QWidget { 
            background-color: #e0e0e0; 
            border-bottom: 1px solid #ccc; 
            color: #000000; 
        }
        QToolButton { 
            border: none; 
            padding: 4px; 
            border-radius: 4px; 
            font-size: 16px; 
            color: #000000;
        }
        QToolButton:hover { background-color: #d0d0d0; }
        QToolButton:checked { background-color: #b0b0b0; border: 1px solid #888; }
        QToolButton::menu-indicator { image: none; }
        QMenu { 
            background-color: #f0f0f0; 
            color: #000000; 
            border: 1px solid #888;
        }
        QMenu::item:selected { background-color: #d0d0d0; color: #000000; }
        QSpinBox { 
            background-color: #ffffff; 
            color: #000000; 
            selection-background-color: #50a0ff;
            selection-color: #ffffff;
        }
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """
        Initializes the annotation toolbar UI elements and layout configuration.

        Args:
            parent (Optional[QWidget]): The parent widget container.
        """
        super().__init__(parent)
        self.setFixedHeight(45)
        self.setStyleSheet(self.STYLESHEET)

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
        """
        Resolves the target SVG icon relative to the parent application's currently configured theme state.

        Args:
            filename (str): The original base name of the SVG asset.

        Returns:
            QIcon: A dynamically resolved Qt icon supporting themed contrast switches.
        """
        is_dark = False
        if self.parent() and hasattr(self.parent(), "theme_mode"):
            is_dark = self.parent().theme_mode != 0

        if (
            is_dark
            and filename.endswith(".svg")
            and not filename.endswith("-white.svg")
        ):
            filename = filename.replace(".svg", "-white.svg")

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_path = getattr(sys, "_MEIPASS")
            path = os.path.join(base_path, "riemann", "assets", "icons", filename)
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base_path, "assets", "icons", filename)

        if not os.path.exists(path) and "-white.svg" in filename:
            path = path.replace("-white.svg", ".svg")

        return QIcon(path)

    def update_theme(self, is_dark: bool) -> None:
        """Dynamically updates the toolbar's CSS to match the active theme."""
        bg = "#2a2a2a" if is_dark else "#e0e0e0"
        fg = "#ffffff" if is_dark else "#000000"
        menu_bg = "#333333" if is_dark else "#f0f0f0"
        border = "#555" if is_dark else "#ccc"
        btn_hover = "#444" if is_dark else "#d0d0d0"
        btn_checked = "#555" if is_dark else "#b0b0b0"

        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg}; border-bottom: 1px solid {border}; color: {fg}; }}
            QToolButton {{ border: none; padding: 4px; border-radius: 4px; font-size: 16px; color: {fg}; }}
            QToolButton:hover {{ background-color: {btn_hover}; }}
            QToolButton:checked {{ background-color: {btn_checked}; border: 1px solid #888; }}
            QToolButton::menu-indicator {{ image: none; }}
            QMenu {{ background-color: {menu_bg}; color: {fg}; border: 1px solid #888; }}
            QMenu::item:selected {{ background-color: {btn_hover}; color: {fg}; }}
            QSpinBox {{ background-color: {menu_bg}; color: {fg}; selection-background-color: #50a0ff; selection-color: #ffffff; }}
        """)

    def _update_icons(self) -> None:
        """Refreshes all annotation icons dynamically when the theme changes."""
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
        """
        Helper method to create and add a standard checkable tool button.

        Args:
            icon (str): The display text or symbol for the button.
            tool_id (str): The internal identifier for the tool to emit.
            tooltip (str): The tooltip text to display on hover.
            layout (QHBoxLayout): The layout to add the constructed button to.
            checked (bool): True if the button should be checked upon initialization.

        Returns:
            QToolButton: The configured tool button instance.
        """
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
        """
        Helper method to add selectable actions to dropdown tool menus.

        Args:
            menu (QMenu): The drop-down menu to add the action to.
            text (str): The display string for the menu option.
            tool_id (str): The internal identifier for the specific tool.
            parent_btn (QToolButton): The parent button that opened the menu.
        """
        action = QAction(self._get_icon(icon), text, self)
        action.triggered.connect(lambda: self._set_menu_tool(parent_btn, tool_id, icon))
        menu.addAction(action)

    def _set_menu_tool(self, btn: QToolButton, tool_id: str, icon: str) -> None:
        """
        Updates the parent dropdown button to reflect the actively selected sub-tool.

        Args:
            btn (QToolButton): The dropdown button representing the tool category.
            tool_id (str): The specific identifier to emit.
            icon (str): The new icon to display on the main toolbar level.
        """
        btn.setIcon(self._get_icon(icon))

        if not btn.isChecked():
            btn.setChecked(True)
            for b in self.btn_group.buttons():
                if b != btn:
                    b.setChecked(False)

        self.tool_changed.emit(tool_id)

    def _add_separator(self, layout: QHBoxLayout) -> None:
        """
        Adds a visual vertical line separator to the provided layout instance.

        Args:
            layout (QHBoxLayout): The layout receiving the separator line.
        """
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
