"""
Custom UI Widgets for the Reader Module.
"""

from typing import List

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QLabel


class PageWidget(QLabel):
    """
    An optimized QLabel subclass for displaying PDF pages.
    Handles temporary painting overlays for annotations.
    """

    def __init__(self, parent=None) -> None:
        """Initialize the PageWidget."""
        super().__init__(parent)
        self.temp_points: List[QPoint] = []
        self.temp_pen = QPen()
        self.markup_rects: List[QRect] = []
        self.markup_color: QColor = QColor()

    def set_temp_stroke(
        self, points: List[QPoint], color_str: str, thickness: int, is_highlight: bool
    ) -> None:
        """Updates temporary stroke data and triggers repaint."""
        self.temp_points = points
        c = QColor(color_str)
        if is_highlight:
            c.setAlpha(80)
            w = thickness * 3
        else:
            c.setAlpha(255)
            w = thickness
        self.temp_pen = QPen(
            c,
            w,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        self.update()

    def set_markup_preview(self, rects: List[QRect], color: QColor) -> None:
        """Updates text selection preview rectangles."""
        self.markup_rects = rects
        self.markup_color = color
        self.update()

    def clear_temp_stroke(self) -> None:
        """Clears all temporary visuals."""
        self.temp_points = []
        self.markup_rects = []
        self.update()

    def paintEvent(self, event) -> None:
        """Draws cached PDF image and temporary overlays."""
        super().paintEvent(event)
        painter = QPainter(self)

        if self.temp_points and len(self.temp_points) > 1:
            painter.setPen(self.temp_pen)
            painter.drawPolyline(QPolygon(self.temp_points))

        if self.markup_rects:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.markup_color)
            for r in self.markup_rects:
                painter.drawRect(r)

        painter.end()
