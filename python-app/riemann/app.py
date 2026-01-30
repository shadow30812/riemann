import os
import sys

# PyInstaller bundle
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
    os.environ["PDFIUM_DYNAMIC_LIB_PATH"] = bundle_dir

import json
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QObject,
    QPoint,
    QSettings,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    import riemann_core
except ImportError as e:
    print(f"CRITICAL: Could not import riemann_core backend.\nError: {e}")
    sys.exit(1)


class ZoomMode(Enum):
    """
    Enumeration defining the zoom behavior of the PDF viewer.

    Attributes:
        MANUAL: Zoom level is set explicitly by the user.
        FIT_WIDTH: Zoom level automatically adjusts to fit the page width to the viewport.
        FIT_HEIGHT: Zoom level automatically adjusts to fit the page height to the viewport.
    """

    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2


class ViewMode(Enum):
    """
    Enumeration defining the rendering mode of the document.

    Attributes:
        IMAGE: Standard PDF rendering where pages are drawn as images.
        REFLOW: Text extraction mode rendered via HTML for easier reading on small screens.
    """

    IMAGE = 0
    REFLOW = 1


class ReaderTab(QWidget):
    """
    A self-contained PDF Viewer Widget.

    This class manages the rendering pipeline, navigation, state (zoom, scroll),
    and interactions (annotations, text selection) for a single open PDF document.

    Attributes:
        settings (QSettings): persistent application settings.
        engine (riemann_core.PdfEngine): The Rust-based backend engine instance.
        current_doc (riemann_core.RiemannDocument): The currently loaded PDF document object.
        page_widgets (Dict[int, QLabel]): Mapping of page indices to their display widgets.
        rendered_pages (Set[int]): Set of page indices currently holding rendered pixmaps.
        annotations (Dict): Dictionary storing user annotations.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize the ReaderTab.

        Args:
            parent: The parent widget, if any.
        """
        super().__init__(parent)

        self.settings: QSettings = QSettings("Riemann", "PDFReader")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.engine: Optional[riemann_core.PdfEngine] = None
        self.current_doc: Optional[riemann_core.RiemannDocument] = None
        self.current_path: Optional[str] = None
        self.current_page_index: int = 0

        self.dark_mode: bool = self.settings.value("darkMode", True, type=bool)
        self.zoom_mode: ZoomMode = ZoomMode.FIT_WIDTH
        self.manual_scale: float = 1.0
        self.facing_mode: bool = False
        self.continuous_scroll: bool = True
        self.view_mode: ViewMode = ViewMode.IMAGE
        self.is_annotating: bool = False

        self.search_result: Optional[
            Tuple[int, List[Tuple[float, float, float, float]]]
        ] = None

        self.page_widgets: Dict[int, QLabel] = {}
        self.rendered_pages: Set[int] = set()
        self.annotations: Dict[str, List[Dict[str, Any]]] = {}

        self.virtual_threshold: int = 300
        self._virtual_enabled: bool = False
        self._top_spacer: Optional[QWidget] = None
        self._bottom_spacer: Optional[QWidget] = None
        self._virtual_range: Tuple[int, int] = (0, 0)
        self._cached_base_size: Optional[tuple] = None

        self._init_backend()
        self.setup_ui()
        self.apply_theme()
        self._setup_scroller()

        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.setInterval(150)
        self.scroll_timer.timeout.connect(self.real_scroll_handler)

        self.shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_find.activated.connect(self.toggle_search_bar)

    def _init_backend(self) -> None:
        """Initializes the Rust-based PDF engine backend."""
        try:
            self.engine = riemann_core.PdfEngine()
        except Exception as e:
            sys.stderr.write(f"Backend Initialization Error: {e}\n")

    def setup_ui(self) -> None:
        """Constructs the visual hierarchy, toolbar, and main content area."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(50)
        t_layout = QHBoxLayout(self.toolbar)

        self.btn_reflow = QPushButton("📄/📝")
        self.btn_reflow.setToolTip("Toggle Text Reflow Mode")
        self.btn_reflow.setCheckable(True)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)
        self.btn_reflow.clicked.connect(self.toggle_view_mode)

        self.btn_facing = QPushButton("📄/📖")
        self.btn_facing.setToolTip("Toggle Facing Pages (Single / Two-Page View)")
        self.btn_facing.setCheckable(True)
        self.btn_facing.setChecked(self.facing_mode)
        self.btn_facing.clicked.connect(self.toggle_facing_mode)

        self.btn_scroll_mode = QPushButton("📄/📜")
        self.btn_scroll_mode.setToolTip("Toggle Scroll Mode (Single Page / Continuous)")
        self.btn_scroll_mode.setCheckable(True)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.btn_scroll_mode.clicked.connect(self.toggle_scroll_mode)

        self.btn_annotate = QPushButton("🖊️")
        self.btn_annotate.setToolTip("Enable/Disable Annotation Mode")
        self.btn_annotate.setCheckable(True)
        self.btn_annotate.clicked.connect(self.toggle_annotation_mode)

        self.btn_prev = QPushButton("◄")
        self.btn_prev.setToolTip("Previous Page (Left Arrow)")
        self.btn_prev.clicked.connect(self.prev_view)

        self.txt_page = QLineEdit()
        self.txt_page.setFixedWidth(50)
        self.txt_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.txt_page.setToolTip("Current Page (Type number and hit Enter)")
        self.txt_page.returnPressed.connect(self.on_page_input_return)

        self.lbl_total = QLabel("/ 0")
        self.lbl_total.setToolTip("Total Pages")

        self.btn_next = QPushButton("►")
        self.btn_next.setToolTip("Next Page (Right Arrow)")
        self.btn_next.clicked.connect(self.next_view)

        self.combo_zoom = QComboBox()
        self.combo_zoom.setEditable(True)
        self.combo_zoom.setToolTip("Zoom Level (Ctrl+Scroll)")
        self.combo_zoom.addItems(
            ["Fit Width", "Fit Height", "50%", "75%", "100%", "125%", "150%", "200%"]
        )
        self.combo_zoom.currentIndexChanged.connect(self.on_zoom_selected)
        self.combo_zoom.lineEdit().returnPressed.connect(self.on_zoom_text_entered)
        self.combo_zoom.setFixedWidth(100)
        self._sync_zoom_ui()

        self.btn_theme = QPushButton("🌓")
        self.btn_theme.setToolTip("Toggle Dark/Light Mode")
        self.btn_theme.clicked.connect(self.toggle_theme)

        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.setToolTip("Toggle Fullscreen Reader Mode")
        self.btn_fullscreen.clicked.connect(self.toggle_reader_fullscreen)

        self.btn_ocr = QPushButton("👁️")
        self.btn_ocr.setToolTip("OCR Current Page (Extract Text)")
        self.btn_ocr.clicked.connect(self.perform_ocr_current_page)

        self.btn_search = QPushButton("🔍")
        self.btn_search.setToolTip("Find in Document")
        self.btn_search.setCheckable(True)
        self.btn_search.clicked.connect(self.toggle_search_bar)

        widgets = [
            self.btn_reflow,
            self.btn_facing,
            self.btn_scroll_mode,
            self.btn_search,
            self.btn_annotate,
            self.btn_ocr,
            self.btn_prev,
            self.txt_page,
            self.lbl_total,
            self.btn_next,
            self.combo_zoom,
            self.btn_theme,
            self.btn_fullscreen,
        ]
        for w in widgets:
            t_layout.addWidget(w)
        t_layout.addStretch()
        layout.addWidget(self.toolbar)

        self.search_bar = QWidget()
        self.search_bar.setVisible(False)
        self.search_bar.setFixedHeight(45)
        self.search_bar.setStyleSheet(
            "background-color: #2a2a2a;"
            if self.dark_mode
            else "background-color: #f0f0f0;"
        )

        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(10, 5, 10, 5)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Find text...")
        self.txt_search.returnPressed.connect(self.find_next)

        self.btn_find_prev = QPushButton("▲")
        self.btn_find_prev.clicked.connect(self.find_prev)

        self.btn_find_next = QPushButton("▼")
        self.btn_find_next.clicked.connect(self.find_next)

        self.btn_close_search = QPushButton("✕")
        self.btn_close_search.setFlat(True)
        self.btn_close_search.clicked.connect(self.toggle_search_bar)

        sb_layout.addWidget(QLabel("Find:"))
        sb_layout.addWidget(self.txt_search)
        sb_layout.addWidget(self.btn_find_prev)
        sb_layout.addWidget(self.btn_find_next)
        sb_layout.addWidget(self.btn_close_search)

        layout.addWidget(self.search_bar)

        self.stack = QStackedWidget()

        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidgetResizable(True)
        self.scroll.installEventFilter(self)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(20)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scroll.setWidget(self.scroll_content)
        self.scroll.verticalScrollBar().valueChanged.connect(self.defer_scroll_update)
        self.stack.addWidget(self.scroll)

        self.web = QWebEngineView()
        self.stack.addWidget(self.web)

        layout.addWidget(self.stack)

    def _setup_scroller(self) -> None:
        """Configures the kinetic scrolling properties for the viewport."""
        QScroller.grabGesture(
            self.scroll.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        props = QScroller.scroller(self.scroll.viewport()).scrollerProperties()
        props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.5)
        props.setScrollMetric(QScrollerProperties.ScrollMetric.MaximumVelocity, 0.8)
        QScroller.scroller(self.scroll.viewport()).setScrollerProperties(props)

    def load_document(self, path: str, restore_state: bool = False) -> None:
        """
        Loads a PDF file from the specified path.

        Args:
            path: Absolute file path to the PDF.
            restore_state: If True, attempts to restore the last known page/scroll position.
        """
        try:
            self.current_doc = self.engine.load_document(path)
            self._probe_base_page_size()
            self.current_path = path
            self.settings.setValue("lastFile", path)
            self.load_annotations()

            if restore_state:
                saved_page = self.settings.value("lastPage", 0, type=int)
                saved_scroll = self.settings.value("lastScrollY", 0, type=int)
                self.current_page_index = min(
                    saved_page, self.current_doc.page_count - 1
                )
                self.rebuild_layout()
                self.update_view()
                QTimer.singleShot(
                    50, lambda: self.scroll.verticalScrollBar().setValue(saved_scroll)
                )
            else:
                self.current_page_index = 0
                self.rebuild_layout()
                self.update_view()

        except Exception as e:
            sys.stderr.write(f"Load error: {e}\n")

    def rebuild_layout(self) -> None:
        """
        Reconstructs the layout of QLabels representing the document pages.
        Implements virtualization logic for large documents when continuous scroll is enabled.
        """
        if not self.current_doc:
            return

        # [FIX] 1. Block signals to prevent 'valueChanged' from firing during the wipe
        # This prevents the app from thinking the user scrolled to 0 when we clear widgets.
        sb = self.scroll.verticalScrollBar()
        was_blocked = sb.signalsBlocked()
        sb.blockSignals(True)

        # [FIX] 2. Capture the exact pixel scroll position
        old_scroll_val = sb.value()

        self.page_widgets.clear()
        self.rendered_pages.clear()
        self._virtual_enabled = False
        self._virtual_range = (0, 0)

        # Clear existing widgets
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        count = self.current_doc.page_count
        use_virtual = self.continuous_scroll and (count > self.virtual_threshold)

        if use_virtual:
            self._virtual_enabled = True
            buf_before = 30
            buf_after = 40
            start = max(0, self.current_page_index - buf_before)
            end = min(count, self.current_page_index + buf_after)
            self._virtual_range = (start, end)

            if self._cached_base_size:
                _, base_h = self._cached_base_size
            else:
                self._probe_base_page_size()
                _, base_h = self._cached_base_size or (595, 842)

            scale = self.calculate_scale()
            page_height = int(base_h * scale) + self.scroll_layout.spacing()

            # Top Spacer
            top_spacer = QWidget()
            top_spacer.setFixedHeight(max(0, start * page_height))
            top_spacer.setObjectName("topSpacer")
            self._top_spacer = top_spacer
            self.scroll_layout.addWidget(top_spacer)

            # Page Widgets
            for p_idx in range(start, end):
                if self.facing_mode and (p_idx % 2 == 0) and (p_idx + 1 < end):
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(10)
                    row_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

                    lbl_left = self._create_page_label(p_idx)
                    row_layout.addWidget(lbl_left)
                    self.page_widgets[p_idx] = lbl_left

                    lbl_right = self._create_page_label(p_idx + 1)
                    row_layout.addWidget(lbl_right)
                    self.page_widgets[p_idx + 1] = lbl_right

                    self.scroll_layout.addWidget(row_widget)
                else:
                    if p_idx in self.page_widgets:
                        continue
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(10)
                    row_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

                    lbl = self._create_page_label(p_idx)
                    row_layout.addWidget(lbl)
                    self.page_widgets[p_idx] = lbl
                    self.scroll_layout.addWidget(row_widget)

            # Bottom Spacer
            bottom_spacer = QWidget()
            bottom_spacer.setFixedHeight(max(0, (count - end) * page_height))
            bottom_spacer.setObjectName("bottomSpacer")
            self._bottom_spacer = bottom_spacer
            self.scroll_layout.addWidget(bottom_spacer)

        else:
            # (Standard non-virtual logic)
            if self.continuous_scroll:
                pages_to_layout = range(count)
            else:
                if self.facing_mode:
                    start = (self.current_page_index // 2) * 2
                    pages_to_layout = range(start, min(start + 2, count))
                else:
                    pages_to_layout = range(
                        self.current_page_index, self.current_page_index + 1
                    )

            indices = list(pages_to_layout)
            idx_ptr = 0
            while idx_ptr < len(indices):
                p_idx = indices[idx_ptr]
                is_pair = self.facing_mode and (p_idx + 1 < count) and (p_idx % 2 == 0)

                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(10)
                row_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

                lbl_left = self._create_page_label(p_idx)
                row_layout.addWidget(lbl_left)
                self.page_widgets[p_idx] = lbl_left

                if is_pair:
                    p_idx_right = p_idx + 1
                    lbl_right = self._create_page_label(p_idx_right)
                    row_layout.addWidget(lbl_right)
                    self.page_widgets[p_idx_right] = lbl_right
                    idx_ptr += 2
                else:
                    idx_ptr += 1

                self.scroll_layout.addWidget(row_widget)

        # [FIX] 3. Force the scroll content to recalculate its height immediately
        # This ensures the maximum scroll range is updated BEFORE we try to restore the value.
        self.scroll_content.adjustSize()
        QApplication.processEvents()  # Process any pending layout requests

        # [FIX] 4. Restore the scroll position
        if self.continuous_scroll:
            sb.setValue(old_scroll_val)

        # [FIX] 5. Unblock signals so user interaction works again
        sb.blockSignals(was_blocked)

    def _create_page_label(self, index: int) -> QLabel:
        """
        Creates a placeholder QLabel for a specific page index.

        Args:
            index: The page number (0-based) this label represents.

        Returns:
            A configured QLabel instance.
        """
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setProperty("pageIndex", index)
        w, h = self._get_target_page_size()
        lbl.setFixedSize(w, h)
        lbl.setStyleSheet(
            f"background-color: {'#333' if self.dark_mode else '#fff'}; border: 1px solid #555;"
        )
        lbl.installEventFilter(self)
        return lbl

    def render_visible_pages(self) -> None:
        """
        Identifies currently visible pages and triggers their rendering.
        Evicts off-screen pages to conserve memory.
        """
        if not self.current_doc or not self.page_widgets:
            return

        target_indices: Set[int] = set()

        start = max(0, self.current_page_index - 7)
        end = min(self.current_doc.page_count, self.current_page_index + 8)

        for i in range(start, end):
            target_indices.add(i)

        for idx in list(self.rendered_pages):
            if idx not in target_indices:
                if idx in self.page_widgets:
                    self.page_widgets[idx].clear()
                    self.page_widgets[idx].setText(f"Page {idx + 1}")
                self.rendered_pages.remove(idx)

        scale = self.calculate_scale()

        for idx in target_indices:
            if idx in self.rendered_pages:
                continue
            if idx not in self.page_widgets:
                continue

            self._render_single_page(idx, scale)
            self.rendered_pages.add(idx)

    def _render_single_page(self, idx: int, scale: float) -> None:
        """
        Renders a specific page using the Rust backend and applies it to the UI.
        Handles drawing overlays such as search results and annotations.

        Args:
            idx: The 0-based index of the page to render.
            scale: The logical zoom scale to apply.
        """
        try:
            dpr = self.devicePixelRatio()
            render_scale = scale * dpr

            res = self.current_doc.render_page(
                idx, render_scale, 1 if self.dark_mode else 0
            )

            img = QImage(res.data, res.width, res.height, QImage.Format.Format_ARGB32)
            img.setDevicePixelRatio(dpr)
            pix = QPixmap.fromImage(img)

            if self.search_result and self.search_result[0] == idx:
                painter = QPainter(pix)
                color = (
                    QColor(255, 255, 0, 100)
                    if self.dark_mode
                    else QColor(255, 255, 0, 128)
                )
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)

                for left, top, right, bottom in self.search_result[1]:
                    x = left * render_scale
                    w = (right - left) * render_scale
                    h = (top - bottom) * render_scale
                    y = res.height - (top * render_scale)
                    painter.drawRect(x, y, w, h)

                painter.end()

            if str(idx) in self.annotations:
                painter = QPainter(pix)
                painter.setPen(QPen(QColor(255, 255, 0, 180), 3))
                for anno in self.annotations[str(idx)]:
                    x = int(anno["rel_pos"][0] * pix.width())
                    y = int(anno["rel_pos"][1] * pix.height())
                    painter.drawEllipse(QPoint(x, y), 10, 10)
                painter.end()

            lbl = self.page_widgets[idx]
            lbl.setPixmap(pix)
            # lbl.setMinimumSize(0, 0)

        except Exception as e:
            sys.stderr.write(f"Render error for page {idx}: {e}\n")

    def _get_target_page_size(self) -> Tuple[int, int]:
        """Calculates the target pixel dimensions for pages at current scale."""
        if not self._cached_base_size:
            return (int(595 * self.manual_scale), int(842 * self.manual_scale))

        base_w, base_h = self._cached_base_size
        scale = self.calculate_scale()
        return (int(base_w * scale), int(base_h * scale))

    def _probe_base_page_size(self) -> None:
        """Calculates and caches the base page dimensions in pixels."""
        if not self.current_doc:
            self._cached_base_size = None
            return
        try:
            res = self.current_doc.render_page(0, 1.0, 0)
            self._cached_base_size = (res.width, res.height)
        except Exception:
            self._cached_base_size = (595, 842)

    def calculate_scale(self) -> float:
        """
        Computes the current rendering scale factor based on view mode and window size.

        Returns:
            float: The scale factor (1.0 = 100%).
        """
        if self.zoom_mode == ZoomMode.MANUAL:
            return self.manual_scale

        if not self._cached_base_size:
            try:
                self._probe_base_page_size()
            except Exception:
                return 1.0

        if not self._cached_base_size:
            return 1.0

        base_w, base_h = self._cached_base_size
        viewport = self.scroll.viewport()
        vw = max(10, viewport.width() - 30)
        vh = max(10, viewport.height() - 20)

        if self.facing_mode and self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / (base_w * 2)
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / base_w
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            return vh / base_h

        return 1.0

    def update_view(self) -> None:
        """Triggers a full refresh of the view (Render or Reflow)."""
        if self.view_mode == ViewMode.IMAGE:
            self.render_visible_pages()
            if self.current_doc:
                self.txt_page.setText(str(self.current_page_index + 1))
                self.lbl_total.setText(f"/ {self.current_doc.page_count}")

            self.settings.setValue("lastPage", self.current_page_index)
            self.settings.setValue(
                "lastScrollY", self.scroll.verticalScrollBar().value()
            )
        else:
            self.render_reflow()

    def render_reflow(self) -> None:
        """Performs text extraction and renders content via the WebEngine."""
        if not self.current_doc:
            return

        try:
            text = self.current_doc.get_page_text(self.current_page_index)
            bg = "#1e1e1e" if self.dark_mode else "#fff"
            fg = "#ddd" if self.dark_mode else "#222"
            html = f"<html><body style='background:{bg};color:{fg};padding:40px;'>{text}</body></html>"
            self.web.setHtml(html)
        except Exception:
            pass

    def toggle_search_bar(self) -> None:
        """Toggles the visibility of the text search bar."""
        visible = not self.search_bar.isVisible()
        self.search_bar.setVisible(visible)
        self.btn_search.setChecked(visible)
        if visible:
            self.txt_search.setFocus()
            self.txt_search.selectAll()

    def find_next(self) -> None:
        """Searches for the next occurrence of the text."""
        self._find_text(direction=1)

    def find_prev(self) -> None:
        """Searches for the previous occurrence of the text."""
        self._find_text(direction=-1)

    def _find_text(self, direction: int) -> None:
        """
        Executes text search logic.

        Args:
            direction: 1 for forward, -1 for backward.
        """
        if not self.current_doc:
            return

        term = self.txt_search.text().strip().lower()
        if not term:
            return

        start_idx = self.current_page_index + direction
        count = self.current_doc.page_count

        for i in range(count):
            idx = (start_idx + (i * direction)) % count
            try:
                text = self.current_doc.get_page_text(idx)
                if term in text.lower():
                    self.current_page_index = idx
                    if self.continuous_scroll and self._virtual_enabled:
                        start, end = self._virtual_range
                        if idx < start or idx >= end:
                            self.rebuild_layout()

                    elif not self.continuous_scroll:
                        self.rebuild_layout()
                    self.update_view()
                    self.ensure_visible(idx)
                    return
            except Exception:
                continue

    def on_page_input_return(self) -> None:
        """Handles user input in the page number text field."""
        if not self.current_doc:
            return

        text = self.txt_page.text().strip()
        if text.isdigit():
            page_num = int(text)
            if 1 <= page_num <= self.current_doc.page_count:
                target_idx = page_num - 1
                if target_idx != self.current_page_index:
                    self.current_page_index = target_idx
                    if self.continuous_scroll and self._virtual_enabled:
                        start, end = self._virtual_range
                        if target_idx < start or target_idx >= end:
                            self.rebuild_layout()

                    elif not self.continuous_scroll:
                        self.rebuild_layout()
                    self.update_view()
                    self.ensure_visible(self.current_page_index)
                    self.scroll.setFocus()
            else:
                self.txt_page.setText(str(self.current_page_index + 1))
        else:
            self.txt_page.setText(str(self.current_page_index + 1))

    def defer_scroll_update(self, value: int) -> None:
        """Queues a scroll update event to debounce rapid scrolling."""
        self.txt_page.setText(str(self.current_page_index + 1))
        self.scroll_timer.start()

    def real_scroll_handler(self) -> None:
        """Executes the expensive view update after scrolling has settled."""
        val = self.scroll.verticalScrollBar().value()
        self.on_scroll_changed(val)

    def on_scroll_changed(self, value: int) -> None:
        """
        Calculates the current page based on scroll position.

        Args:
            value: The vertical scroll bar value.
        """
        viewport_center = value + (self.scroll.viewport().height() / 2)
        closest_page = self.current_page_index
        min_dist = float("inf")

        for idx, widget in self.page_widgets.items():
            try:
                mapped_pos = widget.mapTo(self.scroll_content, QPoint(0, 0))
                w_center = mapped_pos.y() + (widget.height() / 2)
                dist = abs(w_center - viewport_center)
                if dist < min_dist:
                    min_dist = dist
                    closest_page = idx
            except RuntimeError:
                continue

        if closest_page != self.current_page_index:
            self.current_page_index = closest_page
            if self.current_doc:
                self.txt_page.setText(str(self.current_page_index + 1))

        if self._virtual_enabled:
            start, end = self._virtual_range
            buffer_threshold = 10

            if (self.current_page_index > end - buffer_threshold) or (
                self.current_page_index < start + buffer_threshold
            ):
                if not (
                    start == 0 and self.current_page_index < buffer_threshold
                ) and not (
                    end == self.current_doc.page_count
                    and self.current_page_index > end - buffer_threshold
                ):
                    self.rebuild_layout()
                    self.ensure_visible(self.current_page_index)

        self.render_visible_pages()

    def next_view(self) -> None:
        """Navigates to the next page or pair of pages."""
        if not self.current_doc:
            return
        step = 2 if self.facing_mode else 1
        new_idx = min(self.current_doc.page_count - 1, self.current_page_index + step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(self.current_page_index)

    def prev_view(self) -> None:
        """Navigates to the previous page or pair of pages."""
        step = 2 if self.facing_mode else 1
        new_idx = max(0, self.current_page_index - step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(self.current_page_index)

    def scroll_page(self, direction: int) -> None:
        """
        Scrolls the viewport by one page height.

        Args:
            direction: 1 for down, -1 for up.
        """
        bar = self.scroll.verticalScrollBar()
        page_step = self.scroll.viewport().height() * 0.9
        bar.setValue(bar.value() + (direction * page_step))

    def ensure_visible(self, index: int) -> None:
        """
        Ensures the specified page widget is visible in the scroll area.
        Handles coordinate calculation for virtualized (non-instantiated) widgets.
        """
        if index in self.page_widgets:
            widget = self.page_widgets[index]
            self.scroll.ensureWidgetVisible(widget, 0, 0)
            return

        if not self._virtual_enabled or not self._cached_base_size:
            return

        start, end = self._virtual_range
        _, base_h = self._cached_base_size
        scale = self.calculate_scale()
        page_h = int(base_h * scale) + self.scroll_layout.spacing()

        top_height = self._top_spacer.height() if self._top_spacer else 0
        idx_offset = index - start
        y_pos = top_height + max(0, idx_offset) * page_h

        viewport_centre_offset = int(self.scroll.viewport().height() / 2)
        self.scroll.verticalScrollBar().setValue(
            max(0, int(y_pos - viewport_centre_offset))
        )

    def event(self, e: QEvent) -> bool:
        """Handles generic Qt events, including native gestures."""
        if e.type() == QEvent.Type.NativeGesture:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                scale_factor = e.value()
                self.zoom_mode = ZoomMode.MANUAL
                self.manual_scale *= 1.0 + scale_factor
                self.manual_scale = max(0.1, min(self.manual_scale, 5.0))
                self.on_zoom_changed_internal()
                return True
        return super().event(e)

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """Filters events for child widgets to handle clicks and keypresses."""
        if event.type() == QEvent.Type.KeyPress and source == self.scroll:
            self.keyPressEvent(event)
            return True

        if event.type() == QEvent.Type.MouseButtonPress and isinstance(source, QLabel):
            if self.is_annotating:
                self.handle_annotation_click(source, event)
                return True

        return super().eventFilter(source, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handles mouse wheel events for scrolling and zooming."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            viewport_y = event.position().y()
            content_y = viewport_y + self.scroll.verticalScrollBar().value()

            factor = 1.1 if delta > 0 else 0.9
            self.manual_scale *= factor
            self.zoom_mode = ZoomMode.MANUAL

            self.on_zoom_changed_internal()

            new_scroll = (content_y * factor) - viewport_y
            self.scroll.verticalScrollBar().setValue(int(new_scroll))
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handles keyboard shortcuts for navigation and view controls."""
        key = event.key()
        mod = event.modifiers()

        if key == Qt.Key.Key_Escape:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()
                event.accept()
                return

        if key == Qt.Key.Key_F11 or key == Qt.Key.Key_F:
            self.toggle_reader_fullscreen()
            event.accept()
            return

        if (mod & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_F:
            self.toggle_search_bar()
            return

        if self.view_mode == ViewMode.IMAGE:
            if (
                mod & Qt.KeyboardModifier.ControlModifier
                and mod & Qt.KeyboardModifier.ShiftModifier
            ):
                if key == Qt.Key.Key_Plus or key == Qt.Key.Key_Equal:
                    self.zoom_step(1.1)
                    event.accept()
                    return
                if key == Qt.Key.Key_Minus or key == Qt.Key.Key_Underscore:
                    self.zoom_step(0.9)
                    event.accept()
                    return

            if mod & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_Plus or key == Qt.Key.Key_Equal:
                    self.zoom_step(1.1)
                    event.accept()
                    return
                if key == Qt.Key.Key_Minus or key == Qt.Key.Key_Underscore:
                    self.zoom_step(0.9)
                    event.accept()
                    return

            if key == Qt.Key.Key_Right:
                self.next_view()
                event.accept()
                return
            elif key == Qt.Key.Key_Left:
                self.prev_view()
                event.accept()
                return
            elif key == Qt.Key.Key_Space:
                direction = -1 if (mod & Qt.KeyboardModifier.ShiftModifier) else 1
                self.scroll_page(direction)
                event.accept()
                return
            elif key == Qt.Key.Key_Down:
                self.scroll.verticalScrollBar().setValue(
                    self.scroll.verticalScrollBar().value() + 50
                )
                event.accept()
                return
            elif key == Qt.Key.Key_Up:
                self.scroll.verticalScrollBar().setValue(
                    self.scroll.verticalScrollBar().value() - 50
                )
                event.accept()
                return

            if mod == Qt.KeyboardModifier.NoModifier:
                if key == Qt.Key.Key_N:
                    self.toggle_theme()
                    event.accept()
                    return
                elif key == Qt.Key.Key_R:
                    self.toggle_view_mode()
                    event.accept()
                    return
                elif key == Qt.Key.Key_C:
                    self.toggle_scroll_mode()
                    event.accept()
                    return
                elif key == Qt.Key.Key_D:
                    self.toggle_facing_mode()
                    event.accept()
                    return
                elif key == Qt.Key.Key_W:
                    self.apply_zoom_string("Fit Width")
                    event.accept()
                    return
                elif key == Qt.Key.Key_H:
                    self.apply_zoom_string("Fit Height")
                    event.accept()
                    return

        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        """Handles window resizing with debouncing to prevent UI freeze."""
        if not hasattr(self, "_resize_timer"):
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.setInterval(150)
            self._resize_timer.timeout.connect(self._on_resize_timeout)

        if self.zoom_mode in [ZoomMode.FIT_WIDTH, ZoomMode.FIT_HEIGHT]:
            self._resize_timer.start()

        super().resizeEvent(event)

    def _on_resize_timeout(self) -> None:
        """Executes layout recalculation after resize settles."""
        self._update_all_widget_sizes()
        self.rendered_pages.clear()
        if self.current_doc:
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()

    def on_zoom_selected(self, idx: int) -> None:
        """Handles zoom selection from the combobox."""
        self.apply_zoom_string(self.combo_zoom.currentText())

    def on_zoom_text_entered(self) -> None:
        """Handles manual zoom text entry."""
        self.apply_zoom_string(self.combo_zoom.lineEdit().text())
        self.scroll.setFocus()

    def apply_zoom_string(self, text: str) -> None:
        """Parses zoom string and applies it."""
        if "Fit Width" in text:
            self.zoom_mode = ZoomMode.FIT_WIDTH
        elif "Fit Height" in text:
            self.zoom_mode = ZoomMode.FIT_HEIGHT
        else:
            try:
                val = float(text.lower().replace("%", "").strip())
                if val > 5.0:
                    val /= 100.0
                self.manual_scale = val
                self.zoom_mode = ZoomMode.MANUAL
            except ValueError:
                pass
        self.on_zoom_changed_internal()

    def zoom_step(self, factor: float) -> None:
        """Increments or decrements zoom by a factor."""
        self.manual_scale *= factor
        self.zoom_mode = ZoomMode.MANUAL
        self.on_zoom_changed_internal()

    def _update_all_widget_sizes(self) -> None:
        """Resizes all page widgets to match the new scale."""
        w, h = self._get_target_page_size()
        for lbl in self.page_widgets.values():
            lbl.setFixedSize(w, h)

    def on_zoom_changed_internal(self) -> None:
        """Updates internal state and UI after a zoom change."""
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)
        self._update_all_widget_sizes()

        self.rendered_pages.clear()
        self.update_view()
        self._sync_zoom_ui()

    def _sync_zoom_ui(self) -> None:
        """Syncs the combobox text with current zoom state."""
        if self.zoom_mode == ZoomMode.FIT_WIDTH:
            self.combo_zoom.setCurrentText("Fit Width")
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            self.combo_zoom.setCurrentText("Fit Height")
        else:
            self.combo_zoom.setCurrentText(f"{int(self.manual_scale * 100)}%")

    def apply_theme(self) -> None:
        """Applies colors based on the current Dark/Light mode setting."""
        pal = self.palette()
        color = QColor(30, 30, 30) if self.dark_mode else QColor(240, 240, 240)
        pal.setColor(QPalette.ColorRole.Window, color)
        self.setPalette(pal)

        bg = "#222" if self.dark_mode else "#eee"
        self.scroll_content.setStyleSheet(
            f"#scrollContent {{ background-color: {bg}; }}"
        )

        fg = "#ddd" if self.dark_mode else "#111"
        self.toolbar.setStyleSheet(f"""
            QWidget {{ background: {color.name()}; color: {fg}; }}
            QPushButton {{ border: none; padding: 6px; border-radius: 4px; }}
            QPushButton:hover {{ background: rgba(128,128,128,0.3); }}
            QPushButton:checked {{ background: rgba(80, 160, 255, 0.4); border: 1px solid #50a0ff; }}
        """)

    def toggle_theme(self) -> None:
        """Toggles the dark mode state and propagates to parent window if possible."""
        if self.window() and isinstance(self.window(), RiemannWindow):
            main_win = self.window()
            if main_win.dark_mode == self.dark_mode:
                main_win.toggle_theme()
                return

        self.dark_mode = not self.dark_mode
        self.apply_theme()
        self.rendered_pages.clear()
        self.update_view()

    def load_annotations(self) -> None:
        """Loads annotations from the sidecar JSON file."""
        if not self.current_path:
            return
        path = str(self.current_path) + ".riemann.json"
        if os.path.exists(path):
            with open(path, "r") as f:
                self.annotations = json.load(f)
        else:
            self.annotations = {}

    def save_annotations(self) -> None:
        """Saves current annotations to disk."""
        if not self.current_path:
            return
        with open(str(self.current_path) + ".riemann.json", "w") as f:
            json.dump(self.annotations, f)

    def toggle_annotation_mode(self, checked: bool) -> None:
        """Toggles the annotation editing mode."""
        self.is_annotating = checked
        self.btn_annotate.setChecked(checked)

    def handle_annotation_click(self, label: QLabel, event: QMouseEvent) -> None:
        """Handles click events on page labels for annotation creation or viewing."""
        page_idx = label.property("pageIndex")
        click_x = event.pos().x()
        click_y = event.pos().y()

        rel_x = click_x / label.width()
        rel_y = click_y / label.height()

        page_annos = self.annotations.get(str(page_idx), [])
        hit_threshold_px = 20

        for i, anno in enumerate(page_annos):
            ax, ay = anno["rel_pos"]
            px_x = ax * label.width()
            px_y = ay * label.height()
            dist = ((click_x - px_x) ** 2 + (click_y - px_y) ** 2) ** 0.5

            if dist < hit_threshold_px:
                self.show_annotation_popup(anno, page_idx, i)
                return

        if self.is_annotating:
            self.create_new_annotation(page_idx, rel_x, rel_y)

    def show_annotation_popup(
        self, anno_data: Dict, page_idx: int, anno_index: int
    ) -> None:
        """Displays a dialog to view or edit an existing annotation."""
        text = anno_data.get("text", "")
        new_text, ok = QInputDialog.getText(
            self, "View Note", "Content (Clear text to delete):", text=text
        )

        if ok:
            if not new_text.strip():
                del self.annotations[str(page_idx)][anno_index]
            else:
                self.annotations[str(page_idx)][anno_index]["text"] = new_text
            self.save_annotations()
            self.refresh_page_render(page_idx)

    def create_new_annotation(self, page_idx: int, rel_x: float, rel_y: float) -> None:
        """Creates a new annotation at the specified relative coordinates."""
        text, ok = QInputDialog.getText(self, "Add Note", "Note content:")
        if ok and text:
            if str(page_idx) not in self.annotations:
                self.annotations[str(page_idx)] = []
            self.annotations[str(page_idx)].append(
                {"rel_pos": (rel_x, rel_y), "text": text}
            )
            self.save_annotations()
            self.refresh_page_render(page_idx)

    def refresh_page_render(self, page_idx: int) -> None:
        """Forces a re-render of a specific page to show/hide annotations."""
        if page_idx in self.rendered_pages:
            self.rendered_pages.remove(page_idx)
        self.render_visible_pages()

    def perform_ocr_current_page(self) -> None:
        """Performs OCR on the current page and displays the text."""
        if not self.current_doc:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            text = self.current_doc.ocr_page(self.current_page_index, 2.0)
            QApplication.restoreOverrideCursor()

            if not text.strip():
                text = "[No text detected by Tesseract]"

            QInputDialog.getMultiLineText(
                self, "OCR Result", "Extracted Text (Copy to clipboard):", text
            )

        except Exception as e:
            QApplication.restoreOverrideCursor()
            print(f"OCR Failed: {e}")

    def toggle_view_mode(self) -> None:
        """Switches between Image mode and Text Reflow mode."""
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        self.stack.setCurrentIndex(0 if self.view_mode == ViewMode.IMAGE else 1)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)
        self.update_view()

    def toggle_facing_mode(self) -> None:
        """Toggles 2-up facing pages mode."""
        self.facing_mode = not self.facing_mode
        self.settings.setValue("facingMode", self.facing_mode)
        self.btn_facing.setChecked(self.facing_mode)
        self.rebuild_layout()
        self.update_view()

    def toggle_scroll_mode(self) -> None:
        """Toggles between single-page snapping and continuous scrolling."""
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScrollMode", self.continuous_scroll)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.rebuild_layout()
        self.update_view()

    def toggle_reader_fullscreen(self) -> None:
        """Toggles the window-level fullscreen mode."""
        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def open_pdf_dialog(self) -> None:
        """Opens a file dialog to load a new PDF into this tab."""
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)


