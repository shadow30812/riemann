"""
Sections and thumbnails sidebar for the PDF reader.

A narrow rail switches between the document outline (bookmarks) and page
thumbnails, like the side panel of browser PDF viewers. Thumbnails are
rendered on a worker thread, only for the rows in view.

Notes:

_highlight_section: Outlines are not always in page order, so look at every
entry; on ties the later (usually deeper) entry wins.
"""

import atexit
import queue
import threading
import weakref
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..palette import tinted_icon, tokens
from .render_worker import PAGE_IMAGE_FORMAT

THUMB_WIDTH = 132
MAX_THUMBS = 160


def read_outline(doc: Any, path: str) -> List[Tuple[int, str, int]]:
    """The outline as (depth, title, page_index) tuples, from the core or pypdf."""
    if hasattr(doc, "get_outline"):
        return [tuple(e) for e in doc.get_outline()]

    import pypdf

    reader = pypdf.PdfReader(path)
    out: List[Tuple[int, str, int]] = []

    def walk(items, depth):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(it)
            except Exception:
                page = -1
            out.append((depth, str(getattr(it, "title", "") or "").strip(), page if page is not None else -1))

    walk(reader.outline or [], 0)
    return out


class _Bridge(QObject):
    outline_ready = Signal(int, list)
    thumb_ready = Signal(int, int, QImage)


class _ThumbRenderer:
    """
    One worker thread rendering requested pages, newest document first. It is
    a daemon thread waiting on a queue, so every renderer is stopped and
    joined at interpreter exit before it can be killed inside PDFium.
    """

    live: "weakref.WeakSet[_ThumbRenderer]" = weakref.WeakSet()

    def __init__(self, bridge: _Bridge):
        _ThumbRenderer.live.add(self)
        self.bridge = bridge
        self.requests: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self.generation = 0
        self.thread: Optional[threading.Thread] = None

    def request(self, generation: int, doc: Any, page: int, theme_mode: int, dpr: float) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="riemann-thumbs", daemon=True)
            self.thread.start()
        self.requests.put((generation, doc, page, theme_mode, dpr))

    def stop(self) -> None:
        self.generation = -1
        if self.thread is not None:
            self.requests.put(None)

    @classmethod
    def stop_all(cls) -> None:
        for renderer in list(cls.live):
            renderer.stop()
            if renderer.thread is not None:
                renderer.thread.join(2)

    def _run(self) -> None:
        while True:
            job = self.requests.get()
            if job is None:
                return
            generation, doc, page, theme_mode, dpr = job
            if generation != self.generation:
                continue
            try:
                width_pt, _ = doc.get_page_size(page)
                res = doc.render_page(page, THUMB_WIDTH * dpr / max(1.0, width_pt), theme_mode)
                image = QImage(res.data, res.width, res.height, res.width * 4, PAGE_IMAGE_FORMAT).copy()
                image.setDevicePixelRatio(dpr)
            except Exception:
                continue
            try:
                self.bridge.thumb_ready.emit(generation, page, image)
            except RuntimeError:
                return


atexit.register(_ThumbRenderer.stop_all)


