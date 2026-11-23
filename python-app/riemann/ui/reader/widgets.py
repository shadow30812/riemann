"""
Custom UI Widgets for the Reader Module.

Notes:

PageWidget: The page image is drawn by paintEvent rather than by QLabel. With
scaledContents, QLabel converted and smooth-scaled every new pixmap on its
first paint whenever the render was a pixel off the slot size (rounding makes
that the usual case), about 12 ms per page while scrolling.
"""


from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QLabel,
)

from ..palette import CONTENT, qcolor


class DropZoneLabel(QLabel):
    """
    Interactive drop zone widget accepting dragged PDF/document files.
    """

    file_dropped = Signal(str)

    def __init__(self, text: str = "Drop a document here", parent=None) -> None:
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.setProperty("dragActive", False)

    def _set_drag_active(self, active: bool) -> None:
        """Flips the dragActive property the start page's stylesheet keys on."""
        self.setProperty("dragActive", active)
        for w in [self, *self.findChildren(QLabel)]:
            w.style().unpolish(w)
            w.style().polish(w)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_active(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                self._set_drag_active(False)
                self.file_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


class PageWidget(QLabel):
    """
    An optimized QLabel subclass for displaying PDF pages.
    Handles temporary painting overlays for annotations.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.temp_points: list[QPoint] = []
        self.temp_pen = QPen()
        self.markup_rects: list[QRect] = []
        self.markup_color: QColor = QColor()

        self.selected_text_rects: list[QRect] = []
        self.link_rects: list[tuple[QRect, str]] = []

        self.temp_shape_p1: QPoint | None = None
        self.temp_shape_p2: QPoint | None = None
        self.temp_shape_type: str = ""
        self.temp_shape_color: str = ""
        self.temp_shape_thick: int = 1

        self._page_pixmap: QPixmap | None = None


    def setPixmap(self, pixmap: QPixmap) -> None:
        super().clear()
        self._page_pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self.update()

    def pixmap(self) -> QPixmap:
        return self._page_pixmap if self._page_pixmap is not None else QPixmap()

    def clear(self) -> None:
        self._page_pixmap = None
        super().clear()

    def setText(self, text: str) -> None:
        self._page_pixmap = None
        super().setText(text)

    def _draw_page_pixmap(self, painter: QPainter) -> None:
        pix = self._page_pixmap
        if pix is None:
            return
        size = pix.deviceIndependentSize()
        if abs(size.width() - self.width()) <= 2 and abs(size.height() - self.height()) <= 2:
            painter.drawPixmap(0, 0, pix)
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.rect(), pix)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def set_text_selection(self, rects: list[QRect]) -> None:
        self.selected_text_rects = rects
        self.update()

    def set_spoken_rects(self, rects: list[QRect]) -> None:
        """Marks the sentence read aloud right now (empty to clear)."""
        if rects or getattr(self, "spoken_rects", None):
            self.spoken_rects = rects
            self.update()

    def set_ruler(self, band: QRect | None) -> None:
        """Dims the page outside band, the line the reading ruler is on (None to clear)."""
        if band != getattr(self, "ruler_band", None):
            self.ruler_band = band
            self.update()

    def set_word_rects(self, rects: list[QRect]) -> None:
        """Marks the word read aloud right now, over the sentence (empty to clear)."""
        if rects or getattr(self, "word_rects", None):
            self.word_rects = rects
            self.update()

    def set_temp_stroke(
        self, points: list[QPoint], color_str: str, thickness: int, is_highlight: bool
    ) -> None:
        """Shows the stroke being drawn; is_highlight makes it translucent."""
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

    def set_markup_preview(self, rects: list[QRect], color: QColor) -> None:
        self.markup_rects = rects
        self.markup_color = color
        self.update()

    def set_shape_preview(
        self, p1: QPoint, p2: QPoint, shape_type: str, color: str, thick: int
    ) -> None:
        """Sets the preview points for dragging rectangles and ovals."""
        self.temp_shape_p1 = p1
        self.temp_shape_p2 = p2
        self.temp_shape_type = shape_type
        self.temp_shape_color = color
        self.temp_shape_thick = thick
        self.update()

    def clear_temp_stroke(self) -> None:
        """
        Clears all temporary visual strokes and selection preview rectangles.
        """
        self.temp_points = []
        self.markup_rects = []
        self.temp_shape_p1 = None
        self.temp_shape_p2 = None
        self.update()

    def paintEvent(self, event) -> None:
        """Draws the page image and the temporary overlays on top of it."""
        super().paintEvent(event)
        painter = QPainter(self)
        self._draw_page_pixmap(painter)

        if self.temp_points and len(self.temp_points) > 1:
            painter.setPen(self.temp_pen)
            painter.drawPolyline(QPolygon(self.temp_points))

        if self.markup_rects:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.markup_color)
            for r in self.markup_rects:
                painter.drawRect(r)

        if self.temp_shape_p1 and self.temp_shape_p2:
            painter.setPen(QPen(QColor(self.temp_shape_color), self.temp_shape_thick))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRect(self.temp_shape_p1, self.temp_shape_p2).normalized()
            if self.temp_shape_type == "rect":
                painter.drawRect(rect)
            elif self.temp_shape_type == "oval":
                painter.drawEllipse(rect)

        band = getattr(self, "ruler_band", None)
        if band is not None:
            shade = qcolor(CONTENT["ruler_shade"], 140)
            painter.fillRect(0, 0, self.width(), max(0, band.top()), shade)
            painter.fillRect(0, band.bottom() + 1, self.width(), max(0, self.height() - band.bottom() - 1), shade)
            painter.fillRect(0, band.top(), max(0, band.left()), band.height(), shade)
            painter.fillRect(
                band.right() + 1, band.top(), max(0, self.width() - band.right() - 1), band.height(), shade
            )
            painter.fillRect(band.left(), band.bottom() - 1, band.width(), 2, qcolor(CONTENT["selection"]))

        if getattr(self, "spoken_rects", None):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(CONTENT["spoken"], 70))
            for rect in self.spoken_rects:
                painter.drawRect(rect)

        if getattr(self, "word_rects", None):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(CONTENT["spoken"], 150))
            for rect in self.word_rects:
                painter.drawRect(rect)
                painter.fillRect(rect.left(), rect.bottom() - 1, rect.width(), 3, qcolor(CONTENT["spoken"]))

        if hasattr(self, "selected_text_rects") and self.selected_text_rects:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(CONTENT["selection"], 80))
            for rect in self.selected_text_rects:
                painter.drawRect(rect)

        painter.end()

