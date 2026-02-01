import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

import markdown
from PIL import Image
from PySide6.QtCore import (
    QBuffer,
    QEvent,
    QObject,
    QPoint,
    QRect,
    QSettings,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
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
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import ViewMode, ZoomMode

try:
    import riemann_core
except ImportError as e:
    print(f"CRITICAL: Could not import riemann_core backend.\nError: {e}")
    sys.exit(1)


class InstallerThread(QThread):
    """
    Background thread to run 'pip install' so the UI doesn't freeze.
    """

    finished_install = Signal()
    install_error = Signal(str)

    def run(self):
        try:
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "pix2tex[gui]",
                "torch",
                "torchvision",
            ]

            subprocess.check_call(cmd)
            self.finished_install.emit()
        except subprocess.CalledProcessError:
            self.install_error.emit(
                "Installation failed.\nPlease check your internet connection."
            )
        except Exception as e:
            self.install_error.emit(f"Installer Error: {e}")


class LoaderThread(QThread):
    """
    Background thread to load the heavy Pix2Tex model without freezing the UI.
    """

    finished_loading = Signal(object)
    error_occurred = Signal(str)

    def run(self):
        try:
            from pix2tex.cli import LatexOCR

            model = LatexOCR()
            self.finished_loading.emit(model)
        except ImportError:
            self.error_occurred.emit(
                "Module 'pix2tex' not found.\nPlease run: pip install pix2tex[gui] torch torchvision"
            )
        except Exception as e:
            self.error_occurred.emit(f"AI Initialization Failed:\n{str(e)}")


