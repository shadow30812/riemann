"""
Reusable UI Components.

This module provides custom Qt widgets used throughout the Riemann application,
including a draggable tab system and a specialized annotation toolbar.
"""

import os
from typing import Optional

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QSpinBox,
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
        """Initialize the draggable tab widget."""
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setTabBar(DraggableTabBar(self))

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        """
        Accepts drag events that contain text (file paths).

        Args:
            e: The drag enter event.
        """
        if e.mimeData().hasText():
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """
        Handles dropping a file path to create a new tab.

        Note:
            Imports ReaderTab locally to avoid circular dependencies, as
            ReaderTab imports DraggableTabWidget via its parent modules.

        Args:
            event: The drop event containing the file path.
        """
        file_path = event.mimeData().text()
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

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """
        Initiates a drag operation when a tab is dragged.

        Args:
            event: The mouse move event.
        """
        if event.buttons() != Qt.MouseButton.LeftButton:
            return

        global_pos = event.globalPosition().toPoint()
        pos_in_widget = self.mapFromGlobal(global_pos)

        tab_index = self.tabAt(pos_in_widget)
        if tab_index < 0:
            return

        widget = self.parent().widget(tab_index)
        if not hasattr(widget, "current_path") or not widget.current_path:
            return

        mime = QMimeData()
        mime.setText(widget.current_path)

        drag = QDrag(self)
        drag.setMimeData(mime)

        pixmap = widget.grab()
        drag.setPixmap(pixmap.scaled(200, 150, Qt.AspectRatioMode.KeepAspectRatio))
        drag.setHotSpot(QPoint(100, 75))

        if drag.exec(Qt.DropAction.MoveAction) == Qt.DropAction.MoveAction:
            self.parent().removeTab(tab_index)

        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event):
        tab_index = self.tabAt(event.pos())
        if tab_index < 0:
            return

        widget = self.parent().widget(tab_index)
        menu = QMenu(self)

        rename_action = menu.addAction("Rename Tab (Custom)")
        revert_action = menu.addAction("Revert to Original Name")
        meta_action = None

        # Check if it's a PDF and has extracted metadata title
        if hasattr(widget, "document_metadata") and widget.document_metadata.get(
            "title"
        ):
            meta_title = widget.document_metadata["title"]
            # Truncate for the menu display so it doesn't stretch across the screen
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
            # If it's a ReaderTab (PDF)
            if hasattr(widget, "current_path") and widget.current_path:
                original_name = os.path.basename(widget.current_path)
                self.setTabText(tab_index, original_name)
            # If it's a BrowserTab
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
        """Initialize the annotation toolbar."""
        super().__init__(parent)
        self.setFixedHeight(45)
        self.setStyleSheet(self.STYLESHEET)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(4)

        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self.btn_nav = self._add_tool_btn(
            "🖱️", "nav", "Navigate / Select", layout, checked=True
        )

        self._add_separator(layout)

        self.btn_note = self._add_tool_btn("📝", "note", "Sticky Note", layout)
        self.btn_text = self._add_tool_btn("🔤", "text", "Text Label", layout)
        self.btn_pen = self._add_tool_btn("🖊️", "pen", "Freehand Pen", layout)
        self.btn_highlighter = self._add_tool_btn(
            "🖍️", "highlight", "Highlighter", layout
        )

        self._add_separator(layout)

        self.btn_markup_h = self._add_tool_btn(
            "H", "markup_highlight", "Text Highlight", layout
        )
        self.btn_markup_u = self._add_tool_btn(
            "U", "markup_underline", "Text Underline", layout
        )
        self.btn_markup_s = self._add_tool_btn(
            "S", "markup_strikeout", "Text Strikeout", layout
        )

        self._add_separator(layout)

        self.btn_shapes = QToolButton()
        self.btn_shapes.setText("□")
        self.btn_shapes.setToolTip("Shapes")
        self.btn_shapes.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        shape_menu = QMenu(self.btn_shapes)
        self._add_menu_action(shape_menu, "□ Rectangle", "rect", self.btn_shapes)
        self._add_menu_action(shape_menu, "◯ Oval", "oval", self.btn_shapes)
        self.btn_shapes.setMenu(shape_menu)
        layout.addWidget(self.btn_shapes)

        self.btn_stamps = QToolButton()
        self.btn_stamps.setText("✅")
        self.btn_stamps.setToolTip("Stamps")
        self.btn_stamps.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        stamp_menu = QMenu(self.btn_stamps)
        self._add_menu_action(stamp_menu, "✅ Tick", "stamp_tick", self.btn_stamps)
        self._add_menu_action(stamp_menu, "❌ Cross", "stamp_cross", self.btn_stamps)
        self.btn_stamps.setMenu(stamp_menu)
        layout.addWidget(self.btn_stamps)

        self._add_separator(layout)

        self.btn_eraser = self._add_tool_btn("⌫", "eraser", "Eraser", layout)

        self.btn_undo = QToolButton()
        self.btn_undo.setText("↩️")
        self.btn_undo.setToolTip("Undo")
        self.btn_undo.clicked.connect(self.undo_requested.emit)
        layout.addWidget(self.btn_undo)

        self.btn_redo = QToolButton()
        self.btn_redo.setText("↪️")
        self.btn_redo.setToolTip("Redo")
        self.btn_redo.clicked.connect(self.redo_requested.emit)
        layout.addWidget(self.btn_redo)

        self._add_separator(layout)

        self.btn_color = QToolButton()
        self.btn_color.setText("🎨")
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

    def _add_tool_btn(
        self,
        icon: str,
        tool_id: str,
        tooltip: str,
        layout: QHBoxLayout,
        checked: bool = False,
    ) -> QToolButton:
        """Helper to create and add a standard checkable tool button."""
        btn = QToolButton()
        btn.setText(icon)
        btn.setCheckable(True)
        btn.setToolTip(tooltip)
        btn.setChecked(checked)
        btn.clicked.connect(lambda: self.tool_changed.emit(tool_id))
        self.btn_group.addButton(btn)
        layout.addWidget(btn)
        return btn

    def _add_menu_action(
        self, menu: QMenu, text: str, tool_id: str, parent_btn: QToolButton
    ) -> None:
        """Helper to add actions to dropdown menus (shapes, stamps)."""
        action = QAction(text, self)
        action.triggered.connect(
            lambda: self._set_menu_tool(parent_btn, tool_id, text.split(" ")[0])
        )
        menu.addAction(action)

    def _set_menu_tool(self, btn: QToolButton, tool_id: str, icon: str) -> None:
        """Updates the parent dropdown button to reflect the selected sub-tool."""
        btn.setText(icon)

        if not btn.isChecked():
            btn.setChecked(True)
            for b in self.btn_group.buttons():
                if b != btn:
                    b.setChecked(False)

        self.tool_changed.emit(tool_id)

    def _add_separator(self, layout: QHBoxLayout) -> None:
        """Adds a visual vertical line separator."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

    def _pick_color(self) -> None:
        """Opens a color picker dialog and emits the selected color."""
        color = QColorDialog.getColor(Qt.GlobalColor.red, self, "Select Tool Color")
        if color.isValid():
            self.color_changed.emit(color.name())