class DraggableTabWidget(QTabWidget):
    """A QTabWidget subclass that allows reordering and dragging tabs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setTabBar(DraggableTabBar(self))

    def dragEnterEvent(self, e):
        """Accepts drag events that contain text (file paths)."""
        if e.mimeData().hasText():
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, event):
        """Handles dropping a file path to create a new tab."""
        file_path = event.mimeData().text()
        if os.path.exists(file_path):
            reader = ReaderTab()
            reader.load_document(file_path)
            self.addTab(reader, os.path.basename(file_path))
            self.setCurrentWidget(reader)
            event.acceptProposedAction()


class DraggableTabBar(QTabBar):
    """A QTabBar that supports dragging tabs out of the window."""

    def mouseMoveEvent(self, event):
        """Initiates a drag operation when a tab is dragged."""
        if event.buttons() != Qt.MouseButton.LeftButton:
            return

        global_pos = event.globalPosition().toPoint()
        pos_in_widget = self.mapFromGlobal(global_pos)

        tab_index = self.tabAt(pos_in_widget)
        if tab_index < 0:
            return

        widget = self.parent().widget(tab_index)
        if not hasattr(widget, "current_path") or not widget.current_path:
            return

        mime = QMimeData()
        mime.setText(widget.current_path)

        drag = QDrag(self)
        drag.setMimeData(mime)

        pixmap = widget.grab()
        drag.setPixmap(pixmap.scaled(200, 150, Qt.AspectRatioMode.KeepAspectRatio))
        drag.setHotSpot(QPoint(100, 75))

        if drag.exec(Qt.DropAction.MoveAction) == Qt.DropAction.MoveAction:
            self.parent().removeTab(tab_index)

        super().mouseMoveEvent(event)


class BrowserTab(QWidget):
    """
    A full-featured web browser tab using QWebEngineView.
    Includes navigation controls and dark mode support.
    """

    def __init__(
        self,
        start_url: str = "https://www.google.com",
        parent=None,
        dark_mode: bool = True,
    ):
        """
        Initialize the BrowserTab.

        Args:
            start_url: The initial URL to load.
            parent: Parent widget.
            dark_mode: Whether to start in dark mode.
        """
        super().__init__(parent)
        self.dark_mode = dark_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(40)
        tb_layout = QHBoxLayout(self.toolbar)
        tb_layout.setContentsMargins(5, 0, 5, 0)

        self.btn_back = QPushButton("◀")
        self.btn_back.setFixedWidth(30)
        self.btn_fwd = QPushButton("▶")
        self.btn_fwd.setFixedWidth(30)
        self.btn_reload = QPushButton("↻")
        self.btn_reload.setFixedWidth(30)

        self.search_bar = QWidget()
        self.search_bar.setFixedHeight(40)
        self.search_bar.setVisible(False)
        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(5, 0, 5, 0)

        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("Find in page...")
        self.txt_find.returnPressed.connect(self.find_next)

        self.btn_find_next = QPushButton("▼")
        self.btn_find_next.clicked.connect(self.find_next)
        self.btn_find_prev = QPushButton("▲")
        self.btn_find_prev.clicked.connect(self.find_prev)
        self.btn_close_find = QPushButton("✕")
        self.btn_close_find.clicked.connect(self.toggle_search)

        sb_layout.addWidget(QLabel("Find:"))
        sb_layout.addWidget(self.txt_find)
        sb_layout.addWidget(self.btn_find_next)
        sb_layout.addWidget(self.btn_find_prev)
        sb_layout.addWidget(self.btn_close_find)

        layout.addWidget(self.search_bar)

        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("Enter URL or Search...")
        self.txt_url.returnPressed.connect(self.navigate_to_url)

        tb_layout.addWidget(self.btn_back)
        tb_layout.addWidget(self.btn_fwd)
        tb_layout.addWidget(self.btn_reload)
        tb_layout.addWidget(self.txt_url)

        layout.addWidget(self.toolbar)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.web = QWebEngineView()
        base_path = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        storage_path = os.path.join(base_path, "browser_data")
        os.makedirs(storage_path, exist_ok=True)

        profile = QWebEngineProfile("RiemannPersistentProfile", self.web)
        profile.setPersistentStoragePath(storage_path)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )

        page = QWebEnginePage(profile, self.web)
        self.web.setPage(page)
        layout.addWidget(self.web)

        self.btn_back.clicked.connect(self.web.back)
        self.btn_fwd.clicked.connect(self.web.forward)
        self.btn_reload.clicked.connect(self.web.reload)

        self.web.urlChanged.connect(self._update_url_bar)
        self.web.loadProgress.connect(self.progress.setValue)
        self.web.loadFinished.connect(lambda: self.progress.setValue(0))
        self.web.titleChanged.connect(self._update_tab_title)

        self.shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_find.activated.connect(self.toggle_search)

        self.shortcut_zoom_in = QShortcut(QKeySequence("Ctrl+="), self)
        self.shortcut_zoom_in.activated.connect(lambda: self.modify_zoom(0.1))

        self.shortcut_zoom_in_alt = QShortcut(QKeySequence("Ctrl++"), self)
        self.shortcut_zoom_in_alt.activated.connect(lambda: self.modify_zoom(0.1))

        self.shortcut_zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        self.shortcut_zoom_out.activated.connect(lambda: self.modify_zoom(-0.1))

        self.shortcut_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        self.shortcut_zoom_reset.activated.connect(lambda: self.web.setZoomFactor(1.0))

        self.apply_theme()
        self.web.load(QUrl(start_url))

    def apply_theme(self):
        """Applies dark mode styles to the UI and WebEngine."""
        settings = self.web.page().settings()

        if self.dark_mode:
            bg = "#333"
            fg = "#ddd"
            inp_bg = "#444"
            border = "#555"
            settings.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, True)
            self.web.page().setBackgroundColor(QColor("#333"))
        else:
            bg = "#f0f0f0"
            fg = "#222"
            inp_bg = "#fff"
            border = "#ccc"
            settings.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, False)
            self.web.page().setBackgroundColor(QColor("#fff"))

        style = f"""
            QWidget {{ background: {bg}; color: {fg}; }}
            QLineEdit {{ background: {inp_bg}; border: 1px solid {border}; border-radius: 4px; padding: 4px; }}
            QPushButton {{ background: transparent; border: none; padding: 4px; }}
            QPushButton:hover {{ background: rgba(128,128,128,0.2); border-radius: 4px; }}
        """
        self.toolbar.setStyleSheet(style)
        self.search_bar.setStyleSheet(style)

        chunk = "#3a86ff" if self.dark_mode else "#007aff"
        self.progress.setStyleSheet(
            f"QProgressBar {{ border: 0px; background: transparent; }} QProgressBar::chunk {{ background: {chunk}; }}"
        )

    def modify_zoom(self, delta):
        """Adjusts the browser zoom level."""
        new_zoom = self.web.zoomFactor() + delta
        self.web.setZoomFactor(max(0.1, min(new_zoom, 5.0)))

    def toggle_search(self):
        """Toggles the find-in-page bar."""
        visible = not self.search_bar.isVisible()
        self.search_bar.setVisible(visible)
        if visible:
            self.txt_find.setFocus()
            self.txt_find.selectAll()
        else:
            self.web.findText("")

    def find_next(self):
        """Finds next occurrence of text in page."""
        self.web.findText(self.txt_find.text())

    def find_prev(self):
        """Finds previous occurrence of text in page."""
        self.web.findText(self.txt_find.text(), QWebEngineView.FindFlag.FindBackward)

    def navigate_to_url(self):
        """Loads the URL or search query entered in the address bar."""
        text = self.txt_url.text().strip()
        if not text:
            return
        if "." in text and " " not in text:
            if not text.startswith("http"):
                text = "https://" + text
            url = QUrl(text)
        else:
            url = QUrl(f"https://www.google.com/search?q={text}")
        self.web.load(url)

    def _update_url_bar(self, url: QUrl):
        """Updates URL bar text when navigation occurs."""
        self.txt_url.setText(url.toString())
        self.txt_url.setCursorPosition(0)

    def _update_tab_title(self, title: str):
        """Updates the parent tab title when the page title changes."""
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                idx = parent.indexOf(self)
                if idx != -1:
                    short_title = (title[:20] + "..") if len(title) > 20 else title
                    parent.setTabText(idx, short_title)
                    parent.setTabToolTip(idx, title)
                break
            parent = parent.parent()


class RiemannWindow(QMainWindow):
    """
    The Main Window Manager.

    Handles global application state, window chrome, shortcuts, and
    orchestrates the split-view tab management system.
    """

    def __init__(self):
        """Initialize the main window, UI, and session."""
        super().__init__()
        self.setWindowTitle("Riemann Reader")
        self.resize(1200, 900)

        self.settings = QSettings("Riemann", "PDFReader")
        self.dark_mode = self.settings.value("darkMode", True, type=bool)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self.splitter)

        self.tabs_main = DraggableTabWidget()
        self.tabs_main.setTabsClosable(True)
        self.tabs_main.tabCloseRequested.connect(self.close_tab)
        self.splitter.addWidget(self.tabs_main)

        self.tabs_side = DraggableTabWidget()
        self.tabs_side.setTabsClosable(True)
        self.tabs_side.tabCloseRequested.connect(self.close_side_tab)
        self.tabs_side.hide()
        self.splitter.addWidget(self.tabs_side)

        self.setup_menu()

        self.shortcut_close = QShortcut(QKeySequence("Ctrl+W"), self)
        self.shortcut_close.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_close.activated.connect(self.close_active_tab)

        self.shortcut_split = QShortcut(QKeySequence("Ctrl+\\"), self)
        self.shortcut_split.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_split.activated.connect(self.toggle_split_view)

        self.shortcut_fullscreen = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.shortcut_fullscreen.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_fullscreen.activated.connect(self.toggle_reader_fullscreen)

        self.shortcut_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.shortcut_escape.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_escape.activated.connect(self._handle_escape)

        self._restore_session()

    def _handle_escape(self):
        if getattr(self, "_reader_fullscreen", False):
            self.toggle_reader_fullscreen()

    def _restore_session(self):
        """Restores window geometry and open tabs from settings."""
        if self.settings.value("window/geometry"):
            self.restoreGeometry(self.settings.value("window/geometry"))

        self._restore_tabs_from_settings("session/main_tabs", self.tabs_main)
        self._restore_tabs_from_settings("session/side_tabs", self.tabs_side)

        if self.tabs_side.count() > 0:
            self.tabs_side.show()
            if self.settings.value("splitter/state"):
                self.splitter.restoreState(self.settings.value("splitter/state"))
        else:
            self.tabs_side.hide()

        if self.tabs_main.count() == 0:
            self.new_tab()

    def _restore_tabs_from_settings(self, key: str, target_widget: QTabWidget) -> None:
        """
        Parses saved tab data and recreates tabs in the target widget.

        Args:
            key: The QSettings key to read.
            target_widget: The tab widget to populate.
        """
        items = self.settings.value(key, [], type=list)
        if isinstance(items, str):
            items = [items]

        for item in items:
            if isinstance(item, str) and os.path.exists(item):
                self._add_pdf_tab(item, target_widget, restore_state=True)

            elif isinstance(item, dict):
                i_type = item.get("type")
                data = item.get("data")

                if i_type == "pdf" and data and os.path.exists(data):
                    self._add_pdf_tab(data, target_widget, restore_state=True)
                elif i_type == "web" and data:
                    self._add_browser_tab(data, target_widget)

    def _add_pdf_tab(
        self, path: str, target_widget: QTabWidget, restore_state: bool = False
    ) -> None:
        """Creates and adds a ReaderTab to the specified widget."""
        reader = ReaderTab()
        reader.load_document(path, restore_state=restore_state)
        target_widget.addTab(reader, os.path.basename(path))

    def _add_browser_tab(self, url: str, target_widget: QTabWidget) -> None:
        """Creates and adds a BrowserTab to the specified widget."""
        browser = BrowserTab(url, dark_mode=self.dark_mode)
        target_widget.addTab(browser, "Loading...")

    def setup_menu(self) -> None:
        """Configures the application menu bar."""
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        open_action = file_menu.addAction("Open PDF")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_pdf_smart)

        new_tab_action = file_menu.addAction("Open New PDF Tab")
        new_tab_action.setShortcut("Ctrl+T")
        new_tab_action.triggered.connect(self.open_pdf_smart)

        file_menu.addSeparator()

        browser_action = file_menu.addAction("New Browser Tab")
        browser_action.setShortcut("Ctrl+B")
        browser_action.triggered.connect(lambda: self.new_browser_tab())

        file_menu.addSeparator()

        exit_action = file_menu.addAction("Exit")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

        view_menu = menubar.addMenu("View")

        theme_action = view_menu.addAction("Toggle Theme")
        theme_action.setShortcut("Ctrl+D")
        theme_action.triggered.connect(self.toggle_theme)

    def new_tab(self, path: Optional[str] = None, restore_state: bool = False) -> None:
        """Creates a new PDF tab in the main tab widget."""
        if path:
            self._add_pdf_tab(path, self.tabs_main, restore_state)
        else:
            reader = ReaderTab()
            self.tabs_main.addTab(reader, "New Tab")
            self.tabs_main.setCurrentWidget(reader)

    def new_browser_tab(self, url="https://www.google.com"):
        """Creates a new browser tab in the currently active group."""
        target = self.tabs_main
        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            target = self.tabs_side

        self._add_browser_tab(url, target)
        new_tab = target.widget(target.count() - 1)
        target.setCurrentWidget(new_tab)
        new_tab.txt_url.setFocus()
        new_tab.txt_url.selectAll()

    def open_pdf_smart(self):
        """
        Opens a PDF. Reuses the current tab if empty; otherwise opens a new one.
        """
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if not path:
            return

        current_widget = self.tabs_main.currentWidget()
        is_empty_reader = (
            isinstance(current_widget, ReaderTab) and not current_widget.current_path
        )

        if is_empty_reader:
            current_widget.load_document(path)
            self.tabs_main.setTabText(
                self.tabs_main.currentIndex(), os.path.basename(path)
            )
        else:
            self.new_tab(path)

    def toggle_split_view(self) -> None:
        """Moves the currently active tab to the side splitter."""
        if self.tabs_side.isHidden():
            self.tabs_side.show()

        current = self.tabs_main.currentWidget()
        if current:
            idx = self.tabs_main.indexOf(current)
            text = self.tabs_main.tabText(idx)
            self.tabs_main.removeTab(idx)
            self.tabs_side.addTab(current, text)
            self.tabs_side.setCurrentWidget(current)

    def close_tab(self, index: int) -> None:
        """Closes a tab in the main group."""
        widget = self.tabs_main.widget(index)
        if widget:
            widget.deleteLater()
        self.tabs_main.removeTab(index)

        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()

    def close_side_tab(self, index: int) -> None:
        """Closes a tab in the side group and hides the splitter if empty."""
        widget = self.tabs_side.widget(index)
        if widget:
            widget.deleteLater()
        self.tabs_side.removeTab(index)
        if self.tabs_side.count() == 0:
            self.tabs_side.hide()

    def closeEvent(self, event):
        """Saves session state before closing."""

        def get_open_files(tab_widget):
            items = []
            for i in range(tab_widget.count()):
                widget = tab_widget.widget(i)
                if isinstance(widget, ReaderTab) and widget.current_path:
                    items.append({"type": "pdf", "data": widget.current_path})
                elif isinstance(widget, BrowserTab):
                    items.append({"type": "web", "data": widget.web.url().toString()})
            return items

        self.settings.setValue("session/main_tabs", get_open_files(self.tabs_main))
        self.settings.setValue("session/side_tabs", get_open_files(self.tabs_side))
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())

        super().closeEvent(event)

    def toggle_reader_fullscreen(self) -> None:
        """Toggles 'Zen Mode' reading, hiding UI chrome."""
        if not getattr(self, "_reader_fullscreen", False):
            self._reader_fullscreen = True
            self._was_maximized = self.isMaximized()

            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            self.tabs_side.tabBar().hide()
            self._set_tabs_toolbar_visible(False)

            self.showFullScreen()
        else:
            self._reader_fullscreen = False

            self.menuBar().show()
            self.tabs_main.tabBar().show()
            self.tabs_side.tabBar().show()
            self._set_tabs_toolbar_visible(True)

            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()

    def _set_tabs_toolbar_visible(self, visible: bool) -> None:
        """Helper to hide/show toolbars in all tabs."""
        for i in range(self.tabs_main.count()):
            w = self.tabs_main.widget(i)
            if isinstance(w, ReaderTab):
                w.toolbar.setVisible(visible)
        for i in range(self.tabs_side.count()):
            w = self.tabs_side.widget(i)
            if isinstance(w, ReaderTab):
                w.toolbar.setVisible(visible)

    def toggle_theme(self) -> None:
        """Globally toggles the application theme."""
        self.dark_mode = not self.dark_mode
        self.settings.setValue("darkMode", self.dark_mode)

        def update_tab(tab: QWidget):
            if isinstance(tab, ReaderTab):
                tab.dark_mode = self.dark_mode
                tab.apply_theme()
                tab.rendered_pages.clear()
                tab.update_view()
            elif isinstance(tab, BrowserTab):
                tab.dark_mode = self.dark_mode
                tab.apply_theme()

        for i in range(self.tabs_main.count()):
            update_tab(self.tabs_main.widget(i))
        for i in range(self.tabs_side.count()):
            update_tab(self.tabs_side.widget(i))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Global key handlers."""
        if event.key() == Qt.Key.Key_Escape:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()
                event.accept()
                return

        if event.key() == Qt.Key.Key_F11:
            self.toggle_reader_fullscreen()
            event.accept()
            return

        super().keyPressEvent(event)

    def close_active_tab(self):
        """Intelligently closes the currently active tab based on focus."""
        focus_widget = QApplication.focusWidget()
        target_tabs = None
        curr = focus_widget

        while curr:
            if curr == self.tabs_main:
                target_tabs = self.tabs_main
                break
            elif curr == self.tabs_side:
                target_tabs = self.tabs_side
                break
            curr = curr.parent()

        if target_tabs is None:
            if self.tabs_side.isVisible() and self.tabs_side.count() > 0:
                target_tabs = self.tabs_main
            else:
                target_tabs = self.tabs_main

        if target_tabs:
            idx = target_tabs.currentIndex()
            if idx != -1:
                if target_tabs == self.tabs_main:
                    self.close_tab(idx)
                else:
                    self.close_side_tab(idx)


def run():
    app = QApplication(sys.argv)
    window = RiemannWindow()
    window.show()
    sys.exit(app.exec())