class PdfSidebarWidget(QWidget):
    """
    Outline and thumbnail navigation shown at the left of a reader tab.
    Thumbnails can be multi-selected; right-clicking them asks the reader for
    its page-editing menu (pages_menu_requested).
    """

    page_selected = Signal(int)
    pages_menu_requested = Signal(QPoint)

    def __init__(self, parent=None, dark_mode: bool = False):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.doc = None
        self.path = ""
        self.theme_mode = 0
        self.current_page = 0
        self.mode = "outline"
        self._generation = 0
        self._requested: set = set()
        self._rendered: Dict[int, None] = {}
        self._outline_pages: List[Tuple[int, QTreeWidgetItem]] = []

        self._bridge = _Bridge(self)
        self._bridge.outline_ready.connect(self._on_outline_ready)
        self._bridge.thumb_ready.connect(self._on_thumb_ready)
        self._renderer = _ThumbRenderer(self._bridge)

        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.setInterval(60)
        self._visible_timer.timeout.connect(self._request_visible_thumbs)

        self._build()
        self.update_theme(dark_mode)

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.rail = QWidget(self)
        self.rail.setObjectName("SidebarRail")
        self.rail.setFixedWidth(44)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(6, 8, 6, 8)
        rail_layout.setSpacing(6)

        self.btn_thumbs = QToolButton(self.rail)
        self.btn_thumbs.setToolTip("Thumbnails")
        self.btn_outline = QToolButton(self.rail)
        self.btn_outline.setToolTip("Sections")
        for btn, mode in ((self.btn_thumbs, "thumbnails"), (self.btn_outline, "outline")):
            btn.setCheckable(True)
            btn.setAutoRaise(True)
            btn.setFixedSize(32, 32)
            btn.setIconSize(QSize(18, 18))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            rail_layout.addWidget(btn)
        rail_layout.addStretch()
        layout.addWidget(self.rail)

        self.panel = QWidget(self)
        self.panel.setObjectName("SidebarPanel")
        self.panel.setFixedWidth(250)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self.stack = QStackedWidget(self.panel)

        outline_page = QWidget()
        outline_layout = QVBoxLayout(outline_page)
        outline_layout.setContentsMargins(0, 6, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.itemClicked.connect(self._on_outline_clicked)
        self.lbl_empty = QLabel("This document has no sections.")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setWordWrap(True)
        self.lbl_empty.hide()
        outline_layout.addWidget(self.tree, 1)
        outline_layout.addWidget(self.lbl_empty)
        self.stack.addWidget(outline_page)

        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListView.ViewMode.IconMode)
        self.thumbs.setFlow(QListView.Flow.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setMovement(QListView.Movement.Static)
        self.thumbs.setResizeMode(QListView.ResizeMode.Adjust)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setSpacing(6)
        self.thumbs.setIconSize(QSize(THUMB_WIDTH, int(THUMB_WIDTH * 1.42)))
        self.thumbs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumbs.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.thumbs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.thumbs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(
            lambda pos: self.pages_menu_requested.emit(self.thumbs.viewport().mapToGlobal(pos))
        )
        self.thumbs.itemClicked.connect(self._on_thumb_clicked)
        self.thumbs.verticalScrollBar().valueChanged.connect(lambda _: self._visible_timer.start())
        self.stack.addWidget(self.thumbs)

        panel_layout.addWidget(self.stack, 1)
        layout.addWidget(self.panel)

    def update_theme(self, dark: bool) -> None:
        self.dark_mode = dark
        t = tokens(dark)
        self.setStyleSheet(
            f"#SidebarRail {{ background: {t['canvas']}; border-right: 1px solid {t['border']}; }}"
            f"#SidebarPanel {{ background: {t['surface']}; border-right: 1px solid {t['border']}; }}"
            f"QToolButton {{ border: none; border-radius: 8px; background: transparent; }}"
            f"QToolButton:hover {{ background: {t['raised']}; }}"
            f"QToolButton:checked {{ background: {t['accent_soft']}; }}"
            f"QTreeWidget, QListWidget {{ background: {t['surface']}; color: {t['text']}; border: none; outline: none; }}"
            f"QTreeWidget::item {{ padding: 5px 4px; border-radius: 6px; }}"
            f"QTreeWidget::item:hover, QListWidget::item:hover {{ background: {t['raised']}; }}"
            f"QTreeWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}"
            f"QListWidget::item {{ border: 2px solid transparent; border-radius: 6px; padding: 4px; color: {t['muted']}; }}"
            f"QListWidget::item:selected {{ border-color: {t['accent']}; background: {t['accent_soft']}; color: {t['text']}; }}"
            f"QLabel {{ color: {t['faint']}; padding: 24px 12px; }}"
        )
        self.btn_thumbs.setIcon(tinted_icon("image", t["text"], 18))
        self.btn_outline.setIcon(tinted_icon("list-tree", t["text"], 18))

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.btn_thumbs.setChecked(mode == "thumbnails")
        self.btn_outline.setChecked(mode == "outline")
        self.stack.setCurrentIndex(1 if mode == "thumbnails" else 0)
        if mode == "thumbnails":
            self._scroll_thumb_into_view()
            self._visible_timer.start()

    def load_document(self, path: str, doc: Any, theme_mode: int = 0) -> None:
        """Shows the outline and page list of a newly loaded document."""
        self.path = path
        self.doc = doc
        self.theme_mode = theme_mode
        self._generation += 1
        self._renderer.generation = self._generation
        self._requested.clear()
        self._rendered.clear()
        self._outline_pages = []
        self.tree.clear()
        self.thumbs.clear()
        if doc is None:
            return

        t = tokens(self.dark_mode)
        placeholder = QPixmap(self.thumbs.iconSize())
        placeholder.fill(QColor(t["raised"]))
        self._placeholder = QIcon(placeholder)
        for i in range(doc.page_count):
            item = QListWidgetItem(self._placeholder, str(i + 1))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.thumbs.addItem(item)

        generation = self._generation
        bridge = self._bridge

        def work():
            try:
                entries = read_outline(doc, path)
            except Exception:
                entries = []
            try:
                bridge.outline_ready.emit(generation, entries)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="riemann-outline").start()
        if self.isVisible() and self.mode == "thumbnails":
            self._visible_timer.start()

    def set_theme_mode(self, theme_mode: int) -> None:
        """Re-renders thumbnails after the page colours (dark/light reading mode) change."""
        if theme_mode == self.theme_mode or self.doc is None:
            return
        self.theme_mode = theme_mode
        self._generation += 1
        self._renderer.generation = self._generation
        self._requested.clear()
        self._visible_timer.start()

    def _on_outline_ready(self, generation: int, entries: list) -> None:
        if generation != self._generation:
            return
        self.tree.clear()
        self._outline_pages = []
        parents: List[QTreeWidgetItem] = []
        for depth, title, page in entries:
            item = QTreeWidgetItem([title or "Untitled"])
            item.setToolTip(0, title)
            item.setData(0, Qt.ItemDataRole.UserRole, page)
            del parents[depth:]
            if parents:
                parents[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            parents.append(item)
            if page >= 0:
                self._outline_pages.append((page, item))
        has_outline = bool(entries)
        self.tree.setVisible(has_outline)
        self.lbl_empty.setVisible(not has_outline)
        if len(entries) <= 40:
            self.tree.expandAll()
        if not has_outline and self.mode == "outline" and self.doc is not None:
            self.set_mode("thumbnails")
        self._highlight_section()

    def _on_outline_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None and int(page) >= 0:
            self.page_selected.emit(int(page))

    def _on_thumb_clicked(self, item: QListWidgetItem) -> None:
        self.page_selected.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def set_current_page(self, page: int) -> None:
        if page == self.current_page and self.thumbs.currentRow() == page:
            return
        self.current_page = page
        if 0 <= page < self.thumbs.count():
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(page)
            self.thumbs.blockSignals(False)
            if self.isVisible() and self.mode == "thumbnails":
                self._scroll_thumb_into_view()
        self._highlight_section()

    def _scroll_thumb_into_view(self) -> None:
        item = self.thumbs.item(self.current_page)
        if item is not None:
            self.thumbs.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    def _highlight_section(self) -> None:
        """Selects the section that starts closest before (or on) the current page."""
        best, best_page = None, -1
        for page, item in self._outline_pages:
            if best_page <= page <= self.current_page:
                best, best_page = item, page
        if best is None:
            self.tree.clearSelection()
            return
        self.tree.setCurrentItem(best)
        if self.isVisible() and self.mode == "outline":
            self.tree.scrollToItem(best)

    def _request_visible_thumbs(self) -> None:
        if self.doc is None or not self.isVisible() or self.mode != "thumbnails":
            return
        count = self.thumbs.count()
        if not count:
            return
        top = self.thumbs.visualItemRect(self.thumbs.item(0)).top()
        pitch = (
            self.thumbs.visualItemRect(self.thumbs.item(1)).top() - top
            if count > 1
            else self.thumbs.viewport().height()
        )
        pitch = max(1, pitch)
        first = max(0, -top // pitch)
        last = min(count - 1, (self.thumbs.viewport().height() - top) // pitch)
        dpr = self.devicePixelRatioF()
        for page in range(max(0, first - 2), min(count, last + 3)):
            if page not in self._requested:
                self._requested.add(page)
                self._renderer.request(self._generation, self.doc, page, self.theme_mode, dpr)

    def _on_thumb_ready(self, generation: int, page: int, image: QImage) -> None:
        if generation != self._generation or page >= self.thumbs.count():
            return
        pixmap = QPixmap.fromImage(image)
        painter = QPainter(pixmap)
        painter.setPen(QColor(tokens(self.dark_mode)["border_strong"]))
        size = pixmap.deviceIndependentSize()
        painter.drawRect(0, 0, int(size.width()) - 1, int(size.height()) - 1)
        painter.end()
        self.thumbs.item(page).setIcon(QIcon(pixmap))
        self._rendered.pop(page, None)
        self._rendered[page] = None
        while len(self._rendered) > MAX_THUMBS:
            old = next(iter(self._rendered))
            del self._rendered[old]
            self._requested.discard(old)
            self.thumbs.item(old).setIcon(self._placeholder)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.mode == "thumbnails":
            self._scroll_thumb_into_view()
            self._visible_timer.start()
        else:
            self._highlight_section()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._visible_timer.start()

    def shutdown(self) -> None:
        """Stops the thumbnail thread; call when the reader tab closes."""
        self._generation += 1
        self._renderer.stop()
        self.doc = None
