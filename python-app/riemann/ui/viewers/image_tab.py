"""
The image tab: one picture, with zoom, fit, rotation, panning and stepping
through the other images in its folder.

Decoding runs on a DecodeWorker thread (core/images.py), so a large TIFF or
AVIF never stalls the window. Each request has a number; a result for an older
request (the person pressed Right again before the last image arrived) is
dropped. Animated GIF and WebP play through QMovie, which reads frame by frame
on the UI thread as Qt intends. An SVG is drawn again at the new size after a
zoom settles (REDRAW_MS), so it stays sharp.

ImageCanvas paints the picture itself. The view is either fitted (FIT_PAGE,
FIT_WIDTH, FIT_HEIGHT, recomputed on every resize) or a fixed scale, where 1.0
draws one image pixel per logical pixel. The offset is the visible
top-left corner in scaled image coordinates and is clamped so the picture
can't be dragged out of sight; a picture smaller than the view is centred.
Rotation turns only the view, never the file.

The background (the N key, as in the reader) cycles through the canvas colour
of the dark theme, the light theme and a checkerboard behind the picture,
which shows transparency.

Keys without modifiers are handled in keyPressEvent, as in the reader; keys
with Ctrl are QShortcuts, shared through share_keys for split view and listed
on the Help page.

Like a ReaderTab it has current_path and is_preview, and the window treats it
as a document (ui/viewers/__init__.py).
"""

import os
import re
from typing import Optional

import shiboken6
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QKeySequence, QMovie, QPainter, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.assets import icon_path
from ...core.doctypes import has_image_name
from ...core.images import DecodedImage, ImageError, decode_image, is_animation, is_vector, save_as_pdf
from ...core.settings import app_settings, get_dialog_directory, save_last_directory
from ..components import populate_open_with_menu
from ..palette import DARK, LIGHT, qcolor, toast_style, tokens
from ..shortcuts import share_keys
from ..threads import release, track
from ..toast import Toast

FIT_PAGE, FIT_WIDTH, FIT_HEIGHT = "page", "width", "height"
ZOOM_STEP = 1.25
MIN_SCALE, MAX_SCALE = 0.02, 32.0
REDRAW_MS = 150
MAX_VECTOR_SIDE = 8192
BACKGROUNDS = ("dark", "light", "checker")
CHECKER = 12


