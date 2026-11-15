"""
Presentation mode: one page at a time, full screen, on black.

Keys: Right, Down, Space, Page Down or a left click for the next page; Left,
Up, Backspace, Page Up or a right click for the previous one; Home and End; digits then
Enter to jump to a page; L toggles the laser pointer (the mouse becomes a red
dot); B and W blank the screen to black or white; Esc leaves. The page
number shows for a moment after each change.

Pages are rendered at the screen's full resolution with the page colours of
the original (no dark theme), and the next page is rendered ahead while the
current one is shown, so turning a page does not wait on PDFium.
"""

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from ..palette import CONTENT, DARK, qcolor, ui_font
from .render_worker import page_image


class PresentationWindow(QWidget):
    LASER_RADIUS = 9

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

    def _pixmap(self, index: int) -> QPixmap:
        cached = self._cache.get(index)
        if cached is not None:
            return cached
        dpr = self.devicePixelRatioF()
        w_pt, h_pt = self.doc.get_page_size(index)
        scale = min(self.width() / max(1.0, w_pt), self.height() / max(1.0, h_pt)) * dpr
        img = page_image(self.doc.render_page(index, scale, 0), scale)
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(dpr * img.devicePixelRatio())
        self._cache[index] = pix
        for old in [k for k in self._cache if abs(k - index) > 2]:
            del self._cache[old]
        return pix

    def _render_ahead(self) -> None:
        for index in (self.page + 1, self.page - 1):
            if 0 <= index < self.count and index not in self._cache:
                self._pixmap(index)

    def go(self, index: int) -> None:
        self.page = max(0, min(self.count - 1, index))
        self.blank = None
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
            p.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), pix, QRectF(pix.rect()))
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
        super().resizeEvent(event)

    def mouseMoveEvent(self, event) -> None:
        old = self.mouse
        self.mouse = event.position()
        if self.laser:
            r = self.LASER_RADIUS * 3
            self.update(QRectF(old.x() - r, old.y() - r, 2 * r, 2 * r).toAlignedRect())
            self.update(QRectF(self.mouse.x() - r, self.mouse.y() - r, 2 * r, 2 * r).toAlignedRect())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.go(self.page + 1)
        elif event.button() == Qt.MouseButton.RightButton:
            self.go(self.page - 1)

    def wheelEvent(self, event) -> None:
        self.go(self.page + (1 if event.angleDelta().y() < 0 else -1))

    def keyPressEvent(self, event) -> None:
        key = event.key()
        K = Qt.Key
        if key in (K.Key_Right, K.Key_Down, K.Key_Space, K.Key_PageDown, K.Key_N):
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
