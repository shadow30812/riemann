"""
The short message shown at the bottom centre of a window, tab or reader.

Each owner keeps one Toast. A new message replaces the one on screen at once and
restarts the hide timer, so quick repeats (cycling the theme, say) never stack
labels on top of each other or vanish early on an older message's timer.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, style: str, msecs: int = 3000) -> None:
        self.setStyleSheet(style)
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(msecs)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2, parent.height() - 80)