def natural_key(name: str):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def folder_images(path: str) -> list:
    """The images in path's folder, in natural name order (2 before 10)."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = [n for n in os.listdir(folder) if has_image_name(n) and os.path.isfile(os.path.join(folder, n))]
    except OSError:
        return [os.path.abspath(path)]
    return [os.path.join(folder, n) for n in sorted(names, key=natural_key)]


class DecodeWorker(QThread):
    decoded = Signal(object, int)
    failed = Signal(str, int)

    def __init__(self, path: str, frame: int, size: Optional[QSize], request: int, parent=None):
        super().__init__(parent)
        self.path, self.frame, self.size, self.request = path, frame, size, request

    def run(self) -> None:
        try:
            result = decode_image(self.path, self.frame, self.size)
        except ImageError as error:
            self.failed.emit(str(error), self.request)
            return
        self.decoded.emit(result, self.request)


class PdfWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, path: str, output: str, parent=None):
        super().__init__(parent)
        self.path, self.output = path, output

    def run(self) -> None:
        try:
            save_as_pdf(self.path, self.output)
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.finished_ok.emit(self.output)


class ImageCanvas(QWidget):
    zoom_changed = Signal(float)
    zoom_settled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.pixmap: Optional[QPixmap] = None
        self.natural = QSize()
        self.fit: Optional[str] = FIT_PAGE
        self.scale = 1.0
        self.rotation = 0
        self.offset = QPointF(0, 0)
        self.background = "dark"
        self.message = ""
        self._drag_from: Optional[QPointF] = None
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(REDRAW_MS)
        self._settle.timeout.connect(self.zoom_settled)

    def set_image(self, image: QImage, natural: QSize, keep_view: bool = False) -> None:
        self.pixmap = QPixmap.fromImage(image)
        self.natural = natural
        self.message = ""
        if not keep_view:
            self.offset = QPointF(0, 0)
        self._refit()
        self.update()

    def set_frame(self, image: QImage) -> None:
        self.pixmap = QPixmap.fromImage(image)
        self.update()

    def show_message(self, text: str) -> None:
        self.pixmap = None
        self.message = text
        self.update()

    def rotated_size(self) -> QSize:
        size = self.natural if self.natural.isValid() else QSize(1, 1)
        return size.transposed() if self.rotation % 180 else size

    def shown_size(self) -> QSize:
        size = self.rotated_size()
        return QSize(max(1, round(size.width() * self.scale)), max(1, round(size.height() * self.scale)))

    def set_fit(self, fit: Optional[str]) -> None:
        self.fit = fit
        self._refit()
        self.update()

    def _refit(self) -> None:
        if self.fit is None or not self.natural.isValid():
            self._clamp()
            return
        size = self.rotated_size()
        sx = self.width() / max(1, size.width())
        sy = self.height() / max(1, size.height())
        scale = {FIT_PAGE: min(sx, sy), FIT_WIDTH: sx, FIT_HEIGHT: sy}[self.fit]
        if self.fit == FIT_PAGE:
            scale = min(scale, 1.0)
        self._set_scale(scale)
        self._clamp()

    def _set_scale(self, scale: float) -> None:
        scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        if abs(scale - self.scale) > 1e-9:
            self.scale = scale
            self.zoom_changed.emit(scale)
            self._settle.start()

    def zoom_by(self, factor: float, anchor: Optional[QPointF] = None) -> None:
        if not self.natural.isValid():
            return
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        before = self._image_point(anchor)
        self.fit = None
        self._set_scale(self.scale * factor)
        shown = self.shown_size()
        origin = self._origin()
        self.offset = QPointF(
            origin.x() + before.x() * shown.width() - anchor.x(),
            origin.y() + before.y() * shown.height() - anchor.y(),
        )
        self._clamp()
        self.update()

    def set_scale(self, scale: float) -> None:
        self.fit = None
        self._set_scale(scale)
        self._clamp()
        self.update()

    def rotate(self, degrees: int) -> None:
        self.rotation = (self.rotation + degrees) % 360
        self.offset = QPointF(0, 0)
        self._refit()
        self.update()

    def pan(self, dx: float, dy: float) -> None:
        self.offset += QPointF(dx, dy)
        self._clamp()
        self.update()

    def _origin(self) -> QPointF:
        shown = self.shown_size()
        return QPointF(max(0.0, (self.width() - shown.width()) / 2), max(0.0, (self.height() - shown.height()) / 2))

    def _image_point(self, pos: QPointF) -> QPointF:
        shown = self.shown_size()
        origin = self._origin()
        return QPointF(
            (pos.x() - origin.x() + self.offset.x()) / shown.width(),
            (pos.y() - origin.y() + self.offset.y()) / shown.height(),
        )

    def _clamp(self) -> None:
        shown = self.shown_size()
        max_x = max(0.0, shown.width() - self.width())
        max_y = max(0.0, shown.height() - self.height())
        self.offset = QPointF(min(max(self.offset.x(), 0.0), max_x), min(max(self.offset.y(), 0.0), max_y))

    def image_rect(self) -> QRectF:
        shown = self.shown_size()
        origin = self._origin()
        return QRectF(origin.x() - self.offset.x(), origin.y() - self.offset.y(), shown.width(), shown.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refit()

    def _paint_checker(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.setClipRect(rect)
        painter.fillRect(rect, qcolor(LIGHT["surface"]))
        dark = qcolor(LIGHT["border_strong"])
        left, top = int(rect.left()), int(rect.top())
        for y in range(top, int(rect.bottom()) + 1, CHECKER):
            for x in range(left + ((y - top) // CHECKER % 2) * CHECKER, int(rect.right()) + 1, CHECKER * 2):
                painter.fillRect(x, y, CHECKER, CHECKER, dark)
        painter.restore()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        theme = LIGHT if self.background == "light" else DARK
        painter.fillRect(self.rect(), qcolor(theme["canvas"]))
        if self.pixmap is None or self.pixmap.isNull():
            if self.message:
                painter.setPen(qcolor(theme["muted"]))
                painter.drawText(self.rect().adjusted(24, 24, -24, -24), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        target = self.image_rect()
        if self.background == "checker":
            self._paint_checker(painter, target.intersected(QRectF(self.rect())))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.scale < 4)
        painter.translate(target.center())
        painter.rotate(self.rotation)
        width, height = (target.height(), target.width()) if self.rotation % 180 else (target.width(), target.height())
        painter.drawPixmap(QRectF(-width / 2, -height / 2, width, height), self.pixmap, QRectF(self.pixmap.rect()))

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120
            if steps:
                self.zoom_by(ZOOM_STEP ** steps, event.position())
        else:
            delta = event.angleDelta()
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.pan(-delta.y(), 0)
            else:
                self.pan(-delta.x(), -delta.y())
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None:
            delta = event.position() - self._drag_from
            self._drag_from = event.position()
            self.pan(-delta.x(), -delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.fit is None:
            self.set_fit(FIT_PAGE)
        else:
            self.fit = None
            self.zoom_by(1.0 / self.scale, event.position())


class ImageTab(QWidget):
    is_preview = False

    def __init__(self, path: str, dark_mode: bool = False, preview: bool = False, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.current_path = os.path.abspath(path)
        self.is_preview = preview
        self.dark_mode = dark_mode
        self.decoded: Optional[DecodedImage] = None
        self.frame = 0
        self.movie: Optional[QMovie] = None
        self.lbl_toast: Optional[Toast] = None
        self._request = 0
        self._images: list = []
        self._workers: list = []
        self._pdf_worker: Optional[PdfWorker] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QWidget()
        self.toolbar.setObjectName("imageToolbar")
        self.toolbar.setFixedHeight(44)
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(8, 4, 8, 4)
        bar.setSpacing(2)
        self.canvas = ImageCanvas(self)
        self.canvas.background = "dark" if dark_mode else "light"
        self.canvas.zoom_changed.connect(self._show_zoom)
        self.canvas.zoom_settled.connect(self._redraw_vector)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)

        self.btn_prev = self._button(bar, "chevron-left.svg", "Previous image in the folder (Left)", lambda: self.step(-1))
        self.btn_next = self._button(bar, "chevron-right.svg", "Next image in the folder (Right)", lambda: self.step(1))
        self.lbl_position = QLabel()
        bar.addWidget(self.lbl_position)
        bar.addSpacing(10)
        self.btn_page_prev = self._button(bar, "chevron-up.svg", "Previous page (Page Up)", lambda: self.turn_page(-1))
        self.lbl_page = QLabel()
        bar.addWidget(self.lbl_page)
        self.btn_page_next = self._button(bar, "chevron-down.svg", "Next page (Page Down)", lambda: self.turn_page(1))
        bar.addStretch(1)
        self.btn_zoom_out = self._button(bar, "minus.svg", "Zoom out (Ctrl+-)", self.zoom_out)
        self.btn_zoom = QToolButton()
        self.btn_zoom.setToolTip("Fit and zoom")
        self.btn_zoom.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        zoom_menu = QMenu(self.btn_zoom)
        zoom_menu.addAction("Fit the whole image (A)", lambda: self.canvas.set_fit(FIT_PAGE))
        zoom_menu.addAction("Fit the width (W)", lambda: self.canvas.set_fit(FIT_WIDTH))
        zoom_menu.addAction("Fit the height (H)", lambda: self.canvas.set_fit(FIT_HEIGHT))
        zoom_menu.addAction("Actual size (Ctrl+0)", lambda: self.canvas.set_scale(1.0))
        self.btn_zoom.setMenu(zoom_menu)
        bar.addWidget(self.btn_zoom)
        self.btn_zoom_in = self._button(bar, "plus.svg", "Zoom in (Ctrl+=)", self.zoom_in)
        bar.addSpacing(10)
        self.btn_rotate_ccw = self._button(bar, "rotate-ccw.svg", "Rotate anticlockwise (Ctrl+Shift+R)", lambda: self.canvas.rotate(-90))
        self.btn_rotate_cw = self._button(bar, "rotate-cw.svg", "Rotate clockwise (Ctrl+R)", lambda: self.canvas.rotate(90))
        self.btn_background = self._button(bar, "palette.svg", "Background: dark, light or a checkerboard (N)", self.toggle_theme)
        self.btn_copy = self._button(bar, "file.svg", "Copy the image (Ctrl+C)", self.copy_image)
        self.btn_pdf = self._button(bar, "file-output.svg", "Save as PDF", self.save_as_pdf)
        self.btn_open_with = QToolButton()
        self.btn_open_with.setText("Open with")
        self.btn_open_with.setToolTip("Open with another application")
        self.btn_open_with.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        open_menu = QMenu(self.btn_open_with)
        open_menu.aboutToShow.connect(lambda: populate_open_with_menu(open_menu, self.current_path, self))
        self.btn_open_with.setMenu(open_menu)
        bar.addWidget(self.btn_open_with)
        self.btn_fullscreen = self._button(bar, "maximize.svg", "Full screen (F)", self.toggle_reader_fullscreen)

        for seq, slot in (
            ("Ctrl+C", self.copy_image),
            ("Ctrl+R", lambda: self.canvas.rotate(90)),
            ("Ctrl+Shift+R", lambda: self.canvas.rotate(-90)),
            ("Ctrl+=", self.zoom_in),
            ("Ctrl++", self.zoom_in),
            ("Ctrl+-", self.zoom_out),
            ("Ctrl+0", lambda: self.canvas.set_scale(1.0)),
        ):
            QShortcut(QKeySequence(seq), self).activated.connect(slot)
        share_keys(self)

        self.apply_ui_theme(dark_mode)
        self.load(self.current_path)

    def _button(self, bar: QHBoxLayout, icon: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setProperty("icon_name", icon)
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setIconSize(QSize(18, 18))
        button.clicked.connect(slot)
        bar.addWidget(button)
        return button

    def _update_icons(self) -> None:
        for button in self.toolbar.findChildren(QToolButton):
            name = button.property("icon_name")
            if name:
                button.setIcon(QIcon(icon_path(name, self.dark_mode)))

    def apply_ui_theme(self, dark: bool) -> None:
        self.dark_mode = dark
        t = tokens(dark)
        self.toolbar.setStyleSheet(
            f"QWidget#imageToolbar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}"
            f"QLabel {{ color: {t['muted']}; background: transparent; }}"
            f"QToolButton {{ color: {t['text']}; border: 1px solid transparent; border-radius: 4px; padding: 5px; background: transparent; }}"
            f"QToolButton:hover {{ background: {t['raised']}; }}"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        self._update_icons()

    def toggle_theme(self) -> None:
        index = BACKGROUNDS.index(self.canvas.background) if self.canvas.background in BACKGROUNDS else 0
        self.canvas.background = BACKGROUNDS[(index + 1) % len(BACKGROUNDS)]
        self.canvas.update()
        names = {"dark": "Dark background", "light": "Light background", "checker": "Checkerboard: shows transparency"}
        self.show_toast(names[self.canvas.background])

    def load(self, path: str, frame: int = 0) -> None:
        self.current_path = os.path.abspath(path)
        self._images = folder_images(self.current_path)
        self.frame = frame
        self._stop_movie()
        self.decoded = None
        self.canvas.show_message("Opening…")
        if not frame and is_animation(self.current_path):
            self._start_movie()
        else:
            self._decode(keep_view=False)
        self._update_labels()

    def _decode(self, keep_view: bool, size: Optional[QSize] = None) -> None:
        self._request += 1
        worker = DecodeWorker(self.current_path, self.frame, size or self._vector_size(), self._request, self)
        worker.decoded.connect(lambda result, request, k=keep_view: self._on_decoded(result, request, k))
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda w=worker: self._forget_worker(w))
        self._workers.append(track(worker))
        worker.start()

    def _forget_worker(self, worker: DecodeWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        if shiboken6.isValid(worker):
            worker.deleteLater()

    def _vector_size(self) -> Optional[QSize]:
        if not is_vector(self.current_path):
            return None
        if self.decoded is None:
            return None
        shown = self.canvas.shown_size()
        if self.canvas.rotation % 180:
            shown = shown.transposed()
        ratio = self.devicePixelRatioF()
        side = min(MAX_VECTOR_SIDE, round(max(shown.width(), shown.height()) * ratio))
        return QSize(side, side)

    def _redraw_vector(self) -> None:
        if self.decoded is not None and self.decoded.vector:
            self._decode(keep_view=True)

    def _on_decoded(self, result: DecodedImage, request: int, keep_view: bool) -> None:
        if request != self._request:
            return
        first = self.decoded is None
        self.decoded = result
        self.canvas.set_image(result.image, QSize(result.width, result.height), keep_view=keep_view and not first)
        if first and result.vector:
            self._redraw_vector()
        self._update_labels()

    def _on_failed(self, message: str, request: int) -> None:
        if request != self._request:
            return
        self.decoded = None
        self.canvas.show_message(f"{os.path.basename(self.current_path)} can't be shown.\n{message}")
        self._update_labels()

    def _start_movie(self) -> None:
        self.movie = QMovie(self.current_path, parent=self)
        self.movie.setCacheMode(QMovie.CacheMode.CacheNone)
        self.movie.frameChanged.connect(self._on_movie_frame)
        self.movie.start()
        if self.movie.isValid():
            image = self.movie.currentImage()
            self.decoded = DecodedImage(image, image.width(), image.height(), 1, bytes(self.movie.format()).decode().upper())
            self.canvas.set_image(image, image.size())
        else:
            self._stop_movie()
            self._decode(keep_view=False)

    def _on_movie_frame(self, _frame: int) -> None:
        if self.movie is not None:
            self.canvas.set_frame(self.movie.currentImage())

    def _stop_movie(self) -> None:
        if self.movie is not None:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None

    def toggle_animation(self) -> None:
        if self.movie is not None:
            self.movie.setPaused(self.movie.state() == QMovie.MovieState.Running)

    def _update_labels(self) -> None:
        images = self._images
        index = images.index(self.current_path) if self.current_path in images else -1
        parts = []
        if self.decoded is not None:
            parts.append(f"{self.decoded.width} × {self.decoded.height}")
            if self.decoded.format:
                parts.append(self.decoded.format)
        if len(images) > 1 and index != -1:
            parts.append(f"{index + 1} of {len(images)} in the folder")
        self.lbl_position.setText("  ·  ".join(parts))
        many = len(images) > 1
        self.btn_prev.setEnabled(many)
        self.btn_next.setEnabled(many)
        frames = self.decoded.frames if self.decoded is not None else 1
        for widget in (self.btn_page_prev, self.lbl_page, self.btn_page_next):
            widget.setVisible(frames > 1)
        self.lbl_page.setText(f"Page {self.frame + 1} of {frames}")
        self._show_zoom(self.canvas.scale)

    def _show_zoom(self, scale: float) -> None:
        self.btn_zoom.setText(f"{round(scale * 100)}%")

    def zoom_in(self) -> None:
        self.canvas.zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        self.canvas.zoom_by(1 / ZOOM_STEP)

    def step(self, delta: int) -> None:
        """Shows the image delta places along in the folder, wrapping around."""
        images = folder_images(self.current_path)
        if len(images) < 2:
            return
        index = images.index(self.current_path) if self.current_path in images else 0
        self.load(images[(index + delta) % len(images)])
        self._update_tab_title(os.path.basename(self.current_path))

    def turn_page(self, delta: int) -> None:
        if self.decoded is None or self.decoded.frames < 2:
            return
        frame = max(0, min(self.decoded.frames - 1, self.frame + delta))
        if frame != self.frame:
            self.frame = frame
            self._decode(keep_view=True)
            self._update_labels()

    def _update_tab_title(self, title: str) -> None:
        parent = self.parent()
        while parent is not None and not hasattr(parent, "setTabText"):
            parent = parent.parent()
        if parent is None:
            return
        idx = parent.indexOf(self)
        if idx == -1:
            return
        prefix = "Preview: " if parent.tabText(idx).startswith("Preview: ") else ""
        parent.setTabText(idx, prefix + title)
        window = self.window()
        if hasattr(window, "_update_tab_tooltip"):
            window._update_tab_tooltip(parent, idx)
        if hasattr(window, "_update_window_title"):
            window._update_window_title()

    def copy_image(self) -> None:
        pixmap = self.canvas.pixmap
        if pixmap is None or pixmap.isNull():
            return
        QApplication.clipboard().setPixmap(pixmap)
        self.show_toast("Copied the image")

    def save_as_pdf(self) -> None:
        if self._pdf_worker is not None:
            return
        settings = app_settings()
        base = os.path.splitext(os.path.basename(self.current_path))[0] + ".pdf"
        output, _ = QFileDialog.getSaveFileName(
            self, "Save as PDF", os.path.join(get_dialog_directory(settings), base), "PDF Files (*.pdf)"
        )
        if not output:
            return
        if not output.lower().endswith(".pdf"):
            output += ".pdf"
        save_last_directory(settings, output)
        worker = PdfWorker(self.current_path, output, self)
        worker.finished_ok.connect(lambda path: self.show_toast(f"Saved {os.path.basename(path)}"))
        worker.failed.connect(lambda message: self.show_toast(f"Couldn't save the PDF: {message}"))
        worker.finished.connect(self._pdf_done)
        self._pdf_worker = track(worker)
        worker.start()

    def _pdf_done(self) -> None:
        if self._pdf_worker is not None and shiboken6.isValid(self._pdf_worker):
            self._pdf_worker.deleteLater()
        self._pdf_worker = None

    def toggle_reader_fullscreen(self) -> None:
        window = self.window()
        if hasattr(window, "toggle_reader_fullscreen"):
            window.toggle_reader_fullscreen()

    def update_fullscreen_icon(self, state: int) -> None:
        icons = {0: "maximize.svg", 1: "maximize.svg", 2: "minimize.svg"}
        self.btn_fullscreen.setProperty("icon_name", icons.get(state, "maximize.svg"))
        self._update_icons()

    def show_toast(self, message: str) -> None:
        if self.lbl_toast is None:
            self.lbl_toast = Toast(self)
        self.lbl_toast.show_message(message, toast_style(tokens(self.canvas.background != "light")), 2500)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            super().keyPressEvent(event)
            return
        actions = {
            Qt.Key.Key_Left: lambda: self.step(-1),
            Qt.Key.Key_Right: lambda: self.step(1),
            Qt.Key.Key_PageUp: lambda: self.turn_page(-1),
            Qt.Key.Key_PageDown: lambda: self.turn_page(1),
            Qt.Key.Key_A: lambda: self.canvas.set_fit(FIT_PAGE),
            Qt.Key.Key_W: lambda: self.canvas.set_fit(FIT_WIDTH),
            Qt.Key.Key_H: lambda: self.canvas.set_fit(FIT_HEIGHT),
            Qt.Key.Key_F: self.toggle_reader_fullscreen,
            Qt.Key.Key_Space: self.toggle_animation,
            Qt.Key.Key_Up: lambda: self.canvas.pan(0, -60),
            Qt.Key.Key_Down: lambda: self.canvas.pan(0, 60),
            Qt.Key.Key_Escape: self._leave_fullscreen,
        }
        action = actions.get(key)
        if action is None:
            super().keyPressEvent(event)
            return
        action()
        event.accept()

    def _leave_fullscreen(self) -> None:
        window = self.window()
        if getattr(window, "_reader_fullscreen", False) and hasattr(window, "exit_fullscreen"):
            window.exit_fullscreen()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus()

    def cleanup(self) -> None:
        self._stop_movie()
        for worker in list(self._workers):
            release(worker, worker.decoded, worker.failed, worker.finished)
        self._workers.clear()
        if self._pdf_worker is not None:
            release(self._pdf_worker, self._pdf_worker.finished_ok, self._pdf_worker.failed, self._pdf_worker.finished)
            self._pdf_worker = None
