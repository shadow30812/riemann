"""
Custom UI Widgets for the Reader Module.
"""

import os
from typing import List

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QLabel


class PageWidget(QLabel):
    """
    An optimized QLabel subclass for displaying PDF pages.
    Handles temporary painting overlays for annotations.
    """

    def __init__(self, parent=None) -> None:
        """
        Initializes the PageWidget and its drawing context states.

        Args:
            parent: The parent widget instance. Defaults to None.
        """
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.temp_points: List[QPoint] = []
        self.temp_pen = QPen()
        self.markup_rects: List[QRect] = []
        self.markup_color: QColor = QColor()
        self.signature_overlays: List[dict] = []
        self.selected_text_rects: List[QRect] = []

    def set_text_selection(self, rects: List[QRect]) -> None:
        """
        Sets the text selection rectangles to be visually highlighted.

        Args:
            rects (List[QRect]): The bounding rectangles of the selected text segments.
        """
        self.selected_text_rects = rects
        self.update()

    def set_temp_stroke(
        self, points: List[QPoint], color_str: str, thickness: int, is_highlight: bool
    ) -> None:
        """
        Updates temporary stroke data and triggers a repaint event.

        Args:
            points (List[QPoint]): The sequence of coordinate points mapping the stroke.
            color_str (str): The hexadecimal color string representing the stroke line.
            thickness (int): The width/thickness of the stroke line.
            is_highlight (bool): True if the stroke represents a translucent highlight context.
        """
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
        """
        Updates text selection preview rectangles for document markup display.

        Args:
            rects (List[QRect]): A list of rectangles determining the highlight bounds.
            color (QColor): The color instance to apply to the highlighted regions.
        """
        self.markup_rects = rects
        self.markup_color = color
        self.update()

    def clear_temp_stroke(self) -> None:
        """
        Clears all temporary visual strokes and selection preview rectangles.
        """
        self.temp_points = []
        self.markup_rects = []
        self.update()

    def paintEvent(self, event) -> None:
        """
        Draws the cached PDF image alongside any temporary graphical overlays or signature bounds.

        Args:
            event: The paint event triggered by the Qt framework signaling drawing routines.
        """
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
            painter.setBrush(QColor(255, 255, 255, 255))
            painter.drawRect(rect)

            if status == "VALID":
                color = QColor(46, 125, 50)
                msg = "Signature Valid"
            elif status == "UNKNOWN":
                color = QColor(245, 127, 23)
                msg = "Identity Unknown"
            else:
                color = QColor(198, 40, 40)
                msg = "Invalid / Modified"

            painter.setPen(QPen(color, 3))
            painter.drawRect(rect)
            painter.setPen(color)

            font = painter.font()
            font.setPointSize(max(8, int(rect.height() / 6)))
            font.setBold(True)
            painter.setFont(font)

            text_rect = rect.adjusted(5, 5, -5, -5)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                f"{msg}\n{subject}",
            )

        if hasattr(self, "selected_text_rects") and self.selected_text_rects:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 120, 215, 80))
            for rect in self.selected_text_rects:
                painter.drawRect(rect)

        painter.end()

    def set_signature_overlays(self, overlays: List[dict]) -> None:
        """
        Updates the visual validation bounds and status indicators for embedded digital signatures.

        Args:
            overlays (List[dict]): A list of dictionaries containing signature properties and bounding boxes.
        """
        self.signature_overlays = overlays
        self.update()
