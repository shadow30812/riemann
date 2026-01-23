import json
import os
import sys
from enum import Enum

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QSettings,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
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
    QMenu,
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
    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2


class ViewMode(Enum):
    IMAGE = 0
    REFLOW = 1


class ReaderTab(QWidget):
    """
    A self-contained PDF Viewer Widget.
    Previously the entire RiemannWindow, now just one 'Tab'.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # We share the main settings, but manage our own view state
        self.settings = QSettings("Riemann", "PDFReader")
        self.setFocusPolicy(Qt.StrongFocus)

        # State
        self.engine = None
        self.current_doc = None
        self.current_path = None
        self.current_page_index = 0

        # Local View State
        self.dark_mode = self.settings.value("darkMode", True, type=bool)
        self.zoom_mode = ZoomMode.FIT_WIDTH
        self.manual_scale = 1.0
        self.facing_mode = False
        self.continuous_scroll = True
        self.view_mode = ViewMode.IMAGE

        self.is_annotating = False

        # Caching
        self.page_widgets = {}
        self.rendered_pages = set()
        self.annotations = {}

        self._init_backend()
        self.setup_ui()
        self.apply_theme()  # Apply local theme preference
        self._setup_scroller()

        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.setInterval(150)  # Wait 150ms after scroll stops
        self.scroll_timer.timeout.connect(self.real_scroll_handler)

    def _init_backend(self):
        # We create a fresh engine handle per tab,
        # or you could pass a shared engine singleton.
        try:
            self.engine = riemann_core.PdfEngine()
        except Exception as e:
            print(f"Backend Error: {e}")

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Toolbar ---
        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(50)
        t_layout = QHBoxLayout(self.toolbar)

        # Reflow (Toggle)
        self.btn_reflow = QPushButton("📄/📝")
        self.btn_reflow.setToolTip("Toggle Text Reflow Mode")
        self.btn_reflow.setCheckable(True)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)
        self.btn_reflow.clicked.connect(self.toggle_view_mode)

        # Facing (Toggle)
        self.btn_facing = QPushButton("📄/📖")
        self.btn_facing.setToolTip("Toggle Facing Pages (Single / Two-Page View)")
        self.btn_facing.setCheckable(True)
        self.btn_facing.setChecked(self.facing_mode)
        self.btn_facing.clicked.connect(self.toggle_facing_mode)

        # Scroll Mode (Toggle)
        self.btn_scroll_mode = QPushButton("📄/📜")
        self.btn_scroll_mode.setToolTip("Toggle Scroll Mode (Single Page / Continuous)")
        self.btn_scroll_mode.setCheckable(True)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.btn_scroll_mode.clicked.connect(self.toggle_scroll_mode)

        # Annotate (Toggle)
        self.btn_annotate = QPushButton("🖊️")
        self.btn_annotate.setToolTip("Enable/Disable Annotation Mode")
        self.btn_annotate.setCheckable(True)
        self.btn_annotate.clicked.connect(self.toggle_annotation_mode)

        # Nav
        self.btn_prev = QPushButton("◄")
        self.btn_prev.setToolTip("Previous Page (Left Arrow)")
        self.btn_prev.clicked.connect(self.prev_view)

        self.lbl_page = QLabel("0 / 0")
        self.lbl_page.setToolTip("Current Page / Total Pages")

        self.btn_next = QPushButton("►")
        self.btn_next.setToolTip("Next Page (Right Arrow)")
        self.btn_next.clicked.connect(self.next_view)

        # Zoom
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

        # Theme
        self.btn_theme = QPushButton("🌓")
        self.btn_theme.setToolTip("Toggle Dark/Light Mode")
        self.btn_theme.clicked.connect(self.toggle_theme)

        # Fullscreen
        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.setToolTip(
            "Toggle Fullscreen Reader Mode (F11 to enter, Esc to exit)"
        )
        self.btn_fullscreen.clicked.connect(self.toggle_reader_fullscreen)

        # Add widgets
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

        # --- Main Stack ---
        self.stack = QStackedWidget()

        # Reader View (Scroll Area)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidgetResizable(True)

        self.scroll.installEventFilter(self)
        self.scroll.setFocusPolicy(
            Qt.NoFocus
        )  # Force focus to remain on the Tab or be proxied

        # New Rendering Architecture: QWidget Container
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(20)  # Gap between pages
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scroll.setWidget(self.scroll_content)

        self.scroll.verticalScrollBar().valueChanged.connect(self.defer_scroll_update)
        self.stack.addWidget(self.scroll)

        # Reflow View
        self.web = QWebEngineView()
        self.stack.addWidget(self.web)

        layout.addWidget(self.stack)

    def load_document(self, path, restore_state=False):
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

                # We need to build layout before scrolling
                self.rebuild_layout()
                self.update_view()

                # Delayed scroll restore
                QTimer.singleShot(
                    50, lambda: self.scroll.verticalScrollBar().setValue(saved_scroll)
                )
            else:
                self.current_page_index = 0
                self.rebuild_layout()
                self.update_view()

        except Exception as e:
            print(f"Load error: {e}")

    def defer_scroll_update(self, value):
        # 1. Fast update for the label (cheap)
        self.lbl_page.setText(f"{self.current_page_index + 1}...")
        # 2. Restart timer for expensive render
        self.scroll_timer.start()

    def real_scroll_handler(self):
        val = self.scroll.verticalScrollBar().value()
        self.on_scroll_changed(val)

    def rebuild_layout(self):
        """
        Reconstructs the QLabels for the document based on:
        1. continuous_scroll
        2. facing_mode
        """
        if not self.current_doc:
            return

        # Clear existing layout
        self.page_widgets.clear()
        self.rendered_pages.clear()

        # Remove child widgets properly
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        count = self.current_doc.page_count

        # Determine loop strategy
        if self.continuous_scroll:
            pages_to_layout = range(count)
        else:
            # In single mode, we only layout the current view
            if self.facing_mode:
                # Round down to even for start of pair
                start = (self.current_page_index // 2) * 2
                pages_to_layout = range(start, min(start + 2, count))
            else:
                pages_to_layout = range(
                    self.current_page_index, self.current_page_index + 1
                )

        # Generator logic
        indices = list(pages_to_layout)
        idx_ptr = 0

        while idx_ptr < len(indices):
            p_idx = indices[idx_ptr]

            # Logic for Facing Pairs
            is_pair = False
            if self.facing_mode and (p_idx + 1 < count) and (p_idx % 2 == 0):
                is_pair = True

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

    def _create_page_label(self, index):
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setProperty("pageIndex", index)
        # Placeholder size
        lbl.setMinimumSize(100, 141)
        lbl.setStyleSheet(
            f"background-color: {'#333' if self.dark_mode else '#fff'}; border: 1px solid #555;"
        )

        # Mouse event for annotation
        lbl.installEventFilter(self)

        return lbl

    def render_visible_pages(self):
        """
        The caching system.
        1. Identify visible widgets.
        2. Render them + neighbors.
        3. Evict others.
        """
        if not self.current_doc or not self.page_widgets:
            return

        target_indices = set()

        # Range to render (neighbors strategy)
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

    def _render_single_page(self, idx, scale):
        try:
            # Call backend
            res = self.current_doc.render_page(idx, scale, 1 if self.dark_mode else 0)
            img = QImage(res.data, res.width, res.height, QImage.Format.Format_ARGB32)
            pix = QPixmap.fromImage(img)

            # Draw Annotations
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
            # Remove fixed size so layout adapts to pixmap
            lbl.setMinimumSize(0, 0)

        except Exception as e:
            print(f"[reader] Render fail page {idx}: {e}")

    def update_view(self):
        """Master update function"""
        if self.view_mode == ViewMode.IMAGE:
            # 2. Render content
            self.render_visible_pages()

            # 3. Update Status
            self.lbl_page.setText(
                f"{self.current_page_index + 1} / {self.current_doc.page_count}"
            )

            # 4. Save State
            self.settings.setValue("lastPage", self.current_page_index)
            self.settings.setValue(
                "lastScrollY", self.scroll.verticalScrollBar().value()
            )
        else:
            self.render_reflow()

    def calculate_scale(self):
        if self.zoom_mode == ZoomMode.MANUAL:
            return self.manual_scale

        # Calculate base page size (page 0)
        try:
            res = self.current_doc.render_page(0, 1.0, 0)
            base_w = res.width
            base_h = res.height
        except:
            return 1.0

        viewport = self.scroll.viewport()
        vw = viewport.width() - 30  # Scrollbar margin
        vh = viewport.height() - 20

        if self.facing_mode and self.zoom_mode == ZoomMode.FIT_WIDTH:
            # Fit two pages width
            return vw / (base_w * 2)
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / base_w
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            return vh / base_h

        return 1.0

    def apply_theme(self):
        pal = self.palette()
        color = QColor(30, 30, 30) if self.dark_mode else QColor(240, 240, 240)
        pal.setColor(QPalette.ColorRole.Window, color)
        self.setPalette(pal)

        # Stylesheet for container
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

    def _setup_scroller(self):
        # Enable Kinetic Scrolling
        QScroller.grabGesture(self.scroll.viewport(), QScroller.LeftMouseButtonGesture)
        props = QScroller.scroller(self.scroll.viewport()).scrollerProperties()

        # Tune physics for natural feel
        props.setScrollMetric(QScrollerProperties.DecelerationFactor, 0.5)
        props.setScrollMetric(QScrollerProperties.MaximumVelocity, 0.8)
        QScroller.scroller(self.scroll.viewport()).setScrollerProperties(props)

    def _sync_zoom_ui(self):
        if self.zoom_mode == ZoomMode.FIT_WIDTH:
            self.combo_zoom.setCurrentText("Fit Width")
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            self.combo_zoom.setCurrentText("Fit Height")
        else:
            self.combo_zoom.setCurrentText(f"{int(self.manual_scale * 100)}%")

    # --- Events & Input ---

    def event(self, e):
        # Pinch Gesture Support
        if e.type() == QEvent.Type.NativeGesture:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                scale_factor = e.value()  # Delta usually
                self.zoom_mode = ZoomMode.MANUAL
                self.manual_scale *= 1.0 + scale_factor
                self.manual_scale = max(0.1, min(self.manual_scale, 5.0))
                self.on_zoom_changed_internal()
                return True

        return super().event(e)

    def toggle_reader_fullscreen(self):
        """
        Delegate fullscreen toggling to the parent window.
        """
        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def eventFilter(self, source, event):
        if event.type() == QEvent.Type.KeyPress and source == self.scroll:
            self.keyPressEvent(event)
            return True

        # Handle clicks on page labels for annotation
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(source, QLabel):
            if self.is_annotating:
                self.handle_annotation_click(source, event)
                return True

        return super().eventFilter(source, event)

    def wheelEvent(self, event):
        # Ctrl + Wheel = Zoom
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            viewport_y = event.position().y()
            content_y = viewport_y + self.scroll.verticalScrollBar().value()

            # Determine zoom factor
            factor = 1.1 if delta > 0 else 0.9

            self.manual_scale *= factor
            self.zoom_mode = ZoomMode.MANUAL

            # Update Content (Clears cache internally via on_zoom_changed_internal)
            self.on_zoom_changed_internal()

            # Approximate scroll restore
            new_scroll = (content_y * factor) - viewport_y
            self.scroll.verticalScrollBar().setValue(int(new_scroll))

            event.accept()
        else:
            super().wheelEvent(event)

    # --- Navigation Helpers ---

    def next_view(self):
        step = 2 if self.facing_mode else 1
        new_idx = min(self.current_doc.page_count - 1, self.current_page_index + step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(self.current_page_index)

    def prev_view(self):
        step = 2 if self.facing_mode else 1
        new_idx = max(0, self.current_page_index - step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(self.current_page_index)

    def scroll_page(self, direction):
        # Spacebar scrolling
        bar = self.scroll.verticalScrollBar()
        page_step = self.scroll.viewport().height() * 0.9
        bar.setValue(bar.value() + (direction * page_step))

    def ensure_visible(self, index):
        """Scroll to the specific page widget"""
        if index in self.page_widgets:
            widget = self.page_widgets[index]
            self.scroll.ensureWidgetVisible(widget, 0, 0)

    # --- File & System ---

    def on_scroll_changed(self, value):
        # Dynamically update current_page_index based on what's visible
        # This fixes the issue where scrolling far away results in blank pages
        viewport_center = value + (self.scroll.viewport().height() / 2)

        closest_page = self.current_page_index
        min_dist = float("inf")

        # Heuristic: Scan a range around the current index first for performance
        # Fallback to full scan if needed, or just scan rendered widgets
        scan_targets = self.page_widgets.items()

        for idx, widget in scan_targets:
            # Widget center y
            w_center = widget.y() + (widget.height() / 2)
            dist = abs(w_center - viewport_center)
            if dist < min_dist:
                min_dist = dist
                closest_page = idx

        if closest_page != self.current_page_index:
            self.current_page_index = closest_page
            self.lbl_page.setText(
                f"{self.current_page_index + 1} / {self.current_doc.page_count}"
            )
        self.render_visible_pages()

    # --- Toggles & Actions ---

    def toggle_facing_mode(self):
        self.facing_mode = not self.facing_mode
        self.settings.setValue("facingMode", self.facing_mode)
        # Visual feedback handled by checkable state
        self.btn_facing.setChecked(self.facing_mode)
        self.rebuild_layout()
        self.update_view()

    def toggle_scroll_mode(self):
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScrollMode", self.continuous_scroll)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.rebuild_layout()
        self.update_view()

    def keyPressEvent(self, event):
        """Handle global keys for the window"""
        key = event.key()
        mod = event.modifiers()

        # --- 1. Global Window Shortcuts (Esc, F11) ---
        if key == Qt.Key.Key_Escape:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()
                event.accept()
                return

        if key == Qt.Key.Key_F11 or key == Qt.Key.Key_F:
            self.toggle_reader_fullscreen()
            event.accept()
            return

        # --- 2. Navigation & View Shortcuts ---
        if self.view_mode == ViewMode.IMAGE:
            # Zoom (Ctrl + / -)
            if mod & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_Plus or key == Qt.Key.Key_Equal:
                    self.zoom_step(1.1)
                    event.accept()
                    return
                if key == Qt.Key.Key_Minus:
                    self.zoom_step(0.9)
                    event.accept()
                    return

            # Navigation (Arrows, Space)
            if key == Qt.Key.Key_Right:
                self.next_view()
                event.accept()
                return
            elif key == Qt.Key.Key_Left:
                self.prev_view()
                event.accept()
                return
            elif key == Qt.Key.Key_Space:
                if mod & Qt.KeyboardModifier.ShiftModifier:
                    self.scroll_page(-1)
                else:
                    self.scroll_page(1)
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

            # --- 3. Feature Toggles (N, R, C, D) ---
            elif key == Qt.Key.Key_N:  # N for Night/Dark Mode
                self.toggle_theme()
                event.accept()
                return
            elif key == Qt.Key.Key_R:  # R for Reflow
                self.toggle_view_mode()
                event.accept()
                return
            elif key == Qt.Key.Key_C:  # C for Continuous
                self.toggle_scroll_mode()
                event.accept()
                return
            elif key == Qt.Key.Key_D:  # D for Dual Page
                self.toggle_facing_mode()
                event.accept()
                return
            elif key == Qt.Key.Key_W:  # W for Fit Width
                self.apply_zoom_string("Fit Width")
                event.accept()
                return
            elif key == Qt.Key.Key_H:  # H for Fit Height
                self.apply_zoom_string("Fit Height")
                event.accept()
                return

        super().keyPressEvent(event)

    def toggle_theme(self):
        # 1. If this was triggered by a button click (user action), notify the Window.
        # We check if the local state matches the global state to detect a "switch" attempt.
        if self.window() and isinstance(self.window(), RiemannWindow):
            main_win = self.window()
            # If our local mode matches the window, it means we are initiating a change
            if main_win.dark_mode == self.dark_mode:
                main_win.toggle_theme()  # Delegate to Manager
                return

        # 2. If we are here, it means the Window called us (or we are standalone).
        # We just apply the visual changes.
        self.apply_theme()
        self.rendered_pages.clear()
        self.update_view()

    # --- Zoom Impl ---

    def on_zoom_selected(self, idx):
        self.apply_zoom_string(self.combo_zoom.currentText())

    def on_zoom_text_entered(self):
        self.apply_zoom_string(self.combo_zoom.lineEdit().text())
        self.scroll.setFocus()

    def apply_zoom_string(self, text):
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

    def zoom_step(self, factor):
        self.manual_scale *= factor
        self.zoom_mode = ZoomMode.MANUAL
        self.on_zoom_changed_internal()

    def on_zoom_changed_internal(self):
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)
        self.rendered_pages.clear()
        self.update_view()
        self._sync_zoom_ui()

    # --- Boilerplate ---

    def load_annotations(self):
        path = str(self.current_path) + ".riemann.json"
        if os.path.exists(path):
            with open(path, "r") as f:
                self.annotations = json.load(f)
        else:
            self.annotations = {}

    def open_pdf_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)

    def toggle_view_mode(self):
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        self.stack.setCurrentIndex(0 if self.view_mode == ViewMode.IMAGE else 1)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)
        self.update_view()

    def render_reflow(self):
        try:
            text = self.current_doc.get_page_text(self.current_page_index)
            bg = "#1e1e1e" if self.dark_mode else "#fff"
            fg = "#ddd" if self.dark_mode else "#222"
            html = f"<html><body style='background:{bg};color:{fg};padding:40px;'>{text}</body></html>"
            self.web.setHtml(html)
        except Exception as e:
            # Added error print to catch other potential issues (e.g. index out of bounds)
            print(f"Reflow Error: {e}")

    def toggle_annotation_mode(self, checked):
        self.is_annotating = checked
        self.btn_annotate.setChecked(checked)

    def handle_annotation_click(self, label, event):
        # 1. Get click coordinates relative to the page image
        page_idx = label.property("pageIndex")
        click_x = event.pos().x()
        click_y = event.pos().y()

        # Normalize to 0.0 - 1.0 range (same as we store them)
        rel_x = click_x / label.width()
        rel_y = click_y / label.height()

        page_annos = self.annotations.get(str(page_idx), [])

        # 2. HIT TEST: Check if we clicked near an existing annotation
        # Threshold: 20 pixels radius (approx 0.02 - 0.05 relative distance depending on zoom)
        hit_threshold_px = 20

        for i, anno in enumerate(page_annos):
            ax, ay = anno["rel_pos"]

            # Convert stored relative pos back to pixels for distance check
            px_x = ax * label.width()
            px_y = ay * label.height()

            # Pythagorean distance
            dist = ((click_x - px_x) ** 2 + (click_y - px_y) ** 2) ** 0.5

            if dist < hit_threshold_px:
                # HIT FOUND! Show the note.
                self.show_annotation_popup(anno, page_idx, i)
                return

        # 3. NO HIT: If we are in "Edit Mode", create a new one
        if self.is_annotating:
            self.create_new_annotation(page_idx, rel_x, rel_y)

    def show_annotation_popup(self, anno_data, page_idx, anno_index):
        """Allows viewing and Deleting annotations"""
        text = anno_data.get("text", "")

        # We use QInputDialog to show text.
        # Ideally, use a custom dialog if you want "Delete" buttons,
        # but this is a quick hack: users can clear text to delete.
        new_text, ok = QInputDialog.getText(
            self, "View Note", "Content (Clear text to delete):", text=text
        )

        if ok:
            if not new_text.strip():
                # DELETE if user cleared the text
                del self.annotations[str(page_idx)][anno_index]
            else:
                # UPDATE text
                self.annotations[str(page_idx)][anno_index]["text"] = new_text

            self.save_annotations()
            self.refresh_page_render(page_idx)

    def create_new_annotation(self, page_idx, rel_x, rel_y):
        text, ok = QInputDialog.getText(self, "Add Note", "Note content:")
        if ok and text:
            if str(page_idx) not in self.annotations:
                self.annotations[str(page_idx)] = []

            self.annotations[str(page_idx)].append(
                {"rel_pos": (rel_x, rel_y), "text": text}
            )

            self.save_annotations()
            self.refresh_page_render(page_idx)

    def save_annotations(self):
        with open(str(self.current_path) + ".riemann.json", "w") as f:
            json.dump(self.annotations, f)

    def refresh_page_render(self, page_idx):
        """Forces a repaint of a specific page to show/hide the yellow dot"""
        if page_idx in self.rendered_pages:
            self.rendered_pages.remove(page_idx)
        self.render_visible_pages()


class RiemannWindow(QMainWindow):
    """
    The Window Manager. Handles Tabs and Splitting.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Riemann Reader")
        self.resize(1200, 900)

        # FIX 3: Initialize Settings and State in the Window
        self.settings = QSettings("Riemann", "PDFReader")
        self.dark_mode = self.settings.value("darkMode", True, type=bool)

        # Central Splitter (allows Left/Right view)
        self.splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(self.splitter)

        # Left Tab Group (Always exists)
        self.tabs_main = QTabWidget()
        self.tabs_main.setTabsClosable(True)
        self.tabs_main.tabCloseRequested.connect(self.close_tab)
        self.splitter.addWidget(self.tabs_main)

        # Right Tab Group (Created on demand for Split View)
        self.tabs_side = QTabWidget()
        self.tabs_side.setTabsClosable(True)
        self.tabs_side.tabCloseRequested.connect(self.close_side_tab)
        self.tabs_side.hide()  # Hidden by default
        self.splitter.addWidget(self.tabs_side)

        # Global Menu
        self.setup_menu()

        last_file = self.settings.value("lastFile", type=str)

        if last_file and os.path.exists(last_file):
            # Open and restore position
            self.new_tab(last_file, restore_state=True)
        elif last_file and not os.path.exists(last_file):
            # Report Error
            sys.stderr.write("Last opened file no longer exists!\n")
            self.new_tab()
        else:
            # Fallback to empty tab
            self.new_tab()

    def setup_menu(self):
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("File")

        open_action = file_menu.addAction("Open PDF")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_pdf_dialog)

        new_tab_action = file_menu.addAction("New Tab")
        new_tab_action.setShortcut("Ctrl+T")
        new_tab_action.triggered.connect(self.new_tab)

        # View Menu
        view_menu = menubar.addMenu("View")

        split_action = view_menu.addAction("Split Editor Right")
        split_action.setShortcut("Ctrl+\\")
        split_action.triggered.connect(self.toggle_split_view)

    def new_tab(self, path=None, restore_state=False):
        """Creates a new ReaderTab in the currently active group."""
        reader = ReaderTab()

        if path:
            reader.load_document(path, restore_state=restore_state)
            title = os.path.basename(path)
        else:
            title = "New Tab"

        # Add to whichever side is active or default to main
        self.tabs_main.addTab(reader, title)
        self.tabs_main.setCurrentWidget(reader)

    def open_pdf_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            # If current tab is empty, load there. Otherwise, new tab.
            current = self.tabs_main.currentWidget()
            if isinstance(current, ReaderTab) and current.current_path is None:
                current.load_document(path)
                self.tabs_main.setTabText(
                    self.tabs_main.currentIndex(), os.path.basename(path)
                )
            else:
                self.new_tab(path)

    def toggle_split_view(self):
        """Moves the current tab to the right side splitter."""
        if self.tabs_side.isHidden():
            self.tabs_side.show()

        # Move current widget from Main -> Side
        current = self.tabs_main.currentWidget()
        if current:
            idx = self.tabs_main.indexOf(current)
            text = self.tabs_main.tabText(idx)

            # Remove from main, add to side
            self.tabs_main.removeTab(idx)
            self.tabs_side.addTab(current, text)
            self.tabs_side.setCurrentWidget(current)

    def close_tab(self, index):
        widget = self.tabs_main.widget(index)
        if widget:
            widget.deleteLater()
        self.tabs_main.removeTab(index)

    def close_side_tab(self, index):
        widget = self.tabs_side.widget(index)
        if widget:
            widget.deleteLater()
        self.tabs_side.removeTab(index)
        if self.tabs_side.count() == 0:
            self.tabs_side.hide()

    def toggle_reader_fullscreen(self):
        """
        Global Fullscreen: Hides OS Chrome, Window Menus, Tab Bars, and Tab Toolbars
        """
        if not getattr(self, "_reader_fullscreen", False):
            self._reader_fullscreen = True
            self._was_maximized = self.isMaximized()

            # Hide Window Controls
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            self.tabs_side.tabBar().hide()

            # Hide Toolbars in all active tabs
            self._set_tabs_toolbar_visible(False)

            self.showFullScreen()
        else:
            self._reader_fullscreen = False

            # Restore Controls
            self.menuBar().show()
            self.tabs_main.tabBar().show()
            self.tabs_side.tabBar().show()

            # Restore Toolbars
            self._set_tabs_toolbar_visible(True)

            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()

    def _set_tabs_toolbar_visible(self, visible):
        """Helper to toggle toolbars in all tabs"""
        for i in range(self.tabs_main.count()):
            w = self.tabs_main.widget(i)
            if isinstance(w, ReaderTab):
                w.toolbar.setVisible(visible)
        for i in range(self.tabs_side.count()):
            w = self.tabs_side.widget(i)
            if isinstance(w, ReaderTab):
                w.toolbar.setVisible(visible)

    def toggle_theme(self):
        """Global Theme Toggle"""
        self.dark_mode = not self.dark_mode
        self.settings.setValue("darkMode", self.dark_mode)

        # Helper function to update a tab without triggering recursion
        def update_tab(tab):
            if isinstance(tab, ReaderTab):
                tab.dark_mode = self.dark_mode
                tab.apply_theme()  # Update colors
                tab.rendered_pages.clear()  # Clear image cache
                tab.update_view()  # Re-render

        # Apply to all tabs in main group
        for i in range(self.tabs_main.count()):
            update_tab(self.tabs_main.widget(i))

        # Apply to all tabs in side group
        for i in range(self.tabs_side.count()):
            update_tab(self.tabs_side.widget(i))

    def keyPressEvent(self, event):
        """Handle global keys for the window"""
        if event.key() == Qt.Key.Key_Escape:
            if self._reader_fullscreen:
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
