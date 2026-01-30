import os
from typing import Optional

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import QTabBar, QTabWidget, QWidget

# Local import inside methods to avoid circular dependency
# from ui.reader import ReaderTab


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
            # Import locally to avoid circular dependency with ReaderTab
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
