"""
Custom UI Widgets for the Reader Module.
"""

from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLabel,
    QScrollArea,
    QToolButton,
    QWidget,
)


class DropZoneLabel(QLabel):
    """
    Interactive drop zone widget accepting dragged PDF/document files.
    """

    file_dropped = Signal(str)

    def __init__(self, text: str = "Drop PDF Here", parent=None) -> None:
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

        self.selected_text_rects: List[QRect] = []
        self.link_rects: List[tuple[QRect, str]] = []

        self.temp_shape_p1: Optional[QPoint] = None
        self.temp_shape_p2: Optional[QPoint] = None
        self.temp_shape_type: str = ""
        self.temp_shape_color: str = ""
        self.temp_shape_thick: int = 1

        self._page_pixmap: Optional[QPixmap] = None

    # The page image is drawn by paintEvent rather than by QLabel. With
    # scaledContents, QLabel converted and smooth-scaled every new pixmap on its
    # first paint whenever the render was a pixel off the slot size (rounding
    # makes that the usual case), about 12 ms per page while scrolling.

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
        """
        Draws the cached PDF image alongside any temporary graphical overlays.

        Args:
            event: The paint event triggered by the Qt framework signaling drawing routines.
        """
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

        if hasattr(self, "selected_text_rects") and self.selected_text_rects:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 120, 215, 80))
            for rect in self.selected_text_rects:
                painter.drawRect(rect)

        painter.end()


class ToolbarStrip(QScrollArea):
    """
    Holds the reader's toolbar buttons in one row that never squeezes them.
    When the row is wider than the pane, the icons shrink first; whatever
    still does not fit scrolls sideways with the mouse wheel, the edge arrows,
    or by resting the pointer near either edge.
    """

    EDGE = 36
    NORMAL_ICON = 20
    COMPACT_ICON = 16

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._compact = False
        self._normal_width = 0

        self.btn_left = QToolButton(self)
        self.btn_left.setToolTip("Scroll left")
        self.btn_right = QToolButton(self)
        self.btn_right.setToolTip("Scroll right")
        for btn, step in ((self.btn_left, -1), (self.btn_right, 1)):
            btn.setFixedSize(22, 36)
            btn.setIconSize(QSize(16, 16))
            btn.setAutoRaise(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAutoRepeat(True)
            btn.setAutoRepeatInterval(30)
            btn.clicked.connect(lambda _=False, d=step: self._scroll_by(d * 40))
            btn.hide()

        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(16)
        self._edge_timer.timeout.connect(self._edge_scroll)
        self.horizontalScrollBar().rangeChanged.connect(lambda *_: self._update_arrows())
        self.horizontalScrollBar().valueChanged.connect(lambda *_: self._update_arrows())

    def set_arrow_color(self, color: str) -> None:
        from ..palette import tinted_icon

        self.btn_left.setIcon(tinted_icon("chevron-left", color, 16))
        self.btn_right.setIcon(tinted_icon("chevron-right", color, 16))

    def _scroll_by(self, dx: int) -> None:
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() + dx)

    def _update_arrows(self) -> None:
        bar = self.horizontalScrollBar()
        h = self.viewport().height()
        self.btn_left.move(0, (h - self.btn_left.height()) // 2)
        self.btn_right.move(self.width() - self.btn_right.width(), (h - self.btn_right.height()) // 2)
        self.btn_left.setVisible(bar.value() > bar.minimum())
        self.btn_right.setVisible(bar.value() < bar.maximum())
        self.btn_left.raise_()
        self.btn_right.raise_()

    def _set_compact(self, compact: bool) -> None:
        self._compact = compact
        size = self.COMPACT_ICON if compact else self.NORMAL_ICON
        inner = self.widget()
        if inner is None:
            return
        for btn in inner.findChildren(QAbstractButton):
            if btn.iconSize().width() in (self.NORMAL_ICON, self.COMPACT_ICON):
                btn.setIconSize(QSize(size, size))
        if inner.layout() is not None:
            inner.layout().setSpacing(2 if compact else 6)

    def fit(self) -> None:
        """Switches between full-size and compact icons to suit the width available."""
        inner = self.widget()
        if inner is None:
            return
        available = self.viewport().width()
        if not self._compact:
            self._normal_width = inner.sizeHint().width()
            if self._normal_width > available:
                self._set_compact(True)
        elif self._normal_width and self._normal_width <= available:
            self._set_compact(False)
        self._update_arrows()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.fit)

    def wheelEvent(self, event) -> None:
        delta = event.pixelDelta()
        step = delta.x() or delta.y()
        if not step:
            angle = event.angleDelta()
            step = (angle.x() or angle.y()) // 2
        if step and self.horizontalScrollBar().maximum() > 0:
            self._scroll_by(-step)
            event.accept()
        else:
            super().wheelEvent(event)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._edge_timer.start()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._edge_timer.stop()

    def _edge_scroll(self) -> None:
        """Scrolls while the pointer rests near an edge that hides more buttons."""
        pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(pos):
            self._edge_timer.stop()
            return
        bar = self.horizontalScrollBar()
        if bar.maximum() <= 0:
            return
        if pos.x() < self.EDGE and bar.value() > 0:
            self._scroll_by(-max(2, (self.EDGE - pos.x()) // 4))
        elif pos.x() > self.width() - self.EDGE and bar.value() < bar.maximum():
            self._scroll_by(max(2, (pos.x() - self.width() + self.EDGE) // 4))