class ReaderTab(QWidget):
    """
    A self-contained PDF Viewer Widget.

    This class manages the rendering pipeline, navigation, state (zoom, scroll),
    and interactions (annotations, text selection) for a single open PDF document.

    Attributes:
        settings (QSettings): Persistent application settings.
        engine (riemann_core.PdfEngine): The Rust-based backend engine instance.
        current_doc (riemann_core.RiemannDocument): The currently loaded PDF document object.
        page_widgets (Dict[int, QLabel]): Mapping of page indices to their display widgets.
        rendered_pages (Set[int]): Set of page indices currently holding rendered pixmaps.
        annotations (Dict): Dictionary storing user annotations.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
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

        self.is_snipping: bool = False
        self.snip_start: QPoint = QPoint()
        self.snip_band: Optional[QRubberBand] = None

        self.latex_model = None
        self.loader_thread: Optional[LoaderThread] = None
        self._pending_snip_image = None

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
        self._cached_base_size: Optional[Tuple[int, int]] = None

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

        self.btn_save = QPushButton("💾")
        self.btn_save.setToolTip("Save Copy of PDF")
        self.btn_save.clicked.connect(self.save_document)

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

        self.btn_snip = QPushButton("✂️")
        self.btn_snip.setToolTip("Snip Math to LaTeX (Draw a box around an equation)")
        self.btn_snip.setCheckable(True)
        self.btn_snip.clicked.connect(self.toggle_snip_mode)

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
            self.btn_save,
            self.btn_reflow,
            self.btn_facing,
            self.btn_scroll_mode,
            self.btn_search,
            self.btn_annotate,
            self.btn_snip,
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

    def toggle_snip_mode(self, checked: bool) -> None:
        """Enables the rubberband selection mode for Math OCR."""
        self.is_snipping = checked
        self.btn_snip.setChecked(checked)
        if checked:
            self.is_annotating = False
            self.btn_annotate.setChecked(False)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if self.snip_band:
                self.snip_band.hide()

    def _init_latex_model(self):
        """Lazy loads the heavy Pix2Tex model only when needed."""
        if self.latex_model is not None:
            return True

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from pix2tex.cli import LatexOCR

            self.latex_model = LatexOCR()
            QApplication.restoreOverrideCursor()
            return True
        except ImportError:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self,
                "Missing Library",
                "pix2tex is not installed.\n\nRun: pip install pix2tex[gui] torch torchvision",
            )
            return False
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Model Error", f"Failed to load AI model:\n{e}")
            return False

    def save_document(self) -> None:
        """Saves a copy of the current PDF to a user-specified location."""
        if not self.current_path or not os.path.exists(self.current_path):
            QMessageBox.warning(self, "Save Error", "No document loaded to save.")
            return

        suggested_name = os.path.basename(self.current_path)
        dest_path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF As", suggested_name, "PDF Files (*.pdf)"
        )

        if dest_path:
            try:
                shutil.copy2(self.current_path, dest_path)
                QMessageBox.information(self, "Success", f"Saved to {dest_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file:\n{e}")

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
        Loads a PDF or Markdown file from the specified path.

        Args:
            path: Absolute file path to the file.
            restore_state: If True, attempts to restore the last known page/scroll position.
        """
        if path.lower().endswith(".md"):
            self.current_path = path
            self.settings.setValue("lastFile", path)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()

                html_content = markdown.markdown(
                    text, extensions=["fenced_code", "tables"]
                )

                bg = "#1e1e1e" if self.dark_mode else "#fff"
                fg = "#ddd" if self.dark_mode else "#222"
                style = f"""
                    body {{ background:{bg}; color:{fg}; padding:40px; font-family: sans-serif; max-width: 800px; margin: 0 auto; line-height: 1.6; }}
                    pre {{ background: {"#333" if self.dark_mode else "#f5f5f5"}; padding: 10px; border-radius: 5px; }}
                    code {{ font-family: monospace; }}
                    a {{ color: #50a0ff; }}
                """

                full_html = f"<html><head><style>{style}</style></head><body>{html_content}</body></html>"

                self.web.setHtml(full_html)
                self.view_mode = ViewMode.REFLOW
                self.stack.setCurrentIndex(1)

                self.btn_facing.setEnabled(False)
                self.btn_ocr.setEnabled(False)

            except Exception as e:
                sys.stderr.write(f"Markdown Load Error: {e}\n")
            return

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

        sb = self.scroll.verticalScrollBar()
        was_blocked = sb.signalsBlocked()
        sb.blockSignals(True)

        old_scroll_val = sb.value()

        self.page_widgets.clear()
        self.rendered_pages.clear()
        self._virtual_enabled = False
        self._virtual_range = (0, 0)

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

            top_spacer = QWidget()
            top_spacer.setFixedHeight(max(0, start * page_height))
            top_spacer.setObjectName("topSpacer")
            self._top_spacer = top_spacer
            self.scroll_layout.addWidget(top_spacer)

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

            bottom_spacer = QWidget()
            bottom_spacer.setFixedHeight(max(0, (count - end) * page_height))
            bottom_spacer.setObjectName("bottomSpacer")
            self._bottom_spacer = bottom_spacer
            self.scroll_layout.addWidget(bottom_spacer)

        else:
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

        self.scroll_content.adjustSize()
        QApplication.processEvents()

        if self.continuous_scroll:
            sb.setValue(old_scroll_val)

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

            logical_w = pix.width() / dpr
            logical_h = pix.height() / dpr

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
                    x = int(left * scale)
                    w = int((right - left) * scale)
                    h = int((top - bottom) * scale)
                    y = int(res.height - (top * scale))
                    painter.drawRect(x, y, w, h)

                painter.end()

            if str(idx) in self.annotations:
                painter = QPainter(pix)
                painter.setPen(QPen(QColor(255, 255, 0, 180), 3))
                painter.setBrush(QColor(255, 255, 0, 50))
                for anno in self.annotations[str(idx)]:
                    x = int(anno["rel_pos"][0] * logical_w)
                    y = int(anno["rel_pos"][1] * logical_h)
                    painter.drawEllipse(QPoint(x, y), 10, 10)
                painter.end()

            lbl = self.page_widgets[idx]
            lbl.setPixmap(pix)

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
            raw_text = self.current_doc.get_page_text(self.current_page_index)
            clean_text = re.sub(r"[ \t]+", " ", raw_text)
            safe_text = html.escape(clean_text)
            formatted_text = safe_text.replace("\n", "<br>")

            bg = "#1e1e1e" if self.dark_mode else "#fff"
            fg = "#ddd" if self.dark_mode else "#222"

            katex_head = """
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
                onload="renderMathInElement(document.body, {
                    delimiters: [
                        {left: '$$', right: '$$', display: true},
                        {left: '$', right: '$', display: false},
                        {left: '\\(', right: '\\)', display: false},
                        {left: '\\[', right: '\\]', display: true}
                    ],
                    throwOnError: false
                });">
            </script>
            """

            full_html = f"""<!DOCTYPE html>
            <html>
            <head>
                {katex_head}
                <style>
                    body {{
                        background: {bg};
                        color: {fg};
                        padding: 40px;
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        line-height: 1.6;
                        text-align: left;
                    }}
                    .katex {{ font-size: 1.1em; }}
                    .katex-display {{
                        text-align: left !important;
                        margin-left: 0 !important;
                        text-indent: 0 !important;
                    }}
                </style>
            </head>
            <body>{formatted_text}</body>
            </html>
            """
            self.web.setHtml(full_html)
        except Exception as e:
            sys.stderr.write(f"Reflow Error: {e}\n")

    def toggle_search_bar(self) -> None:
        """Toggles the visibility of the text search bar."""
        visible = not self.search_bar.isVisible()
        self.search_bar.setVisible(visible)
        self.btn_search.setChecked(visible)

        if visible:
            self.txt_search.setFocus()
            self.txt_search.selectAll()
        else:
            self.search_result = None
            self.update_view()

    def find_next(self) -> None:
        """Searches for the next occurrence of the text."""
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(self.txt_search.text())
        else:
            self._find_text(direction=1)

    def find_prev(self) -> None:
        """Searches for the previous occurrence of the text."""
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(
                self.txt_search.text(), QWebEngineView.FindFlag.FindBackward
            )
        else:
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
            except Exception as e:
                print(f"Search error on page {idx}: {e}")
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

        if isinstance(source, QLabel) and self.is_snipping:
            if event.type() == QEvent.Type.MouseButtonPress:
                self.snip_start = event.pos()
                if not self.snip_band:
                    self.snip_band = QRubberBand(QRubberBand.Shape.Rectangle, source)
                self.snip_band.setGeometry(
                    self.snip_start.x(), self.snip_start.y(), 0, 0
                )
                self.snip_band.show()
                return True

            elif event.type() == QEvent.Type.MouseMove:
                if self.snip_band and self.snip_band.isVisible():
                    rect = QRect(self.snip_start, event.pos()).normalized()
                    self.snip_band.setGeometry(rect)
                return True

            elif event.type() == QEvent.Type.MouseButtonRelease:
                if self.snip_band and self.snip_band.isVisible():
                    rect = self.snip_band.geometry()
                    self.snip_band.hide()
                    if rect.width() > 10 and rect.height() > 10:
                        self.process_snip(source, rect)
                return True

        if event.type() == QEvent.Type.MouseButtonPress and isinstance(source, QLabel):
            if self.handle_annotation_click(source, event):
                return True

        return super().eventFilter(source, event)

    def process_snip(self, label: QLabel, rect: QRect):
        """Prepares the image and attempts to run inference."""
        pixmap = label.pixmap()
        if not pixmap:
            return

        dpr = pixmap.devicePixelRatio()
        x = int(rect.x() * dpr)
        y = int(rect.y() * dpr)
        w = int(rect.width() * dpr)
        h = int(rect.height() * dpr)

        scaled_rect = QRect(x, y, w, h)
        cropped = pixmap.copy(scaled_rect)

        buffer = QBuffer()
        buffer.open(QBuffer.ReadWrite)
        cropped.save(buffer, "PNG")
        pil_image = Image.open(io.BytesIO(buffer.data()))

        self.run_latex_inference(pil_image)

    def run_latex_inference(self, pil_image):
        """Checks model status and runs inference or triggers loading."""

        if self.latex_model:
            self._execute_inference(pil_image)
            return

        self._pending_snip_image = pil_image
        self.toggle_snip_mode(False)

        self.progress = QProgressDialog(
            "Initializing AI Engine...\n(First run may trigger a ~150MB download)",
            "Cancel",
            0,
            0,
            self,
        )
        self.progress.setWindowTitle("Please Wait")
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.setCancelButton(None)

        self.loader_thread = LoaderThread()
        self.loader_thread.finished_loading.connect(self._on_model_loaded)
        self.loader_thread.error_occurred.connect(self._on_model_error)

        self.loader_thread.start()

    def _on_model_loaded(self, model_instance):
        """Callback when thread finishes successfully."""
        self.progress.close()
        self.latex_model = model_instance

        if self._pending_snip_image:
            self._execute_inference(self._pending_snip_image)
            self._pending_snip_image = None

    def _on_model_error(self, error_msg: str):
        """Callback when thread loading fails."""
        self.progress.close()
        if "not found" in error_msg:
            reply = QMessageBox.question(
                self,
                "Missing AI Components",
                "The LaTeX OCR feature requires downloading additional AI libraries (~500MB).\n\n"
                "Would you like to download and install them now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.install_dependencies()
            else:
                self._pending_snip_image = None
        else:
            self._pending_snip_image = None
            QMessageBox.critical(self, "Error", error_msg)

    def install_dependencies(self):
        """Starts the background installer."""
        self.progress = QProgressDialog(
            "Downloading and Installing AI Libraries...\n(This may take a few minutes)",
            None,
            0,
            0,
            self,
        )
        self.progress.setWindowTitle("Installing")
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.show()

        self.installer_thread = InstallerThread()
        self.installer_thread.finished_install.connect(self._on_install_finished)
        self.installer_thread.install_error.connect(self._on_install_error)
        self.installer_thread.start()

    def _on_install_finished(self):
        """Called when pip install completes successfully."""
        self.progress.close()
        QMessageBox.information(
            self,
            "Success",
            "AI Libraries installed successfully!\nInitializing engine...",
        )

        self.run_latex_inference(self._pending_snip_image)

    def _on_install_error(self, msg: str):
        """Called when pip install fails."""
        self.progress.close()
        self._pending_snip_image = None
        QMessageBox.critical(self, "Installation Failed", msg)

    def _execute_inference(self, pil_image):
        """Runs the actual prediction on the main thread (inference is fast enough)."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            latex_code = self.latex_model(pil_image)
        except Exception as e:
            latex_code = f"Error during inference: {e}"
        finally:
            QApplication.restoreOverrideCursor()

        self.toggle_snip_mode(False)
        QInputDialog.getMultiLineText(self, "LaTeX Result", "Copy Code:", latex_code)

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

    def resizeEvent(self, event: Any) -> None:
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
        from ..app import RiemannWindow

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

    def handle_annotation_click(self, label: QLabel, event: QMouseEvent) -> bool:
        """
        Handles click events on page labels.
        Returns: True if event was handled (popup shown or annotation created).
        """
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
                return True

        if self.is_annotating:
            self.create_new_annotation(page_idx, rel_x, rel_y)
            return True

        return False

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
        from ..app import RiemannWindow

        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def open_pdf_dialog(self) -> None:
        """Opens a file dialog to load a new PDF into this tab."""
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)
