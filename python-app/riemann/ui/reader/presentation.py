"""
Presentation mode: one page at a time, full screen, on black.

Keys: Right, Down, Space, Page Down or a left click for the next page; Left,
Up, Backspace, Page Up or a right click for the previous one; Home and End; digits then
Enter to jump to a page; L toggles the laser pointer (the mouse becomes a red
dot); B and W blank the screen to black or white; Esc leaves. The page
number shows for a moment after each change.

Zoom: Ctrl+Plus and Ctrl+Minus (or Ctrl+wheel) zoom around the mouse pointer,
up to MAX_ZOOM times the fitted page; Ctrl+0 fits the page again, and so does
turning the page. While zoomed in, dragging with the left button pans; a click
without a drag still turns the page. The zoomed page is rendered once per zoom
step at full resolution, apart from the fitted pages in the cache.

Pages are rendered at the screen's full resolution with the page colours of
the original (no dark theme), and the next page is rendered ahead while the
current one is shown, so turning a page does not wait on PDFium.
"""

from PySide6.QtCore import QPoint, QPointF, QRectF, QSizeF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from ..palette import CONTENT, DARK, qcolor, ui_font
from .render_worker import page_image


class PresentationWindow(QWidget):
    LASER_RADIUS = 9
    MAX_ZOOM = 4.0
    ZOOM_STEP = 1.25
    DRAG_THRESHOLD = 6

    def __init__(self, doc, start_page: int = 0, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Riemann presentation")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.doc = doc
        self.count = int(doc.page_count)
        self.page = max(0, min(self.count - 1, start_page))
        self.laser = False
        self.blank: QColor | None = None
        self.mouse = QPointF(-100, -100)
        self.typed = ""
        self.zoom = 1.0
        self.origin = QPointF(0, 0)
        self._zoomed: tuple[int, float, QPixmap] | None = None
        self._press: QPointF | None = None
        self._dragged = False
        self._cache: dict[int, QPixmap] = {}
        self._label_timer = QTimer(self)
        self._label_timer.setSingleShot(True)
        self._label_timer.timeout.connect(self.update)
        self._ahead = QTimer(self)
        self._ahead.setSingleShot(True)
        self._ahead.timeout.connect(self._render_ahead)

    def start(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.activateWindow()
        self.go(self.page)

    def _fit_size(self, index: int) -> QSizeF:
        w_pt, h_pt = self.doc.get_page_size(index)
        fit = min(self.width() / max(1.0, w_pt), self.height() / max(1.0, h_pt))
        return QSizeF(w_pt * fit, h_pt * fit)

    def _render(self, index: int, zoom: float) -> QPixmap:
        dpr = self.devicePixelRatioF()
        w_pt, _ = self.doc.get_page_size(index)
        scale = self._fit_size(index).width() / max(1.0, w_pt) * zoom * dpr
        img = page_image(self.doc.render_page(index, scale, 0), scale)
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(dpr * img.devicePixelRatio())
        return pix

    def _pixmap(self, index: int) -> QPixmap:
        if self.zoom > 1.0 and index == self.page:
            if self._zoomed is None or self._zoomed[:2] != (index, self.zoom):
                self._zoomed = (index, self.zoom, self._render(index, self.zoom))
            return self._zoomed[2]
        cached = self._cache.get(index)
        if cached is not None:
            return cached
        pix = self._render(index, 1.0)
        self._cache[index] = pix
        for old in [k for k in self._cache if abs(k - index) > 2]:
            del self._cache[old]
        return pix

    def _render_ahead(self) -> None:
        for index in (self.page + 1, self.page - 1):
            if 0 <= index < self.count and index not in self._cache:
                self._pixmap(index)

    def page_rect(self) -> QRectF:
        """Where the current page is drawn, in widget coordinates."""
        size = self._fit_size(self.page) * self.zoom
        if self.zoom <= 1.0:
            return QRectF(QPointF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2), size)
        return QRectF(self.origin, size)

    def _clamped(self, origin: QPointF, size: QSizeF) -> QPointF:
        def axis(start: float, length: float, room: float) -> float:
            if length <= room:
                return (room - length) / 2
            return min(0.0, max(room - length, start))

        return QPointF(axis(origin.x(), size.width(), self.width()), axis(origin.y(), size.height(), self.height()))

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        """Zooms to zoom times the fitted page, keeping the page point under anchor in place."""
        zoom = max(1.0, min(self.MAX_ZOOM, zoom))
        if zoom == self.zoom:
            return
        if anchor is None or not self.rect().contains(anchor.toPoint()):
            anchor = QPointF(self.width() / 2, self.height() / 2)
        old = self.page_rect()
        fx = (anchor.x() - old.x()) / max(1.0, old.width())
        fy = (anchor.y() - old.y()) / max(1.0, old.height())
        size = self._fit_size(self.page) * zoom
        self.zoom = zoom
        self.origin = self._clamped(QPointF(anchor.x() - fx * size.width(), anchor.y() - fy * size.height()), size)
        self._zoomed = None
        self.update()

    def zoom_in(self, anchor: QPointF | None = None) -> None:
        self.set_zoom(self.zoom * self.ZOOM_STEP, anchor)

    def zoom_out(self, anchor: QPointF | None = None) -> None:
        self.set_zoom(self.zoom / self.ZOOM_STEP, anchor)

    def go(self, index: int) -> None:
        self.page = max(0, min(self.count - 1, index))
        self.blank = None
        self.zoom = 1.0
        self._zoomed = None
        self._label_timer.start(1500)
        self.update()
        self._ahead.start(50)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.blank is not None:
            p.fillRect(self.rect(), self.blank)
            p.end()
            return
        p.fillRect(self.rect(), QColor(CONTENT["stage"]))
        try:
            pix = self._pixmap(self.page)
        except Exception:
            pix = None
        if pix is not None and not pix.isNull():
            w = pix.width() / pix.devicePixelRatio()
            h = pix.height() / pix.devicePixelRatio()
            target = (
                self.page_rect() if self.zoom > 1.0 else QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
            )
            p.drawPixmap(QRectF(target.topLeft(), QSizeF(w, h)), pix, QRectF(pix.rect()))
        if self._label_timer.isActive() or self.typed:
            text = f"Go to {self.typed}" if self.typed else f"{self.page + 1} / {self.count}"
            p.setFont(ui_font(15, QFont.Weight.Medium))
            box = p.fontMetrics().boundingRect(text).adjusted(-14, -8, 14, 8)
            box.moveBottomRight(self.rect().bottomRight() - QPoint(24, 24))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(DARK["canvas"], 200))
            p.drawRoundedRect(QRectF(box), 8, 8)
            p.setPen(QColor(DARK["text"]))
            p.drawText(QRectF(box), Qt.AlignmentFlag.AlignCenter, text)
        if self.laser and self.rect().contains(self.mouse.toPoint()):
            glow = QRadialGradient(self.mouse, self.LASER_RADIUS * 2.4)
            glow.setColorAt(0.0, qcolor(CONTENT["laser"], 230))
            glow.setColorAt(0.35, qcolor(CONTENT["laser"], 170))
            glow.setColorAt(1.0, qcolor(CONTENT["laser"], 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(self.mouse, self.LASER_RADIUS * 2.4, self.LASER_RADIUS * 2.4)
        p.end()

    def resizeEvent(self, event) -> None:
        self._cache.clear()
        self._zoomed = None
        self.zoom = 1.0
        super().resizeEvent(event)

    def mouseMoveEvent(self, event) -> None:
        old = self.mouse
        self.mouse = event.position()
        if self._press is not None and self.zoom > 1.0:
            moved = self.mouse - self._press
            if self._dragged or abs(moved.x()) + abs(moved.y()) > self.DRAG_THRESHOLD:
                self._dragged = True
                self.origin = self._clamped(self.origin + (self.mouse - old), self.page_rect().size())
                self.update()
        if self.laser:
            r = self.LASER_RADIUS * 3
            self.update(QRectF(old.x() - r, old.y() - r, 2 * r, 2 * r).toAlignedRect())
            self.update(QRectF(self.mouse.x() - r, self.mouse.y() - r, 2 * r, 2 * r).toAlignedRect())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position()
            self._dragged = False
        elif event.button() == Qt.MouseButton.RightButton:
            self.go(self.page - 1)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            if not self._dragged:
                self.go(self.page + 1)
            self._press = None
            self._dragged = False

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in(event.position())
            elif event.angleDelta().y() < 0:
                self.zoom_out(event.position())
            return
        self.go(self.page + (1 if event.angleDelta().y() < 0 else -1))

    def keyPressEvent(self, event) -> None:
        key = event.key()
        K = Qt.Key
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if key in (K.Key_Plus, K.Key_Equal) and (ctrl or key == K.Key_Plus):
            self.zoom_in(self.mouse)
        elif key in (K.Key_Minus, K.Key_Underscore):
            self.zoom_out(self.mouse)
        elif key == K.Key_0 and ctrl:
            self.set_zoom(1.0)
        elif key in (K.Key_Right, K.Key_Down, K.Key_Space, K.Key_PageDown, K.Key_N):
            self.go(self.page + 1)
        elif key in (K.Key_Left, K.Key_Up, K.Key_Backspace, K.Key_PageUp, K.Key_P):
            if self.typed and key == K.Key_Backspace:
                self.typed = self.typed[:-1]
                self.update()
            else:
                self.go(self.page - 1)
        elif key == K.Key_Home:
            self.go(0)
        elif key == K.Key_End:
            self.go(self.count - 1)
        elif K.Key_0 <= key <= K.Key_9:
            self.typed += chr(key)
            self.update()
        elif key in (K.Key_Return, K.Key_Enter) and self.typed:
            self.go(int(self.typed) - 1)
            self.typed = ""
        elif key == K.Key_L:
            self.laser = not self.laser
            self.update()
        elif key == K.Key_B:
            self.blank = None if self.blank == QColor(CONTENT["stage"]) else QColor(CONTENT["stage"])
            self.update()
        elif key == K.Key_W:
            self.blank = None if self.blank == QColor(CONTENT["paper"]) else QColor(CONTENT["paper"])
            self.update()
        elif key == K.Key_Escape:
            if self.typed:
                self.typed = ""
                self.update()
            else:
                self.close()
        else:
            super().keyPressEvent(event)
