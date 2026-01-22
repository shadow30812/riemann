import json
import os
import sys
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QPixmap
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
    QStackedWidget,
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


class RiemannWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Riemann")
        self.resize(1200, 900)

        # Persistent Settings
        self.settings = QSettings("Riemann", "PDFReader")

        # State
        self.engine = None
        self.current_doc = None
        self.current_path = None
        self.current_page_index = 0
        self.dark_mode = self.settings.value("darkMode", True, type=bool)
        self.continuous_scroll = self.settings.value(
            "continuousScroll", True, type=bool
        )
        self.view_mode = ViewMode.IMAGE

        self.zoom_mode = ZoomMode.FIT_WIDTH
        self.manual_scale = 1.5

        # Annotations
        self.is_annotating = False
        self.annotations = {}

        self._init_backend()
        self.setup_ui()
        self.apply_theme()

        # Restore last file
        last_file = self.settings.value("lastFile")
        if last_file and os.path.exists(last_file):
            self.load_document(last_file, restore_page=True)

        QApplication.instance().installEventFilter(self)

    def _init_backend(self):
        try:
            self.engine = riemann_core.PdfEngine()
        except Exception as e:
            print(f"Backend init error: {e}")

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(50)
        t_layout = QHBoxLayout(self.toolbar)

        self.btn_open = QPushButton("Open")
        self.btn_open.clicked.connect(self.open_pdf_dialog)

        self.btn_reflow = QPushButton("📄/📝")
        self.btn_reflow.setToolTip("Toggle Reflow Mode")
        self.btn_reflow.clicked.connect(self.toggle_view_mode)

        self.btn_annotate = QPushButton("🖊️")
        self.btn_annotate.setToolTip("Annotate")
        self.btn_annotate.setCheckable(True)
        self.btn_annotate.clicked.connect(self.toggle_annotation_mode)

        # Scroll Mode Toggle
        self.btn_scroll_mode = QPushButton("📜" if self.continuous_scroll else "📄")
        self.btn_scroll_mode.setToolTip("Toggle Continuous Scroll / Single Page")
        self.btn_scroll_mode.clicked.connect(self.toggle_scroll_mode)

        self.btn_prev = QPushButton("◄")
        self.btn_prev.clicked.connect(self.prev_page)
        self.lbl_page = QLabel("0 / 0")
        self.btn_next = QPushButton("►")
        self.btn_next.clicked.connect(self.next_page)

        # Enhanced Zoom Controls
        self.combo_zoom = QComboBox()
        self.combo_zoom.setEditable(True)
        self.combo_zoom.addItems(
            [
                "Fit Width",
                "Fit Height",
                "25%",
                "50%",
                "75%",
                "100%",
                "125%",
                "150%",
                "200%",
                "300%",
                "400%",
            ]
        )
        # Trigger on Enter press or selection change
        self.combo_zoom.currentIndexChanged.connect(self.on_zoom_selected)
        self.combo_zoom.lineEdit().returnPressed.connect(self.on_zoom_text_entered)
        self.combo_zoom.setFixedWidth(100)

        self.btn_theme = QPushButton("🌓")
        self.btn_theme.clicked.connect(self.toggle_theme)

        for w in [
            self.btn_open,
            self.btn_reflow,
            self.btn_annotate,
            self.btn_scroll_mode,
            self.btn_prev,
            self.lbl_page,
            self.btn_next,
            self.combo_zoom,
            self.btn_theme,
        ]:
            t_layout.addWidget(w)
        t_layout.addStretch()
        layout.addWidget(self.toolbar)

        # Stack
        self.stack = QStackedWidget()
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)  # Center pages
        self.page_label = QLabel()
        self.page_label.mousePressEvent = self.on_page_clicked
        self.page_label.setScaledContents(False)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.page_label)
        self.scroll.setWidgetResizable(True)
        self.stack.addWidget(self.scroll)

        self.web = QWebEngineView()
        self.stack.addWidget(self.web)
        layout.addWidget(self.stack)

    # --- Navigation Logic ---

    def eventFilter(self, source, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()

            # Global F11
            if key == Qt.Key.Key_F11:
                self.toggle_fullscreen()
                return True

            if self.view_mode == ViewMode.IMAGE:
                mod = event.modifiers()
                if mod == Qt.KeyboardModifier.NoModifier:
                    # Horizontal (Restricted)
                    if key in (Qt.Key.Key_Left, Qt.Key.Key_A):
                        self.handle_horizontal_scroll(-1)
                        return True
                    elif key in (Qt.Key.Key_Right, Qt.Key.Key_D):
                        self.handle_horizontal_scroll(1)
                        return True

                    # Vertical (Mode dependent)
                    elif key in (Qt.Key.Key_Up, Qt.Key.Key_W):
                        self.handle_vertical_scroll(-1)
                        return True
                    elif key in (Qt.Key.Key_Down, Qt.Key.Key_S):
                        self.handle_vertical_scroll(1)
                        return True

        return super().eventFilter(source, event)

    def handle_horizontal_scroll(self, direction):
        """
        direction: -1 (Left), 1 (Right)
        """
        bar = self.scroll.horizontalScrollBar()

        # If content fits in viewport (zoomed out), arrows act as Page Turns
        if bar.maximum() <= 0:
            if direction == 1:
                self.next_page()
            else:
                self.prev_page()
            return

        # If zoomed in (content > viewport), arrows SCROLL ONLY.
        # We do NOT change page at the edge.
        step = 40
        new_val = bar.value() + (direction * step)
        new_val = max(0, min(new_val, bar.maximum()))
        bar.setValue(new_val)

    def handle_vertical_scroll(self, direction):
        """
        direction: -1 (Up), 1 (Down)
        """
        bar = self.scroll.verticalScrollBar()
        step = 40
        new_val = bar.value() + (direction * step)

        if direction == -1 and new_val < 0:
            # Reached Top
            if self.continuous_scroll:
                self.prev_page(scroll_pos="bottom")
            else:
                bar.setValue(0)  # Stop at top

        elif direction == 1 and new_val > bar.maximum():
            # Reached Bottom
            if self.continuous_scroll:
                self.next_page()
            else:
                bar.setValue(bar.maximum())  # Stop at bottom
        else:
            bar.setValue(new_val)

    # --- Zoom & Logic ---

    def on_zoom_selected(self, idx):
        self.apply_zoom_string(self.combo_zoom.currentText())

    def on_zoom_text_entered(self):
        self.apply_zoom_string(self.combo_zoom.lineEdit().text())
        # Clear focus from combo box so arrow keys work again immediately
        self.scroll.setFocus()

    def apply_zoom_string(self, text):
        if "Fit Width" in text:
            self.zoom_mode = ZoomMode.FIT_WIDTH
        elif "Fit Height" in text:
            self.zoom_mode = ZoomMode.FIT_HEIGHT
        else:
            try:
                # Handle "100%", "100", "1.5", etc.
                val = text.lower().replace("%", "").strip()
                scale = float(val)
                # If user types "100", they mean 100%, so 1.0. If "1.5", they mean 150%.
                # Heuristic: if > 5, assume percentage.
                if scale > 5.0:
                    scale /= 100.0

                self.manual_scale = scale
                self.zoom_mode = ZoomMode.MANUAL
            except ValueError:
                pass
        self.render_current_page()

    def toggle_scroll_mode(self):
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScroll", self.continuous_scroll)
        self.btn_scroll_mode.setText("📜" if self.continuous_scroll else "📄")

    def toggle_annotation_mode(self, checked):
        self.is_annotating = checked
        self.btn_annotate.setStyleSheet("background-color: #4a90e2;" if checked else "")

    def on_page_clicked(self, event):
        if not self.is_annotating or not self.current_doc:
            return

        pos = event.pos()
        text, ok = QInputDialog.getText(self, "Add Note", "Note content:")
        if ok and text:
            page_annos = self.annotations.get(str(self.current_page_index), [])
            rel_x = pos.x() / self.page_label.width()
            rel_y = pos.y() / self.page_label.height()
            page_annos.append({"rel_pos": (rel_x, rel_y), "text": text})
            self.annotations[str(self.current_page_index)] = page_annos
            self.save_annotations()
            self.render_current_page()

    def save_annotations(self):
        if not self.current_path:
            return
        anno_path = str(self.current_path) + ".riemann.json"
        with open(anno_path, "w") as f:
            json.dump(self.annotations, f)

    def load_annotations(self):
        anno_path = str(self.current_path) + ".riemann.json"
        if os.path.exists(anno_path):
            with open(anno_path, "r") as f:
                self.annotations = json.load(f)
        else:
            self.annotations = {}

    def toggle_view_mode(self):
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        self.stack.setCurrentIndex(0 if self.view_mode == ViewMode.IMAGE else 1)
        self.render_current_page()

    def render_current_page(self, scroll_pos="top"):
        if not self.current_doc:
            return
        self.lbl_page.setText(
            f"{self.current_page_index + 1} / {self.current_doc.page_count}"
        )
        self.settings.setValue("lastPage", self.current_page_index)

        if self.view_mode == ViewMode.IMAGE:
            self.render_image()
            bar = self.scroll.verticalScrollBar()
            if scroll_pos == "bottom":
                bar.setValue(bar.maximum())
            else:
                bar.setValue(0)
        else:
            self.render_reflow()

    def render_image(self):
        try:
            scale = self.calculate_scale()
            res = self.current_doc.render_page(
                self.current_page_index, scale, 1 if self.dark_mode else 0
            )
            img = QImage(res.data, res.width, res.height, QImage.Format.Format_ARGB32)
            pix = QPixmap.fromImage(img.copy())

            # Annotations
            painter = QPainter(pix)
            painter.setPen(QPen(QColor(255, 255, 0, 180), 3))
            page_annos = self.annotations.get(str(self.current_page_index), [])
            for anno in page_annos:
                x = int(anno["rel_pos"][0] * pix.width())
                y = int(anno["rel_pos"][1] * pix.height())
                painter.drawEllipse(QPoint(x, y), 10, 10)
            painter.end()

            self.page_label.setPixmap(pix)
            self.page_label.resize(pix.size())
        except Exception as e:
            print(f"Render error: {e}")

    def render_reflow(self):
        try:
            text = self.current_doc.get_page_text(self.current_page_index)
            bg = "#1e1e1e" if self.dark_mode else "#fff"
            fg = "#ddd" if self.dark_mode else "#222"
            html = f"<html><body style='background:{bg};color:{fg};font-family:sans-serif;padding:40px;line-height:1.6;'>{text.replace(chr(10), '<br>')}</body></html>"
            self.web.setHtml(html)
        except Exception:
            pass

    def calculate_scale(self):
        if self.zoom_mode == ZoomMode.MANUAL:
            return self.manual_scale
        try:
            res = self.current_doc.render_page(self.current_page_index, 1.0, 0)
            page_w = res.width
            page_h = res.height
        except:
            return 1.0

        view_w = self.scroll.viewport().width() - 20
        view_h = self.scroll.viewport().height() - 20

        if self.zoom_mode == ZoomMode.FIT_WIDTH:
            return view_w / page_w
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            return view_h / page_h
        return 1.0

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.settings.setValue("darkMode", self.dark_mode)
        self.apply_theme()
        self.render_current_page()

    def apply_theme(self):
        pal = self.palette()
        color = QColor(30, 30, 30) if self.dark_mode else QColor(240, 240, 240)
        pal.setColor(QPalette.ColorRole.Window, color)
        self.setPalette(pal)
        self.toolbar.setStyleSheet(
            f"background: {'#252525' if self.dark_mode else '#eee'}; border-bottom: 1px solid #444;"
        )

    def open_pdf_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)

    def load_document(self, path, restore_page=False):
        try:
            self.current_doc = self.engine.load_document(path)
            self.current_path = path
            self.settings.setValue("lastFile", path)
            self.load_annotations()
            if restore_page:
                self.current_page_index = self.settings.value("lastPage", 0, type=int)
            else:
                self.current_page_index = 0
            self.render_current_page()
        except Exception as e:
            print(f"Load error: {e}")

    def next_page(self):
        if (
            self.current_doc
            and self.current_page_index < self.current_doc.page_count - 1
        ):
            self.current_page_index += 1
            self.render_current_page(scroll_pos="top")

    def prev_page(self, scroll_pos="top"):
        if self.current_doc and self.current_page_index > 0:
            self.current_page_index -= 1
            self.render_current_page(scroll_pos=scroll_pos)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()


def run():
    app = QApplication(sys.argv)
    window = RiemannWindow()
    window.show()
    sys.exit(app.exec())
