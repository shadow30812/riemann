import json
import os
import sys
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
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
    QMainWindow,
    QPushButton,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QSplitter,
    QStackedWidget,
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
    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2


class ViewMode(Enum):
    IMAGE = 0
    REFLOW = 1


class ReaderTab(QWidget):
    """
    A self-contained PDF Viewer Widget.
    Manages rendering, navigation, and state for a single open PDF.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        # Shared Settings
        self.settings: QSettings = QSettings("Riemann", "PDFReader")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Core State
        self.engine: Optional[riemann_core.PdfEngine] = None
        self.current_doc: Optional[riemann_core.RiemannDocument] = None
        self.current_path: Optional[str] = None
        self.current_page_index: int = 0

        # View State
        self.dark_mode: bool = self.settings.value("darkMode", True, type=bool)
        self.zoom_mode: ZoomMode = ZoomMode.FIT_WIDTH
        self.manual_scale: float = 1.0
        self.facing_mode: bool = False
        self.continuous_scroll: bool = True
        self.view_mode: ViewMode = ViewMode.IMAGE
        self.is_annotating: bool = False

        # Caching & Storage
        self.page_widgets: Dict[int, QLabel] = {}
        self.rendered_pages: Set[int] = set()
        self.annotations: Dict[str, List[Dict[str, Any]]] = {}

        self._init_backend()
        self.setup_ui()
        self.apply_theme()
        self._setup_scroller()

        # Scroll Event Debouncing
        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.setInterval(150)
        self.scroll_timer.timeout.connect(self.real_scroll_handler)

    # --- Initialization ---

    def _init_backend(self) -> None:
        """Initializes the Rust-based PDF engine."""
        try:
            self.engine = riemann_core.PdfEngine()
        except Exception as e:
            # Fatal error handled in global try/catch, this catches runtime instantiation
            sys.stderr.write(f"Backend Initialization Error: {e}\n")

    def setup_ui(self) -> None:
        """Constructs the UI layout and widgets."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. Toolbar Construction
        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(50)
        t_layout = QHBoxLayout(self.toolbar)

        # Toggle Buttons
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

        # Navigation Controls
        self.btn_prev = QPushButton("◄")
        self.btn_prev.setToolTip("Previous Page (Left Arrow)")
        self.btn_prev.clicked.connect(self.prev_view)

        self.lbl_page = QLabel("0 / 0")
        self.lbl_page.setToolTip("Current Page / Total Pages")

        self.btn_next = QPushButton("►")
        self.btn_next.setToolTip("Next Page (Right Arrow)")
        self.btn_next.clicked.connect(self.next_view)

        # Zoom Controls
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

        # Theme & Window Controls
        self.btn_theme = QPushButton("🌓")
        self.btn_theme.setToolTip("Toggle Dark/Light Mode")
        self.btn_theme.clicked.connect(self.toggle_theme)

        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.setToolTip("Toggle Fullscreen Reader Mode")
        self.btn_fullscreen.clicked.connect(self.toggle_reader_fullscreen)

        # Add to Layout
        widgets = [
            self.btn_reflow,
            self.btn_facing,
            self.btn_scroll_mode,
            self.btn_annotate,
            self.btn_prev,
            self.lbl_page,
            self.btn_next,
            self.combo_zoom,
            self.btn_theme,
            self.btn_fullscreen,
        ]
        for w in widgets:
            t_layout.addWidget(w)
        t_layout.addStretch()
        layout.addWidget(self.toolbar)

        # 2. Main View Stack
        self.stack = QStackedWidget()

        # Page View (Scroll Area)
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

        # Reflow View (Web Engine)
        self.web = QWebEngineView()
        self.stack.addWidget(self.web)

        layout.addWidget(self.stack)

    def _setup_scroller(self) -> None:
        """Configures kinetic scrolling physics."""
        QScroller.grabGesture(
            self.scroll.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        props = QScroller.scroller(self.scroll.viewport()).scrollerProperties()
        props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.5)
        props.setScrollMetric(QScrollerProperties.ScrollMetric.MaximumVelocity, 0.8)
        QScroller.scroller(self.scroll.viewport()).setScrollerProperties(props)

    # --- Document Loading & Core Logic ---

    def load_document(self, path: str, restore_state: bool = False) -> None:
        """
        Loads a PDF from the given path.

        Args:
            path: Absolute path to the PDF file.
            restore_state: If True, restores last page and scroll position.
        """
        try:
            self.current_doc = self.engine.load_document(path)
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
        Reconstructs the layout of QLabels for the document pages.
        Handles logic for Single vs. Continuous scroll and Single vs. Facing pages.
        """
        if not self.current_doc:
            return

        # Clear existing layout
        self.page_widgets.clear()
        self.rendered_pages.clear()

        # Safely remove child widgets
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        count = self.current_doc.page_count

        # Determine which pages need widget placeholders
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

            # Left Page
            lbl_left = self._create_page_label(p_idx)
            row_layout.addWidget(lbl_left)
            self.page_widgets[p_idx] = lbl_left

            if is_pair:
                # Right Page
                p_idx_right = p_idx + 1
                lbl_right = self._create_page_label(p_idx_right)
                row_layout.addWidget(lbl_right)
                self.page_widgets[p_idx_right] = lbl_right
                idx_ptr += 2
            else:
                idx_ptr += 1

            self.scroll_layout.addWidget(row_widget)

    def _create_page_label(self, index: int) -> QLabel:
        """Creates a placeholder label for a page."""
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setProperty("pageIndex", index)
        lbl.setMinimumSize(100, 141)  # Default A4 aspect ratio placeholder
        lbl.setStyleSheet(
            f"background-color: {'#333' if self.dark_mode else '#fff'}; border: 1px solid #555;"
        )
        lbl.installEventFilter(self)
        return lbl

    def render_visible_pages(self) -> None:
        """
        Smart Rendering System:
        1. Identifies pages currently visible + buffer zone.
        2. Evicts non-visible pages to save memory.
        3. Renders new pages via Rust backend.
        """
        if not self.current_doc or not self.page_widgets:
            return

        target_indices: Set[int] = set()

        # Buffer zone: Render 7 pages before and 8 pages after current
        start = max(0, self.current_page_index - 7)
        end = min(self.current_doc.page_count, self.current_page_index + 8)

        for i in range(start, end):
            target_indices.add(i)

        # 1. Evict
        for idx in list(self.rendered_pages):
            if idx not in target_indices:
                if idx in self.page_widgets:
                    self.page_widgets[idx].clear()
                    self.page_widgets[idx].setText(f"Page {idx + 1}")
                self.rendered_pages.remove(idx)

        # 2. Render
        scale = self.calculate_scale()

        for idx in target_indices:
            if idx in self.rendered_pages:
                continue
            if idx not in self.page_widgets:
                continue

            self._render_single_page(idx, scale)
            self.rendered_pages.add(idx)

    def _render_single_page(self, idx: int, scale: float) -> None:
        """Invokes the Rust backend to render a page and updates the UI."""
        try:
            res = self.current_doc.render_page(idx, scale, 1 if self.dark_mode else 0)
            img = QImage(res.data, res.width, res.height, QImage.Format.Format_ARGB32)
            pix = QPixmap.fromImage(img)

            # Draw Annotations overlay
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
            lbl.setMinimumSize(0, 0)  # Allow resize based on content

        except Exception:
            pass  # Fail silently on render error to avoid blocking UI

    def calculate_scale(self) -> float:
        """Determines the render scale based on ZoomMode and Viewport size."""
        if self.zoom_mode == ZoomMode.MANUAL:
            return self.manual_scale

        try:
            # Use page 0 as reference for dimensions
            res = self.current_doc.render_page(0, 1.0, 0)
            base_w = res.width
            base_h = res.height
        except Exception:
            return 1.0

        viewport = self.scroll.viewport()
        vw = viewport.width() - 30
        vh = viewport.height() - 20

        if self.facing_mode and self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / (base_w * 2)
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / base_w
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            return vh / base_h

        return 1.0

    def update_view(self) -> None:
        """Triggers a full view update (Render or Reflow)."""
        if self.view_mode == ViewMode.IMAGE:
            self.render_visible_pages()
            if self.current_doc:
                self.lbl_page.setText(
                    f"{self.current_page_index + 1} / {self.current_doc.page_count}"
                )

            # Save state
            self.settings.setValue("lastPage", self.current_page_index)
            self.settings.setValue(
                "lastScrollY", self.scroll.verticalScrollBar().value()
            )
        else:
            self.render_reflow()

    def render_reflow(self) -> None:
        """Extracts text and renders it as HTML in the WebEngineView."""
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

    # --- Scroll & Navigation Logic ---

    def defer_scroll_update(self, value: int) -> None:
        """Fast scroll handler that defers expensive rendering."""
        self.lbl_page.setText(f"{self.current_page_index + 1}...")
        self.scroll_timer.start()

    def real_scroll_handler(self) -> None:
        """Executed after scroll settles."""
        val = self.scroll.verticalScrollBar().value()
        self.on_scroll_changed(val)

    def on_scroll_changed(self, value: int) -> None:
        """Determines current page index based on scroll position."""
        viewport_center = value + (self.scroll.viewport().height() / 2)
        closest_page = self.current_page_index
        min_dist = float("inf")

        # Heuristic: Scan known widgets to find closest to center
        for idx, widget in self.page_widgets.items():
            w_center = widget.y() + (widget.height() / 2)
            dist = abs(w_center - viewport_center)
            if dist < min_dist:
                min_dist = dist
                closest_page = idx

        if closest_page != self.current_page_index:
            self.current_page_index = closest_page
            if self.current_doc:
                self.lbl_page.setText(
                    f"{self.current_page_index + 1} / {self.current_doc.page_count}"
                )
        self.render_visible_pages()

    def next_view(self) -> None:
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
        step = 2 if self.facing_mode else 1
        new_idx = max(0, self.current_page_index - step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(self.current_page_index)

    def scroll_page(self, direction: int) -> None:
        bar = self.scroll.verticalScrollBar()
        page_step = self.scroll.viewport().height() * 0.9
        bar.setValue(bar.value() + (direction * page_step))

    def ensure_visible(self, index: int) -> None:
        if index in self.page_widgets:
            widget = self.page_widgets[index]
            self.scroll.ensureWidgetVisible(widget, 0, 0)

    # --- Event Handling ---

    def event(self, e: QEvent) -> bool:
        # Native Pinch Gesture Support
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
        if event.type() == QEvent.Type.KeyPress and source == self.scroll:
            self.keyPressEvent(event)
            return True

        if event.type() == QEvent.Type.MouseButtonPress and isinstance(source, QLabel):
            if self.is_annotating:
                self.handle_annotation_click(source, event)
                return True

        return super().eventFilter(source, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            viewport_y = event.position().y()
            content_y = viewport_y + self.scroll.verticalScrollBar().value()

            factor = 1.1 if delta > 0 else 0.9
            self.manual_scale *= factor
            self.zoom_mode = ZoomMode.MANUAL

            self.on_zoom_changed_internal()

            # Approximate scroll restore logic
            new_scroll = (content_y * factor) - viewport_y
            self.scroll.verticalScrollBar().setValue(int(new_scroll))
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mod = event.modifiers()

        # Window-level shortcuts
        if key == Qt.Key.Key_Escape:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()
                event.accept()
                return

        if key == Qt.Key.Key_F11 or key == Qt.Key.Key_F:
            self.toggle_reader_fullscreen()
            event.accept()
            return

        if self.view_mode == ViewMode.IMAGE:
            # Zoom
            if mod & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_Plus or key == Qt.Key.Key_Equal:
                    self.zoom_step(1.1)
                    event.accept()
                    return
                if key == Qt.Key.Key_Minus:
                    self.zoom_step(0.9)
                    event.accept()
                    return

            # Nav
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

            # Toggles
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

    # --- Zoom & Theme ---

    def on_zoom_selected(self, idx: int) -> None:
        self.apply_zoom_string(self.combo_zoom.currentText())

    def on_zoom_text_entered(self) -> None:
        self.apply_zoom_string(self.combo_zoom.lineEdit().text())
        self.scroll.setFocus()

    def apply_zoom_string(self, text: str) -> None:
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
        self.manual_scale *= factor
        self.zoom_mode = ZoomMode.MANUAL
        self.on_zoom_changed_internal()

    def on_zoom_changed_internal(self) -> None:
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)
        self.rendered_pages.clear()
        self.update_view()
        self._sync_zoom_ui()

    def _sync_zoom_ui(self) -> None:
        if self.zoom_mode == ZoomMode.FIT_WIDTH:
            self.combo_zoom.setCurrentText("Fit Width")
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            self.combo_zoom.setCurrentText("Fit Height")
        else:
            self.combo_zoom.setCurrentText(f"{int(self.manual_scale * 100)}%")

    def apply_theme(self) -> None:
        """Applies colors based on Dark/Light mode."""
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
        if self.window() and isinstance(self.window(), RiemannWindow):
            main_win = self.window()
            if main_win.dark_mode == self.dark_mode:
                main_win.toggle_theme()
                return

        # Local toggle (fallback)
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        self.rendered_pages.clear()
        self.update_view()

    # --- Actions: Annotations, etc. ---

    def load_annotations(self) -> None:
        if not self.current_path:
            return
        path = str(self.current_path) + ".riemann.json"
        if os.path.exists(path):
            with open(path, "r") as f:
                self.annotations = json.load(f)
        else:
            self.annotations = {}

    def save_annotations(self) -> None:
        if not self.current_path:
            return
        with open(str(self.current_path) + ".riemann.json", "w") as f:
            json.dump(self.annotations, f)

    def toggle_annotation_mode(self, checked: bool) -> None:
        self.is_annotating = checked
        self.btn_annotate.setChecked(checked)

    def handle_annotation_click(self, label: QLabel, event: QMouseEvent) -> None:
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
        if page_idx in self.rendered_pages:
            self.rendered_pages.remove(page_idx)
        self.render_visible_pages()

    # --- Feature Toggles ---

    def toggle_view_mode(self) -> None:
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        self.stack.setCurrentIndex(0 if self.view_mode == ViewMode.IMAGE else 1)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)
        self.update_view()

    def toggle_facing_mode(self) -> None:
        self.facing_mode = not self.facing_mode
        self.settings.setValue("facingMode", self.facing_mode)
        self.btn_facing.setChecked(self.facing_mode)
        self.rebuild_layout()
        self.update_view()

    def toggle_scroll_mode(self) -> None:
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScrollMode", self.continuous_scroll)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.rebuild_layout()
        self.update_view()

    def toggle_reader_fullscreen(self) -> None:
        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def open_pdf_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)


class RiemannWindow(QMainWindow):
    """
    The Main Window Manager.
    Handles global application state, window chrome, and tab management.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Riemann Reader")
        self.resize(1200, 900)

        self.settings = QSettings("Riemann", "PDFReader")
        self.dark_mode = self.settings.value("darkMode", True, type=bool)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self.splitter)

        # Tab Groups
        self.tabs_main = QTabWidget()
        self.tabs_main.setTabsClosable(True)
        self.tabs_main.tabCloseRequested.connect(self.close_tab)
        self.splitter.addWidget(self.tabs_main)

        self.tabs_side = QTabWidget()
        self.tabs_side.setTabsClosable(True)
        self.tabs_side.tabCloseRequested.connect(self.close_side_tab)
        self.tabs_side.hide()
        self.splitter.addWidget(self.tabs_side)

        self.setup_menu()

        # Restore last session
        last_file = self.settings.value("lastFile", type=str)
        if last_file and os.path.exists(last_file):
            self.new_tab(last_file, restore_state=True)
        else:
            self.new_tab()

    def setup_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")

        open_action = file_menu.addAction("Open PDF")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_pdf_dialog)

        new_tab_action = file_menu.addAction("New Tab")
        new_tab_action.setShortcut("Ctrl+T")
        new_tab_action.triggered.connect(self.new_tab)

        view_menu = menubar.addMenu("View")
        split_action = view_menu.addAction("Split Editor Right")
        split_action.setShortcut("Ctrl+\\")
        split_action.triggered.connect(self.toggle_split_view)

    def new_tab(self, path: Optional[str] = None, restore_state: bool = False) -> None:
        reader = ReaderTab()
        title = "New Tab"

        if path:
            reader.load_document(path, restore_state=restore_state)
            title = os.path.basename(path)

        self.tabs_main.addTab(reader, title)
        self.tabs_main.setCurrentWidget(reader)

    def open_pdf_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            current = self.tabs_main.currentWidget()
            if isinstance(current, ReaderTab) and current.current_path is None:
                current.load_document(path)
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
        widget = self.tabs_main.widget(index)
        if widget:
            widget.deleteLater()
        self.tabs_main.removeTab(index)

    def close_side_tab(self, index: int) -> None:
        widget = self.tabs_side.widget(index)
        if widget:
            widget.deleteLater()
        self.tabs_side.removeTab(index)
        if self.tabs_side.count() == 0:
            self.tabs_side.hide()

    def toggle_reader_fullscreen(self) -> None:
        """
        Toggles 'Zen Mode' reading.
        Hides OS chrome, tab bars, and toolbars for an immersive experience.
        """
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
        for i in range(self.tabs_main.count()):
            w = self.tabs_main.widget(i)
            if isinstance(w, ReaderTab):
                w.toolbar.setVisible(visible)
        for i in range(self.tabs_side.count()):
            w = self.tabs_side.widget(i)
            if isinstance(w, ReaderTab):
                w.toolbar.setVisible(visible)

    def toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self.settings.setValue("darkMode", self.dark_mode)

        def update_tab(tab: QWidget):
            if isinstance(tab, ReaderTab):
                tab.dark_mode = self.dark_mode
                tab.apply_theme()
                tab.rendered_pages.clear()
                tab.update_view()

        for i in range(self.tabs_main.count()):
            update_tab(self.tabs_main.widget(i))
        for i in range(self.tabs_side.count()):
            update_tab(self.tabs_side.widget(i))

    def keyPressEvent(self, event: QKeyEvent) -> None:
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


def run():
    app = QApplication(sys.argv)
    window = RiemannWindow()
    window.show()
    sys.exit(app.exec())
