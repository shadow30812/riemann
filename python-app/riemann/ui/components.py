import os
from typing import Optional

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDrag, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QToolButton,
    QWidget,
)


class DraggableTabWidget(QTabWidget):
    """
    A QTabWidget subclass that allows reordering and dragging tabs.
    Supports dropping file paths to create new ReaderTabs.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setTabBar(DraggableTabBar(self))

    def dragEnterEvent(self, e) -> None:
        """Accepts drag events that contain text (file paths)."""
        if e.mimeData().hasText():
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, event) -> None:
        """Handles dropping a file path to create a new tab."""
        file_path = event.mimeData().text()
        if os.path.exists(file_path):
            from .reader import ReaderTab

            reader = ReaderTab()
            reader.load_document(file_path)
            self.addTab(reader, os.path.basename(file_path))
            self.setCurrentWidget(reader)
            event.acceptProposedAction()


class DraggableTabBar(QTabBar):
    """A QTabBar that supports dragging tabs out of the window."""

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Initiates a drag operation when a tab is dragged."""
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


class AnnotationToolbar(QWidget):
    """
    A context-aware toolbar for PDF annotation tools.
    Emits signals when tools are selected or properties change.
    """

    # Signals to notify the ReaderTab
    tool_changed = Signal(str)  # e.g., 'pen', 'eraser', 'rect'
    color_changed = Signal(str)  # Hex code e.g., '#ff0000'
    thickness_changed = Signal(int)  # Line width 1-10
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(45)
        # Background styling to differentiate it from the main toolbar
        self.setStyleSheet("""
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
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(4)

        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        # --- Section 1: Pointers ---
        self.btn_nav = self._add_tool_btn(
            "🖱️", "nav", "Navigate / Select", layout, checked=True
        )

        self._add_separator(layout)

        # --- Section 2: Core Tools ---
        self.btn_note = self._add_tool_btn("📝", "note", "Sticky Note", layout)
        self.btn_text = self._add_tool_btn("🔤", "text", "Text Label", layout)
        self.btn_pen = self._add_tool_btn("🖊️", "pen", "Freehand Pen", layout)
        self.btn_highlighter = self._add_tool_btn(
            "🖍️", "highlight", "Highlighter", layout
        )

        self._add_separator(layout)

        # --- Section 2.5: Text Markup (Sticky) ---
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

        # --- Section 3: Shapes (Dropdown) ---
        self.btn_shapes = QToolButton()
        self.btn_shapes.setText("□")
        self.btn_shapes.setToolTip("Shapes")
        self.btn_shapes.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        shape_menu = QMenu(self.btn_shapes)
        self._add_menu_action(shape_menu, "□ Rectangle", "rect", self.btn_shapes)
        self._add_menu_action(shape_menu, "◯ Oval", "oval", self.btn_shapes)
        self.btn_shapes.setMenu(shape_menu)
        layout.addWidget(self.btn_shapes)

        # --- Section 4: Stamps (Dropdown) ---
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

        # --- Section 5: Edit ---
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

        # --- Section 6: Properties ---
        # Color Picker
        self.btn_color = QToolButton()
        self.btn_color.setText("🎨")
        self.btn_color.setToolTip("Change Color")
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        # Thickness Spinner
        self.spin_thick = QSpinBox()
        self.spin_thick.setRange(1, 20)
        self.spin_thick.setValue(3)
        self.spin_thick.setToolTip("Line Thickness")
        self.spin_thick.setFixedWidth(50)
        self.spin_thick.valueChanged.connect(self.thickness_changed.emit)
        layout.addWidget(self.spin_thick)

        layout.addStretch()

    def _add_tool_btn(self, icon, tool_id, tooltip, layout, checked=False):
        btn = QToolButton()
        btn.setText(icon)
        btn.setCheckable(True)
        btn.setToolTip(tooltip)
        btn.setChecked(checked)
        btn.clicked.connect(lambda: self.tool_changed.emit(tool_id))
        self.btn_group.addButton(btn)
        layout.addWidget(btn)
        return btn

    def _add_menu_action(self, menu, text, tool_id, parent_btn):
        action = QAction(text, self)
        # When menu action is clicked, we visually select the parent button
        # and emit the specific tool ID
        action.triggered.connect(
            lambda: self._set_menu_tool(parent_btn, tool_id, text.split(" ")[0])
        )
        menu.addAction(action)

    def _set_menu_tool(self, btn, tool_id, icon):
        btn.setText(icon)
        # We manually check the parent button in the exclusive group
        # because the menu action itself isn't a checkable button
        if not btn.isChecked():
            btn.setChecked(True)
            # Add to group temporarily if needed, or just rely on manual management
            # but simplest is to manually ensure other buttons uncheck if we forced this one
            for b in self.btn_group.buttons():
                if b != btn:
                    b.setChecked(False)

        self.tool_changed.emit(tool_id)

    def _add_separator(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

    def _pick_color(self):
        color = QColorDialog.getColor(Qt.GlobalColor.red, self, "Select Tool Color")
        if color.isValid():
            self.color_changed.emit(color.name())
