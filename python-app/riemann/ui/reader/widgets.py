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
        self.signature_overlays: List[dict] = []

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

        for overlay in getattr(self, "signature_overlays", []):
            rect = overlay["rect"]
            status = overlay["status"]
            subject = overlay["subject"]

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 220))  # White out the '?' underneath
            painter.drawRect(rect)

            if status == "VALID":
                color = QColor(46, 125, 50)  # Green
                icon = "✔️"
                msg = "Signature Valid"
            elif status == "UNKNOWN":
                color = QColor(245, 127, 23)  # Yellow
                icon = "🟨"
                msg = "Identity Unknown"
            else:
                color = QColor(198, 40, 40)  # Red
                icon = "❌"
                msg = "Invalid / Modified"

            painter.setPen(QPen(color, 3))
            painter.drawRect(rect)

            painter.setPen(color)
            font = painter.font()
            font.setPointSize(max(8, int(rect.height() / 6)))  # Scale text to box
            font.setBold(True)
            painter.setFont(font)

            text_rect = rect.adjusted(5, 5, -5, -5)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                f"{icon} {msg}\n{subject}",
            )

        painter.end()

    def set_signature_overlays(self, overlays: List[dict]) -> None:
        """Updates the Adobe-style signature visual bounds."""
        self.signature_overlays = overlays
        self.update()
