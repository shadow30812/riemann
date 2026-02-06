"""
Reader Tab Component.

The main aggregator class that combines all mixins to provide
the full PDF reading experience.
"""

import os
import shutil
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QKeySequence,
    QPalette,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.constants import ViewMode, ZoomMode
from ..components import AnnotationToolbar
from .mixins.ai import AiMixin
from .mixins.annotations import AnnotationsMixin
from .mixins.rendering import RenderingMixin
from .mixins.search import SearchMixin
from .utils import generate_markdown_html
from .widgets import PageWidget

try:
    import riemann_core
except ImportError as e:
    print(f"CRITICAL: Could not import riemann_core backend.\nError: {e}")
    sys.exit(1)


class ReaderTab(QWidget, RenderingMixin, AnnotationsMixin, AiMixin, SearchMixin):
    """
    A self-contained PDF Viewer Widget.
    Inherits functional logic from mixins.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initializes the ReaderTab."""
        super().__init__(parent)

        self.settings: QSettings = QSettings("Riemann", "PDFReader")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # State Initialization
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

        # Annotation State
        self.current_tool: str = "nav"
        self.pen_color: str = "#ff0000"
        self.pen_thickness: int = 3
        self.active_drawing: List[QPoint] = []
        self.annotations: Dict[str, List[Dict[str, Any]]] = {}
        self.undo_stack: List[Tuple[str, int, int]] = []
        self.redo_stack: List[Tuple[str, Dict]] = []

        # Snipping State
        self.is_snipping: bool = False
        self.snip_start: QPoint = QPoint()
        self.snip_band: Optional[QRubberBand] = None
        self._pending_snip_image = None
        self.latex_model = None

        # Rendering Cache
        self.form_widgets: Dict[int, List[QWidget]] = {}
        self.form_values_cache: Dict[Tuple[int, Tuple[float, ...]], Any] = {}
        self.page_widgets: Dict[int, PageWidget] = {}
        self.rendered_pages: Set[int] = set()
        self.search_result: Optional[Tuple[int, List[Tuple[float, ...]]]] = None
        self.text_segments_cache: Dict[int, List[Tuple[str, Tuple[float, ...]]]] = {}

        # Virtualization State
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

        self._init_shortcuts()

    def _init_shortcuts(self) -> None:
        """Initializes keyboard shortcuts."""
        shortcuts = [
            ("Ctrl+F", self.toggle_search_bar),
            ("Ctrl+A", self.btn_annotate.click),
            ("Ctrl+Z", self.undo_annotation),
            ("Ctrl+Shift+Z", self.redo_annotation),
        ]
        for seq, slot in shortcuts:
            QShortcut(QKeySequence(seq), self).activated.connect(slot)

        sc1 = QShortcut(QKeySequence("Ctrl+Tab"), self)
        sc1.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc1.activated.connect(lambda: self.cycle_tab(1))

        sc2 = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        sc2.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc2.activated.connect(lambda: self.cycle_tab(-1))

    def _get_tab_widget(self) -> Optional[QTabWidget]:
        """Helper to find the parent QTabWidget."""
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                return parent
            parent = parent.parent()
        return None

    def cycle_tab(self, delta: int) -> None:
        """Cycles to the next or previous tab."""
        tw = self._get_tab_widget()
        if tw:
            count = tw.count()
            next_idx = (tw.currentIndex() + delta) % count
            tw.setCurrentIndex(next_idx)

    def _init_backend(self) -> None:
        """Initializes the Rust-based PDF engine backend."""
        try:
            self.engine = riemann_core.PdfEngine()
        except Exception as e:
            sys.stderr.write(f"Backend Initialization Error: {e}\n")

    def setup_ui(self) -> None:
        """Constructs the visual hierarchy."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(50)
        t_layout = QHBoxLayout(self.toolbar)

        self._setup_toolbar_buttons(t_layout)
        t_layout.addStretch()
        layout.addWidget(self.toolbar)

        self.anno_toolbar = AnnotationToolbar(self)
        self.anno_toolbar.setVisible(False)
        self.anno_toolbar.tool_changed.connect(self.set_tool)
        self.anno_toolbar.color_changed.connect(self.set_color)
        self.anno_toolbar.thickness_changed.connect(self.set_thickness)
        self.anno_toolbar.undo_requested.connect(self.undo_annotation)
        self.anno_toolbar.redo_requested.connect(self.redo_annotation)
        layout.addWidget(self.anno_toolbar)

        self._setup_search_bar()
        layout.addWidget(self.search_bar)

        self.stack = QStackedWidget()
        self._setup_scroll_area()

        self.web = QWebEngineView()
        self.stack.addWidget(self.web)
        self.web.installEventFilter(self)

        layout.addWidget(self.stack)

    def _setup_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        """Creates and configures standard toolbar buttons."""
        self.btn_save = QPushButton("💾")
        self.btn_save.setToolTip("Save Copy of PDF")
        self.btn_save.clicked.connect(self.save_document)

        self.btn_export = QPushButton("📤")
        self.btn_export.setToolTip("Export Annotations to Markdown")
        self.btn_export.clicked.connect(self.export_annotations)

        self.btn_reflow = QPushButton("📄/📝")
        self.btn_reflow.setToolTip("Toggle Text Reflow Mode")
        self.btn_reflow.setCheckable(True)
        self.btn_reflow.clicked.connect(self.toggle_view_mode)

        self.btn_facing = QPushButton("📄/📖")
        self.btn_facing.setToolTip("Toggle Facing Pages")
        self.btn_facing.setCheckable(True)
        self.btn_facing.clicked.connect(self.toggle_facing_mode)

        self.btn_scroll_mode = QPushButton("📄/📜")
        self.btn_scroll_mode.setToolTip("Toggle Scroll Mode")
        self.btn_scroll_mode.setCheckable(True)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.btn_scroll_mode.clicked.connect(self.toggle_scroll_mode)

        self.btn_annotate = QPushButton("🖊️")
        self.btn_annotate.setToolTip("Show Annotation Tools")
        self.btn_annotate.setCheckable(True)
        self.btn_annotate.clicked.connect(self.toggle_annotation_mode)

        self.btn_snip = QPushButton("✂️")
        self.btn_snip.setToolTip("Snip Math to LaTeX")
        self.btn_snip.setCheckable(True)
        self.btn_snip.clicked.connect(self.toggle_snip_mode)

        self.btn_prev = QPushButton("◄")
        self.btn_prev.clicked.connect(self.prev_view)

        self.txt_page = QLineEdit()
        self.txt_page.setFixedWidth(50)
        self.txt_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.txt_page.returnPressed.connect(self.on_page_input_return)

        self.lbl_total = QLabel("/ 0")

        self.btn_next = QPushButton("►")
        self.btn_next.clicked.connect(self.next_view)

        self.combo_zoom = QComboBox()
        self.combo_zoom.setEditable(True)
        self.combo_zoom.addItems(
            ["Fit Width", "Fit Height", "50%", "75%", "100%", "125%", "150%", "200%"]
        )
        self.combo_zoom.currentIndexChanged.connect(self.on_zoom_selected)
        self.combo_zoom.lineEdit().returnPressed.connect(self.on_zoom_text_entered)
        self.combo_zoom.setFixedWidth(100)

        self.btn_theme = QPushButton("🌓")
        self.btn_theme.setToolTip("Toggle Dark/Light Mode")
        self.btn_theme.clicked.connect(self.toggle_theme)

        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.clicked.connect(self.toggle_reader_fullscreen)

        self.btn_ocr = QPushButton("👁️")
        self.btn_ocr.setToolTip("OCR Current Page")
        self.btn_ocr.clicked.connect(self.perform_ocr_current_page)

        self.btn_search = QPushButton("🔍")
        self.btn_search.setCheckable(True)
        self.btn_search.clicked.connect(self.toggle_search_bar)

        widgets = [
            self.btn_save,
            self.btn_export,
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
            layout.addWidget(w)

    def _setup_search_bar(self) -> None:
        """Initializes the find-in-page widget."""
        self.search_bar = QWidget()
        self.search_bar.setVisible(False)
        self.search_bar.setFixedHeight(45)

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

    def _setup_scroll_area(self) -> None:
        """Initializes the scroll area and virtualization container."""
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

    def showEvent(self, event: QEvent) -> None:
        """Grabs focus when the tab is shown."""
        super().showEvent(event)
        if self.view_mode == ViewMode.REFLOW:
            self.web.setFocus()
        else:
            self.setFocus()

    def load_document(self, path: str, restore_state: bool = False) -> None:
        """Loads a PDF or Markdown file."""
        if path.lower().endswith(".md"):
            self._load_markdown(path)
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

    def _load_markdown(self, path: str) -> None:
        """Internal handler for Markdown files."""
        self.current_path = path
        self.settings.setValue("lastFile", path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()

            full_html = generate_markdown_html(text, self.dark_mode)
            self.web.setHtml(full_html)
            self.view_mode = ViewMode.REFLOW
            self.stack.setCurrentIndex(1)
            self.btn_facing.setEnabled(False)
            self.btn_ocr.setEnabled(False)
        except Exception as e:
            sys.stderr.write(f"Markdown Load Error: {e}\n")

    def save_document(self) -> None:
        """Saves a copy of the current PDF."""
        if not self.current_path or not os.path.exists(self.current_path):
            QMessageBox.warning(self, "Save Error", "No document loaded.")
            return

        suggested = os.path.basename(self.current_path)
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save PDF As", suggested, "PDF Files (*.pdf)"
        )

        if dest:
            try:
                shutil.copy2(self.current_path, dest)
                QMessageBox.information(self, "Success", f"Saved to {dest}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file:\n{e}")

    def export_annotations(self) -> None:
        """Exports annotations to a Markdown file."""
        if not self.current_path or not self.annotations:
            QMessageBox.information(self, "Export", "No annotations to export.")
            return

        default_name = (
            os.path.splitext(os.path.basename(self.current_path))[0] + "_notes.md"
        )
        dest_path, _ = QFileDialog.getSaveFileName(
            self, "Export Notes", default_name, "Markdown (*.md)"
        )

        if not dest_path:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with open(dest_path, "w", encoding="utf-8") as f:
                doc_title = os.path.basename(self.current_path)
                f.write(f"# Notes: {doc_title}\n\n")

                sorted_pages = sorted(self.annotations.keys(), key=lambda x: int(x))

                for pid in sorted_pages:
                    page_idx = int(pid)
                    page_num = page_idx + 1
                    f.write(f"## Page {page_num}\n\n")

                    for anno in self.annotations[pid]:
                        atype = anno.get("type")
                        if atype in ("note", "text"):
                            content = anno.get("text", "").replace("\n", "\n> ")
                            if content:
                                f.write(f"- **Note:** {content}\n")
                        elif atype == "markup":
                            subtype = anno.get("subtype", "highlight")
                            f.write(f"- *{subtype.capitalize()}*\n")

                    f.write("\n---\n")

            QApplication.restoreOverrideCursor()
            self.show_toast(f"Exported to {os.path.basename(dest_path)}")

        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export Failed", str(e))

    def _setup_scroller(self) -> None:
        """Configures kinetic scrolling."""
        QScroller.grabGesture(
            self.scroll.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        props = QScroller.scroller(self.scroll.viewport()).scrollerProperties()
        props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.5)
        props.setScrollMetric(QScrollerProperties.ScrollMetric.MaximumVelocity, 0.8)
        QScroller.scroller(self.scroll.viewport()).setScrollerProperties(props)

    def defer_scroll_update(self, value: int) -> None:
        """Debounces scroll events."""
        self.txt_page.setText(str(self.current_page_index + 1))
        self.scroll_timer.start()

    def real_scroll_handler(self) -> None:
        """Handles delayed scroll logic."""
        self.on_scroll_changed(self.scroll.verticalScrollBar().value())

    def on_scroll_changed(self, value: int) -> None:
        """Updates current page index based on scroll position."""
        center = value + (self.scroll.viewport().height() / 2)
        closest, min_dist = self.current_page_index, float("inf")

        for idx, widget in self.page_widgets.items():
            try:
                w_center = widget.mapTo(self.scroll_content, QPoint(0, 0)).y() + (
                    widget.height() / 2
                )
                dist = abs(w_center - center)
                if dist < min_dist:
                    min_dist = dist
                    closest = idx
            except RuntimeError:
                continue

        if closest != self.current_page_index:
            self.current_page_index = closest
            if self.current_doc:
                self.txt_page.setText(str(closest + 1))

        if self._virtual_enabled:
            s, e = self._virtual_range
            if self.current_page_index > e - 10 or self.current_page_index < s + 10:
                self.rebuild_layout()
                self.ensure_visible(self.current_page_index)

        self.render_visible_pages()

    def ensure_visible(self, index: int) -> None:
        """Scrolls to make the page visible."""
        if index in self.page_widgets:
            self.scroll.ensureWidgetVisible(self.page_widgets[index], 0, 0)
            return

        if self._virtual_enabled and self._cached_base_size:
            start, _ = self._virtual_range
            _, bh = self._cached_base_size
            ph = int(bh * self.calculate_scale()) + self.scroll_layout.spacing()
            top = self._top_spacer.height() if self._top_spacer else 0
            y = top + max(0, index - start) * ph
            self.scroll.verticalScrollBar().setValue(
                max(0, int(y - self.scroll.viewport().height() / 2))
            )

    def next_view(self) -> None:
        """Next page."""
        if not self.current_doc:
            return
        step = 2 if self.facing_mode else 1
        new_idx = min(self.current_doc.page_count - 1, self.current_page_index + step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(new_idx)

    def prev_view(self) -> None:
        """Previous page."""
        step = 2 if self.facing_mode else 1
        new_idx = max(0, self.current_page_index - step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(new_idx)

    def toggle_view_mode(self) -> None:
        """Switches Reflow/Image mode."""
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        self.stack.setCurrentIndex(1 if self.view_mode == ViewMode.REFLOW else 0)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)
        self.update_view()

    def toggle_facing_mode(self) -> None:
        """Toggles facing pages."""
        self.facing_mode = not self.facing_mode
        self.settings.setValue("facingMode", self.facing_mode)
        self.btn_facing.setChecked(self.facing_mode)
        self.rebuild_layout()
        self.update_view()

    def toggle_scroll_mode(self) -> None:
        """Toggles continuous scroll."""
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScrollMode", self.continuous_scroll)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.rebuild_layout()
        self.update_view()

    def toggle_reader_fullscreen(self) -> None:
        """Toggles app fullscreen."""
        from ...app import RiemannWindow

        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def open_pdf_dialog(self) -> None:
        """Opens file dialog."""
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF (*.pdf)")
        if path:
            self.load_document(path)

    def scroll_page(self, direction: int) -> None:
        """Scrolls one page height."""
        bar = self.scroll.verticalScrollBar()
        step = self.scroll.viewport().height() * 0.9
        bar.setValue(bar.value() + (direction * step))

    def on_page_input_return(self) -> None:
        """Navigates to the entered page number."""
        if not self.current_doc:
            return
        try:
            num = int(self.txt_page.text().strip())
            if 1 <= num <= self.current_doc.page_count:
                idx = num - 1
                if idx != self.current_page_index:
                    self.current_page_index = idx
                    if not self.continuous_scroll or (
                        self._virtual_enabled
                        and (
                            idx < self._virtual_range[0]
                            or idx >= self._virtual_range[1]
                        )
                    ):
                        self.rebuild_layout()
                    self.update_view()
                    self.ensure_visible(idx)
                    self.scroll.setFocus()
            else:
                raise ValueError
        except ValueError:
            self.txt_page.setText(str(self.current_page_index + 1))

    def show_toast(self, msg: str) -> None:
        """Shows temporary message."""
        self.lbl_toast = QLabel(self)
        self.lbl_toast.setStyleSheet(
            "background: #333; color: white; padding: 10px; border-radius: 5px;"
        )
        self.lbl_toast.setText(msg)
        self.lbl_toast.adjustSize()
        self.lbl_toast.move(
            (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
        )
        self.lbl_toast.show()
        QTimer.singleShot(4000, self.lbl_toast.hide)

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """Handles tool interactions on PageWidgets."""
        if isinstance(source, PageWidget):
            page_idx = source.property("pageIndex")

            if self.is_snipping:
                if event.type() == QEvent.Type.MouseButtonPress:
                    self.snip_start = event.pos()
                    if not self.snip_band:
                        self.snip_band = QRubberBand(
                            QRubberBand.Shape.Rectangle, source
                        )
                    self.snip_band.setGeometry(
                        self.snip_start.x(), self.snip_start.y(), 0, 0
                    )
                    self.snip_band.show()
                    return True
                elif event.type() == QEvent.Type.MouseMove and self.snip_band:
                    self.snip_band.setGeometry(
                        QRect(self.snip_start, event.pos()).normalized()
                    )
                    return True
                elif event.type() == QEvent.Type.MouseButtonRelease and self.snip_band:
                    rect = self.snip_band.geometry()
                    self.snip_band.hide()
                    if rect.width() > 10 and rect.height() > 10:
                        self.process_snip(source, rect)
                    return True

            if self.anno_toolbar.isVisible() and self.current_tool != "nav":
                if event.type() == QEvent.Type.MouseButtonPress:
                    if self.current_tool == "note":
                        if self.handle_annotation_click(source, event):
                            return True
                        self.create_new_annotation(
                            page_idx,
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        return True
                    elif self.current_tool in (
                        "pen",
                        "highlight",
                        "markup_highlight",
                        "markup_underline",
                        "markup_strikeout",
                    ):
                        self.active_drawing = [event.pos()]
                        if self.current_tool.startswith("markup"):
                            self.current_markup_rects = []
                        return True
                    elif self.current_tool == "eraser":
                        self._handle_eraser_click(source, event.pos(), page_idx)
                        return True

                elif event.type() == QEvent.Type.MouseMove and self.active_drawing:
                    if self.current_tool.startswith("markup"):
                        rect = QRect(self.active_drawing[0], event.pos()).normalized()
                        source.set_markup_preview([rect], QColor(255, 255, 0, 100))
                    else:
                        self.active_drawing.append(event.pos())
                        source.set_temp_stroke(
                            self.active_drawing,
                            self.pen_color,
                            self.pen_thickness,
                            self.current_tool == "highlight",
                        )
                    return True

                elif event.type() == QEvent.Type.MouseButtonRelease:
                    if (
                        self.current_tool in ("pen", "highlight")
                        and self.active_drawing
                    ):
                        w, h = source.width(), source.height()
                        pts = [(p.x() / w, p.y() / h) for p in self.active_drawing]
                        self._add_anno_data(
                            page_idx,
                            {
                                "type": "drawing",
                                "subtype": self.current_tool,
                                "points": pts,
                                "color": self.pen_color,
                                "thickness": self.pen_thickness,
                            },
                        )
                        source.clear_temp_stroke()
                        self.active_drawing = []
                        return True

        return super().eventFilter(source, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handles keyboard navigation."""
        key = event.key()
        mod = event.modifiers()

        if key == Qt.Key.Key_Escape:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()
                event.accept()
                return

        if key == Qt.Key.Key_F11:
            self.toggle_reader_fullscreen()
            return

        if mod == Qt.KeyboardModifier.NoModifier:
            if key == Qt.Key.Key_R:
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

            elif key == Qt.Key.Key_F:
                self.toggle_reader_fullscreen()
                event.accept()
                return

        if self.view_mode == ViewMode.IMAGE:
            if mod & Qt.KeyboardModifier.ControlModifier:
                if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    self.zoom_step(1.1)
                elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                    self.zoom_step(0.9)
                return

            if key == Qt.Key.Key_Right:
                self.next_view()
            elif key == Qt.Key.Key_Left:
                self.prev_view()
            elif key == Qt.Key.Key_Space:
                self.scroll_page(-1 if mod & Qt.KeyboardModifier.ShiftModifier else 1)

            elif key == Qt.Key.Key_Up:
                self.scroll.verticalScrollBar().setValue(
                    self.scroll.verticalScrollBar().value() - 50
                )
            elif key == Qt.Key.Key_Down:
                self.scroll.verticalScrollBar().setValue(
                    self.scroll.verticalScrollBar().value() + 50
                )

            elif key == Qt.Key.Key_Home:
                self.scroll.verticalScrollBar().setValue(0)
            elif key == Qt.Key.Key_End:
                self.scroll.verticalScrollBar().setValue(
                    self.scroll.verticalScrollBar().maximum()
                )

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handles Ctrl+Scroll zoom."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else 0.9
            self.manual_scale = max(0.1, min(self.manual_scale * factor, 5.0))
            self.zoom_mode = ZoomMode.MANUAL
            self.on_zoom_changed_internal()
            event.accept()
        else:
            super().wheelEvent(event)

    def on_zoom_selected(self, idx: int) -> None:
        """Handles zoom combo change."""
        self.apply_zoom_string(self.combo_zoom.currentText())

    def on_zoom_text_entered(self) -> None:
        """Handles manual zoom text."""
        self.apply_zoom_string(self.combo_zoom.lineEdit().text())
        self.scroll.setFocus()

    def apply_zoom_string(self, text: str) -> None:
        """Parses zoom string."""
        if "Width" in text:
            self.zoom_mode = ZoomMode.FIT_WIDTH
        elif "Height" in text:
            self.zoom_mode = ZoomMode.FIT_HEIGHT
        else:
            try:
                val = float(text.replace("%", "").strip())
                self.manual_scale = max(
                    0.1, min(val / 100.0 if val > 5.0 else val, 5.0)
                )
                self.zoom_mode = ZoomMode.MANUAL
            except ValueError:
                pass
        self.on_zoom_changed_internal()

    def on_zoom_changed_internal(self) -> None:
        """Updates UI after zoom change."""
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)
        self._update_all_widget_sizes()
        self.rendered_pages.clear()
        self.update_view()

        if self.zoom_mode == ZoomMode.FIT_WIDTH:
            txt = "Fit Width"
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            txt = "Fit Height"
        else:
            txt = f"{int(self.manual_scale * 100)}%"
        self.combo_zoom.setCurrentText(txt)

    def _update_all_widget_sizes(self) -> None:
        """Resizes all PageWidgets."""
        w, h = self._get_target_page_size()
        for lbl in self.page_widgets.values():
            lbl.setFixedSize(w, h)

    def zoom_step(self, factor: float) -> None:
        """Helper for keyboard zoom."""
        self.manual_scale *= factor
        self.zoom_mode = ZoomMode.MANUAL
        self.on_zoom_changed_internal()

    def apply_theme(self) -> None:
        """Updates colors for dark/light mode."""
        pal = self.palette()
        color = QColor(30, 30, 30) if self.dark_mode else QColor(240, 240, 240)
        pal.setColor(QPalette.ColorRole.Window, color)
        self.setPalette(pal)

        bg_scroll = "#222" if self.dark_mode else "#eee"
        self.scroll_content.setStyleSheet(
            f"#scrollContent {{ background-color: {bg_scroll}; }}"
        )

        fg = "#ddd" if self.dark_mode else "#111"

        checked_bg = (
            "rgba(60, 140, 255, 0.3)" if self.dark_mode else "rgba(0, 100, 255, 0.2)"
        )
        checked_border = "#50a0ff"

        self.toolbar.setStyleSheet(f"""
            QWidget {{ background: {color.name()}; color: {fg}; }}
            QPushButton {{ 
                border: 1px solid transparent; 
                padding: 6px; 
                border-radius: 4px; 
                background: transparent;
            }}
            QPushButton:hover {{ 
                background: rgba(128, 128, 128, 0.2); 
            }}
            QPushButton:checked {{ 
                background-color: {checked_bg}; 
                border: 1px solid {checked_border}; 
            }}
        """)

        sb_bg = "#2a2a2a" if self.dark_mode else "#e0e0e0"
        sb_fg = "#ddd" if self.dark_mode else "#111"
        input_bg = "#1e1e1e" if self.dark_mode else "#ffffff"
        input_border = "#555" if self.dark_mode else "#bbb"

        self.search_bar.setStyleSheet(f"""
            QWidget {{ background-color: {sb_bg}; color: {sb_fg}; }}
            QLineEdit {{
                background-color: {input_bg};
                color: {sb_fg};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px;
            }}
            QPushButton {{ background: transparent; border: none; }}
            QPushButton:hover {{ background: rgba(128,128,128,0.2); border-radius: 4px; }}
        """)

    def toggle_theme(self) -> None:
        """Switches theme."""
        from ...app import RiemannWindow

        if self.window() and isinstance(self.window(), RiemannWindow):
            main = self.window()
            if main.dark_mode == self.dark_mode:
                main.toggle_theme()
                return
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        self.rendered_pages.clear()
        self.update_view()
