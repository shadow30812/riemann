"""
Reader Tab Component.

The main aggregator class that combines all mixins to provide
the full PDF reading experience.
"""

import gc
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from math import inf
from typing import Any, Dict, List, Optional, Set, Tuple

import pikepdf
import shiboken6
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QPixmapCache,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.constants import ViewMode, ZoomMode
from ...core.managers import PasswordDialog
from ..components import AnnotationToolbar
from .mixins.ai import AiMixin
from .mixins.annotations import AnnotationsMixin
from .mixins.metadata import MetadataMixin
from .mixins.rendering import RenderingMixin
from .mixins.search import SearchMixin
from .mixins.signatures import SignaturesMixin
from .utils import generate_markdown_html
from .widgets import DropZoneLabel, PageWidget

try:
    import riemann_core
except ImportError as e:
    print(f"CRITICAL: Could not import riemann_core backend.\nError: {e}")
    sys.exit(1)


def get_dialog_directory(settings: QSettings) -> str:
    """Calculates the optimal starting directory for file dialogs."""
    default_dir = settings.value("app/default_dir", "", type=str)
    if default_dir and os.path.exists(default_dir):
        return default_dir

    last_dir = settings.value("app/last_dir", "", type=str)
    if last_dir and os.path.exists(last_dir):
        return last_dir

    return QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )


def save_last_directory(settings: QSettings, file_path: str) -> None:
    """Saves the directory of the provided file path to settings."""
    if file_path:
        directory = (
            os.path.dirname(file_path) if os.path.isfile(file_path) else file_path
        )
        if os.path.exists(directory):
            settings.setValue("app/last_dir", directory)


class DocumentLoadWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, engine, path, password):
        super().__init__()
        self.engine = engine
        self.path = path
        self.password = password

    def run(self):
        try:
            doc = self.engine.load_document(self.path, self.password)
            self.finished.emit(doc)
        except Exception as e:
            self.error.emit(str(e))


class ReaderTab(
    QWidget,
    RenderingMixin,
    AnnotationsMixin,
    AiMixin,
    SearchMixin,
    SignaturesMixin,
    MetadataMixin,
):
    """
    A self-contained PDF Viewer Widget acting as the central interactive component.

    Inherits structural rendering, interactive logic, background searching, and
    metadata management functionality dynamically through specialized mixins.
    """

    signatures_detected = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """
        Initializes the ReaderTab, constructing UI elements, and loading stored settings.

        Args:
            parent (Optional[QWidget]): The parent layout containment widget. Defaults to None.
        """
        super().__init__(parent)

        self.settings: QSettings = QSettings("Riemann", "PDFReader")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

        self.engine: Optional[riemann_core.PdfEngine] = None
        self.current_doc: Optional[riemann_core.RiemannDocument] = None
        self.current_path: Optional[str] = None
        self.current_page_index: int = 0

        self.theme_mode: int = 0  # Always default to Light Mode (0) on document open
        self.zoom_mode: ZoomMode = ZoomMode.FIT_WIDTH
        self.manual_scale: float = 1.0
        self.facing_mode: bool = False
        self.continuous_scroll: bool = True
        self.view_mode: ViewMode = ViewMode.IMAGE
        self.is_annotating: bool = False

        self.current_tool: str = "nav"
        self.pen_color: str = "#ff0000"
        self.pen_thickness: int = 3
        self.active_drawing: List[QPoint] = []
        self.annotations: Dict[str, List[Dict[str, Any]]] = {}
        self.undo_stack: List[Tuple[str, int, int]] = []
        self.redo_stack: List[Tuple[str, Dict]] = []

        self.is_snipping: bool = False
        self.snip_start: QPoint = QPoint()
        self.snip_band: Optional[QRubberBand] = None
        self._pending_snip_image = None
        self.latex_model = None

        self.form_widgets: Dict[int, List[QWidget]] = {}
        self.form_values_cache: Dict[Tuple[int, Tuple[float, ...]], Any] = {}
        self.page_widgets: Dict[int, PageWidget] = {}
        self.rendered_pages: Set[int] = set()
        self.search_result: Optional[Tuple[int, List[Tuple[float, ...]]]] = None
        self.text_segments_cache: Dict[int, List[Tuple[str, Tuple[float, ...]]]] = {}

        self.virtual_threshold: int = 15
        self._virtual_enabled: bool = False
        self._top_spacer: Optional[QWidget] = None
        self._bottom_spacer: Optional[QWidget] = None
        self._virtual_range: Tuple[int, int] = (0, 0)
        self._cached_base_size: Optional[Tuple[int, int]] = None

        self._autoscroll_active: bool = False
        self._autoscroll_origin_global: Optional[QPoint] = None
        self._autoscroll_speed_y: float = 0.0
        self._autoscroll_speed_x: float = 0.0

        self._click_count: int = 0
        self._last_click_time: float = 0.0
        self._last_click_pos: QPoint = QPoint()
        self._just_selected_multi_click: bool = False

        self._init_backend()
        self.setup_ui()
        self.apply_theme()
        self._setup_scroller()

        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.setInterval(150)
        self.scroll_timer.timeout.connect(self.real_scroll_handler)

        self.autoscroll_timer = QTimer(self)
        self.autoscroll_timer.setInterval(30)
        self.autoscroll_timer.timeout.connect(self._do_autoscroll)

        self.external_comments: Dict[int, List[Dict[str, Any]]] = {}

        # Floating page indicator popup for pure fullscreen mode
        self.page_indicator_popup = QLabel(self)
        self.page_indicator_popup.setStyleSheet(
            "background-color: rgba(25, 25, 25, 215);"
            "color: #ffffff;"
            "font-size: 13px;"
            "font-weight: bold;"
            "border-radius: 6px;"
            "padding: 5px 12px;"
            "border: 1px solid rgba(255, 255, 255, 45);"
        )
        self.page_indicator_popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.page_indicator_opacity = QGraphicsOpacityEffect(self.page_indicator_popup)
        self.page_indicator_popup.setGraphicsEffect(self.page_indicator_opacity)
        self.page_indicator_opacity.setOpacity(0.0)
        self.page_indicator_popup.hide()

        self._page_indicator_fade_anim = QPropertyAnimation(
            self.page_indicator_opacity, b"opacity"
        )
        self._page_indicator_fade_anim.setDuration(800)
        self._page_indicator_fade_anim.setStartValue(1.0)
        self._page_indicator_fade_anim.setEndValue(0.0)
        self._page_indicator_fade_anim.finished.connect(
            self._on_page_indicator_fade_finished
        )

        self._page_indicator_timer = QTimer(self)
        self._page_indicator_timer.setSingleShot(True)
        self._page_indicator_timer.setInterval(1200)
        self._page_indicator_timer.timeout.connect(self._start_page_indicator_fade)

        self._init_shortcuts()

    def _init_shortcuts(self) -> None:
        """
        Registers widget-specific keyboard shortcuts mapped to primary application functionality.
        """
        shortcuts = [
            ("Ctrl+F", self.toggle_search_bar),
            ("Ctrl+I", self.toggle_ai_search_bar),
            ("Ctrl+P", self.print_document),
            ("Ctrl+A", self.select_all_text),
            ("Ctrl+Shift+A", self.btn_annotate.click),
            ("Ctrl+Z", self.undo_annotation),
            ("Ctrl+Shift+Z", self.redo_annotation),
            ("Ctrl+R", self.rotate_document),
            ("Ctrl+Shift+R", self.rotate_document_ccw),
            ("Ctrl+Shift+S", self.export_secure_pdf),
            ("Page Down", lambda: self.scroll_page_length(1)),
            ("Page Up", lambda: self.scroll_page_length(-1)),
            ("F5", self.reload_document),
        ]
        for seq, slot in shortcuts:
            QShortcut(QKeySequence(seq), self).activated.connect(slot)

        sc1 = QShortcut(QKeySequence("Ctrl+Tab"), self)
        sc1.setContext(Qt.ShortcutContext.WindowShortcut)
        sc1.activated.connect(lambda: self.cycle_tab(1))

        sc2 = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        sc2.setContext(Qt.ShortcutContext.WindowShortcut)
        sc2.activated.connect(lambda: self.cycle_tab(-1))

    def _get_tab_widget(self) -> Optional[QTabWidget]:
        """
        Searches the component hierarchy iteratively evaluating structural relationships to retrieve the parent tab container.

        Returns:
            Optional[QTabWidget]: The parent QTabWidget if resolvable, None otherwise.
        """
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                return parent
            parent = parent.parent()
        return None

    def cycle_tab(self, delta: int) -> None:
        """
        Adjusts logical focus forwarding execution states iteratively mapping onto relative adjacent tab windows.

        Args:
            delta (int): The integer movement steps (-1 indicates backwards cycle).
        """
        tw = self._get_tab_widget()
        if tw:
            count = tw.count()
            next_idx = (tw.currentIndex() + delta) % count
            tw.setCurrentIndex(next_idx)

    def _update_tab_title(self, title: str) -> None:
        """
        Mutates structural name mappings propagating string modifications directly targeting the parent visual tab header.

        Args:
            title (str): Output string definition utilized rendering visible identifiers.
        """
        tw = self._get_tab_widget()
        if tw:
            idx = tw.indexOf(self)
            if idx != -1:
                display_title = (title[:25] + "..") if len(title) > 25 else title
                tw.setTabText(idx, display_title)
                if hasattr(self.window(), "_update_tab_tooltip"):
                    self.window()._update_tab_tooltip(tw, idx)
                if hasattr(self.window(), "_update_window_title"):
                    self.window()._update_window_title()

    def _init_backend(self) -> None:
        """
        Instantiates underlying native Rust extensions managing hardware accelerated layout algorithms gracefully.
        """
        try:
            self.engine = riemann_core.PdfEngine()
        except Exception as e:
            sys.stderr.write(f"Backend Initialization Error: {e}\n")

    def setup_ui(self) -> None:
        """
        Builds the widget hierarchy and assembles nested structural elements systematically applying alignments.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(50)
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.toolbar.installEventFilter(self)

        self.toolbar_anim = QPropertyAnimation(self.toolbar, b"maximumHeight")
        self.toolbar_anim.setDuration(200)
        self.toolbar_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        self.hover_trigger = QWidget(self)
        self.hover_trigger.setFixedHeight(15)
        self.hover_trigger.setStyleSheet("background: transparent;")
        self.hover_trigger.installEventFilter(self)
        self.hover_trigger.hide()

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

        self.signature_banner = QWidget()
        self.signature_banner.setVisible(False)
        self.anno_toolbar.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.signature_banner.setFixedHeight(40)
        banner_layout = QHBoxLayout(self.signature_banner)

        self.lbl_sig_status = QLabel("Signature Status")
        self.btn_view_cert = QPushButton("View Certificate")
        self.btn_view_cert.clicked.connect(self.view_certificate)
        self.btn_trust_cert = QPushButton("Trust Certificate")
        self.btn_trust_cert.clicked.connect(self.trust_current_certificate)
        self.btn_close_banner = QPushButton("✕")
        self.btn_close_banner.setFlat(True)
        self.btn_close_banner.clicked.connect(
            lambda: self.signature_banner.setVisible(False)
        )

        banner_layout.addWidget(self.lbl_sig_status)
        banner_layout.addStretch()
        banner_layout.addWidget(self.btn_view_cert)
        banner_layout.addWidget(self.btn_trust_cert)
        banner_layout.addWidget(self.btn_close_banner)
        layout.addWidget(self.signature_banner)

        self._setup_search_bar()
        layout.addWidget(self.search_bar)

        self._setup_ai_search_bar()
        layout.addWidget(self.ai_search_bar)

        self.stack = QStackedWidget()
        self._setup_scroll_area()

        self.scroll_content = QWidget()
        self.stack.addWidget(self.scroll)

        self._web_placeholder = QWidget()
        self.stack.addWidget(self._web_placeholder)

        self._setup_home_page()
        self.stack.addWidget(self.home_page_widget)
        layout.addWidget(self.stack)

        if not getattr(self, "current_path", None):
            self.stack.setCurrentIndex(2)
            self.toolbar.hide()

        for widget_class in (QPushButton, QToolButton, QComboBox):
            for w in self.findChildren(widget_class):
                w.setCursor(Qt.CursorShape.PointingHandCursor)

        self.link_tooltip = QLabel(self)
        self.link_tooltip.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; padding: 3px 7px; "
            "border: 1px solid #444; border-radius: 3px; font-size: 11px;"
        )
        self.link_tooltip.hide()

    def _setup_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        """
        Allocates interactive push buttons resolving respective execution slot relationships natively.

        Args:
            layout (QHBoxLayout): Reference pointer tracking parent bounding alignments systematically.
        """
        icon_size = QSize(20, 20)

        self.btn_save = QPushButton()
        self.btn_save.setIcon(self._get_icon("save.svg"))
        self.btn_save.setIconSize(icon_size)
        self.btn_save.setToolTip("Save Copy of PDF")
        self.btn_save.clicked.connect(self.save_document)

        self.btn_rename = QPushButton()
        self.btn_rename.setIcon(self._get_icon("rename.svg"))
        self.btn_rename.setIconSize(icon_size)
        self.btn_rename.setToolTip("Auto-Rename File using Metadata")
        self.btn_rename.clicked.connect(self.rename_current_pdf)

        self.btn_export = QPushButton()
        self.btn_export.setIcon(self._get_icon("file-output.svg"))
        self.btn_export.setIconSize(icon_size)
        self.btn_export.setToolTip("Export Annotations to Markdown")
        self.btn_export.clicked.connect(self.export_annotations)

        self.btn_open_external = QPushButton()
        self.btn_open_external.setIcon(self._get_icon("airplay.svg"))
        self.btn_open_external.setIconSize(icon_size)
        self.btn_open_external.setToolTip(
            "Open in External Application (System Viewer, Chrome, Firefox...)"
        )
        self.btn_open_external.clicked.connect(self.show_open_with_menu)

        self.btn_print = QPushButton()
        self.btn_print.setIcon(self._get_icon("printer.svg"))
        self.btn_print.setIconSize(icon_size)
        self.btn_print.setToolTip("Print Document (Ctrl+P)")
        self.btn_print.clicked.connect(self.print_document)

        self.btn_cite = QPushButton()
        self.btn_cite.setIcon(self._get_icon("text-quote.svg"))
        self.btn_cite.setIconSize(icon_size)
        self.btn_cite.setToolTip("Copy BibTeX Citation")
        self.btn_cite.clicked.connect(self.copy_citation)

        self.btn_rotate = QPushButton()
        self.btn_rotate.setIcon(self._get_icon("rotate-cw.svg"))
        self.btn_rotate.setIconSize(icon_size)
        self.btn_rotate.setToolTip("Rotate PDF 90°")
        self.btn_rotate.clicked.connect(self.rotate_document)

        self.btn_rotate_ccw = QPushButton()
        self.btn_rotate_ccw.setIcon(self._get_icon("rotate-ccw.svg"))
        self.btn_rotate_ccw.setIconSize(icon_size)
        self.btn_rotate_ccw.setToolTip("Rotate PDF -90°")
        self.btn_rotate_ccw.clicked.connect(self.rotate_document_ccw)

        self.btn_reflow = QPushButton()
        self.btn_reflow.setIcon(self._get_icon("file-text.svg"))
        self.btn_reflow.setIconSize(icon_size)
        self.btn_reflow.setToolTip("Toggle Text Reflow Mode")
        self.btn_reflow.setCheckable(True)
        self.btn_reflow.clicked.connect(self.toggle_view_mode)

        self.btn_facing = QPushButton()
        self.btn_facing.setIcon(self._get_icon("book-open.svg"))
        self.btn_facing.setIconSize(icon_size)
        self.btn_facing.setToolTip("Toggle Facing Pages")
        self.btn_facing.setCheckable(True)
        self.btn_facing.clicked.connect(self.toggle_facing_mode)

        self.btn_scroll_mode = QPushButton()
        self.btn_scroll_mode.setIcon(self._get_icon("scroll.svg"))
        self.btn_scroll_mode.setIconSize(icon_size)
        self.btn_scroll_mode.setToolTip("Toggle Scroll Mode")
        self.btn_scroll_mode.setCheckable(True)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.btn_scroll_mode.clicked.connect(self.toggle_scroll_mode)

        self.btn_autoscroll_up = QPushButton()
        self.btn_autoscroll_up.setIcon(self._get_icon("move-up.svg"))
        self.btn_autoscroll_up.setIconSize(icon_size)
        self.btn_autoscroll_up.setToolTip("Auto-Scroll Up")
        self.btn_autoscroll_up.setCheckable(True)
        self.btn_autoscroll_up.clicked.connect(lambda: self.toggle_autoscroll(-1))

        self.btn_autoscroll_down = QPushButton()
        self.btn_autoscroll_down.setIcon(self._get_icon("move-down.svg"))
        self.btn_autoscroll_down.setIconSize(icon_size)
        self.btn_autoscroll_down.setToolTip("Auto-Scroll Down")
        self.btn_autoscroll_down.setCheckable(True)
        self.btn_autoscroll_down.clicked.connect(lambda: self.toggle_autoscroll(1))

        self.btn_annotate = QPushButton()
        self.btn_annotate.setIcon(self._get_icon("pen-line.svg"))
        self.btn_annotate.setIconSize(icon_size)
        self.btn_annotate.setToolTip("Show Annotation Tools")
        self.btn_annotate.setCheckable(True)
        self.btn_annotate.clicked.connect(self.toggle_annotation_mode)

        self.btn_snip = QPushButton()
        self.btn_snip.setIcon(self._get_icon("crop.svg"))
        self.btn_snip.setIconSize(icon_size)
        self.btn_snip.setToolTip("Snip Math to LaTeX")
        self.btn_snip.setCheckable(True)
        self.btn_snip.clicked.connect(self.toggle_snip_mode)

        self.btn_prev = QPushButton()
        self.btn_prev.setIcon(self._get_icon("chevron-left.svg"))
        self.btn_prev.setIconSize(icon_size)
        self.btn_prev.clicked.connect(self.prev_view)

        self.txt_page = QLineEdit()
        self.txt_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.txt_page.returnPressed.connect(self.on_page_input_return)

        self.txt_page.textChanged.connect(self._update_page_input_width)
        self._update_page_input_width(self.txt_page.text())

        self.lbl_total = QLabel("/ 0")

        self.btn_next = QPushButton()
        self.btn_next.setIcon(self._get_icon("chevron-right.svg"))
        self.btn_next.setIconSize(icon_size)
        self.btn_next.clicked.connect(self.next_view)

        self.btn_zoom_out = QPushButton()
        self.btn_zoom_out.setIcon(self._get_icon("minus.svg"))
        self.btn_zoom_out.setIconSize(icon_size)
        self.btn_zoom_out.setToolTip("Zoom Out")
        self.btn_zoom_out.clicked.connect(lambda: self.zoom_step(0.9))

        self.combo_zoom = QComboBox()
        self.combo_zoom.setEditable(True)
        self.combo_zoom.addItems(
            [
                "Auto Fit",
                "Fit Width",
                "Fit Height",
                "50%",
                "75%",
                "100%",
                "125%",
                "150%",
                "200%",
            ]
        )
        self.combo_zoom.currentIndexChanged.connect(self.on_zoom_selected)
        self.combo_zoom.lineEdit().returnPressed.connect(self.on_zoom_text_entered)

        self.btn_zoom_in = QPushButton()
        self.btn_zoom_in.setIcon(self._get_icon("plus.svg"))
        self.btn_zoom_in.setIconSize(icon_size)
        self.btn_zoom_in.setToolTip("Zoom In")
        self.btn_zoom_in.clicked.connect(lambda: self.zoom_step(1.1))

        self.btn_theme = QPushButton()
        self.btn_theme.setIcon(
            self._get_icon(
                "moon.svg" if getattr(self, "theme_mode", 0) == 0 else "sun.svg"
            )
        )
        self.btn_theme.setIconSize(icon_size)
        self.btn_theme.setToolTip("Cycle Theme (Light / Fast Dark / Smart Dark)")
        self.btn_theme.clicked.connect(self.toggle_theme)

        self.btn_fullscreen = QPushButton()
        self.btn_fullscreen.setIcon(self._get_icon("maximize.svg"))
        self.btn_fullscreen.setIconSize(icon_size)
        self.btn_fullscreen.clicked.connect(self.toggle_reader_fullscreen)

        self.btn_ocr = QPushButton()
        self.btn_ocr.setIcon(self._get_icon("scan-text.svg"))
        self.btn_ocr.setIconSize(icon_size)
        self.btn_ocr.setToolTip("OCR Current Page")
        self.btn_ocr.clicked.connect(self.perform_ocr_current_page)

        self.btn_search = QPushButton()
        self.btn_search.setIcon(self._get_icon("search.svg"))
        self.btn_search.setIconSize(icon_size)
        self.btn_search.setCheckable(True)
        self.btn_search.clicked.connect(self.toggle_search_bar)

        self.btn_ai_search = QPushButton()
        self.btn_ai_search.setIcon(self._get_icon("sparkles.svg"))
        self.btn_ai_search.setIconSize(icon_size)
        self.btn_ai_search.setToolTip("AI Semantic Search (Ctrl+I)")
        self.btn_ai_search.setCheckable(True)
        self.btn_ai_search.setStyleSheet(
            "color: #9b59b6; font-weight: bold; font-size: 16px;"
        )
        self.btn_ai_search.clicked.connect(self.toggle_ai_search_bar)

        self.btn_secure_export = QPushButton()
        self.btn_secure_export.setIcon(self._get_icon("file-lock.svg"))
        self.btn_secure_export.setIconSize(icon_size)
        self.btn_secure_export.setText(" Lock | Save")
        self.btn_secure_export.setToolTip(
            "Export a password-protected copy of this PDF (Ctrl+Shift+S)"
        )

        self.btn_secure_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_secure_export.setStyleSheet("""
            QPushButton {
                padding: 6px 14px;
                border-radius: 4px;
                background-color: #2C2C30;
                color: #E0E0E0;
                border: 1px solid #3F3F46;
            }
            QPushButton:hover {
                background-color: #3F3F46;
                border: 1px solid #52525B;
            }
        """)
        self.btn_secure_export.clicked.connect(self.export_secure_pdf)

        self.btn_sign = QPushButton()
        self.btn_sign.setIcon(self._get_icon("signature.svg"))
        self.btn_sign.setIconSize(icon_size)
        self.btn_sign.setToolTip("Sign Document (PKCS#12)")
        self.btn_sign.clicked.connect(self.initiate_signing_flow)

        widgets = [
            self.btn_save,
            self.btn_print,
            self.btn_rename,
            self.btn_export,
            self.btn_open_external,
            self.btn_sign,
            self.btn_rotate,
            self.btn_rotate_ccw,
            self.btn_reflow,
            self.btn_facing,
            self.btn_scroll_mode,
            self.btn_autoscroll_up,
            self.btn_autoscroll_down,
            self.btn_search,
            self.btn_ai_search,
            self.btn_annotate,
            self.btn_cite,
            self.btn_snip,
            self.btn_ocr,
            self.btn_prev,
            self.txt_page,
            self.lbl_total,
            self.btn_next,
            self.btn_zoom_out,
            self.combo_zoom,
            self.btn_zoom_in,
            self.btn_theme,
            self.btn_fullscreen,
            self.btn_secure_export,
        ]
        for w in widgets:
            layout.addWidget(w)

    def _update_page_input_width(self, text: str = "") -> None:
        """Dynamically adjusts the width of the page number input box based on content length."""
        if not text:
            text = self.txt_page.text() or "0"

        fm = self.txt_page.fontMetrics()
        width = fm.horizontalAdvance(text) + 40
        self.txt_page.setFixedWidth(max(40, min(width, 80)))

    def _setup_search_bar(self) -> None:
        """
        Constructs lateral overlay text searching UI widgets embedding basic navigational controls reliably.
        """
        self.search_bar = QWidget()
        self.search_bar.setVisible(False)
        self.search_bar.setFixedHeight(45)

        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(10, 5, 10, 5)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Find text...")
        self.txt_search.returnPressed.connect(self.find_next)

        icon_size = QSize(18, 18)
        self.btn_find_prev = QPushButton()
        self.btn_find_prev.setIcon(self._get_icon("chevron-up.svg"))
        self.btn_find_prev.setIconSize(icon_size)
        self.btn_find_prev.clicked.connect(self.find_prev)

        self.btn_find_next = QPushButton()
        self.btn_find_next.setIcon(self._get_icon("chevron-down.svg"))
        self.btn_find_next.setIconSize(icon_size)
        self.btn_find_next.clicked.connect(self.find_next)

        self.btn_close_search = QPushButton()
        self.btn_close_search.setIcon(self._get_icon("x.svg"))
        self.btn_close_search.setIconSize(icon_size)
        self.btn_close_search.setFlat(True)
        self.btn_close_search.clicked.connect(self.toggle_search_bar)

        sb_layout.addWidget(QLabel("Find:"))
        sb_layout.addWidget(self.txt_search)
        sb_layout.addWidget(self.btn_find_prev)
        sb_layout.addWidget(self.btn_find_next)
        sb_layout.addWidget(self.btn_close_search)

    def _setup_ai_search_bar(self) -> None:
        """
        Organizes specialized AI interaction interface overlays incorporating custom thematic styling distinct from standard bars.
        """
        self.ai_search_bar = QWidget()
        self.ai_search_bar.setVisible(False)
        self.ai_search_bar.setFixedHeight(45)
        self.ai_search_bar.setStyleSheet("background-color: #2b1d3d; color: #e6d0ff;")

        sb_layout = QHBoxLayout(self.ai_search_bar)
        sb_layout.setContentsMargins(10, 5, 10, 5)

        self.txt_ai_search = QLineEdit()
        self.txt_ai_search.setPlaceholderText(
            "Ask AI to find concepts, meanings, or subjects..."
        )
        self.txt_ai_search.setStyleSheet(
            "background-color: #1a1025; border: 1px solid #7b4bce; "
            "border-radius: 4px; padding: 4px; color: white;"
        )
        self.txt_ai_search.returnPressed.connect(
            lambda: self.ai_search(self.txt_ai_search.text())
        )

        self.btn_ai_find = QPushButton("Ask AI")
        self.btn_ai_find.setIcon(self._get_icon("sparkles.svg"))
        self.btn_ai_find.setStyleSheet(
            "background-color: #7b4bce; color: white; border-radius: 4px; padding: 4px 10px;"
        )
        self.btn_ai_find.clicked.connect(
            lambda: self.ai_search(self.txt_ai_search.text())
        )

        icon_size = QSize(18, 18)
        self.btn_ai_prev = QPushButton()
        self.btn_ai_prev.setIcon(self._get_icon("chevron-up.svg"))
        self.btn_ai_prev.setIconSize(icon_size)
        self.btn_ai_prev.clicked.connect(self.ai_find_prev)

        self.btn_ai_next = QPushButton()
        self.btn_ai_next.setIcon(self._get_icon("chevron-down.svg"))
        self.btn_ai_next.setIconSize(icon_size)
        self.btn_ai_next.clicked.connect(self.ai_find_next)

        self.btn_close_ai_search = QPushButton()
        self.btn_close_ai_search.setIcon(self._get_icon("x-white.svg"))
        self.btn_close_ai_search.setIconSize(icon_size)
        self.btn_close_ai_search.setFlat(True)
        self.btn_close_ai_search.clicked.connect(self.toggle_ai_search_bar)

        lbl = QLabel("AI Search:")
        lbl.setStyleSheet("font-weight: bold;")

        sb_layout.addWidget(lbl)
        sb_layout.addWidget(self.txt_ai_search)
        sb_layout.addWidget(self.btn_ai_find)
        sb_layout.addWidget(self.btn_ai_prev)
        sb_layout.addWidget(self.btn_ai_next)
        sb_layout.addWidget(self.btn_close_ai_search)

    def select_all_text(self) -> None:
        """
        Executes global selection bindings natively supported within reflow architectures.
        Throws a contextual toast warning on generic image view modes.
        """
        if self.view_mode == ViewMode.REFLOW:
            self.web.page().triggerAction(QWebEnginePage.WebAction.SelectAll)
        else:
            self.show_toast(
                "Select All is currently only supported in Reflow (Web/Markdown) mode."
            )

    def _setup_scroll_area(self) -> None:
        """
        Deploys primary scrolling viewports managing continuous layout flow coordinates correctly.
        """
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidgetResizable(True)
        self.scroll.installEventFilter(self)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.viewport().setMouseTracking(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(20)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scroll.setWidget(self.scroll_content)
        self.scroll.verticalScrollBar().valueChanged.connect(self.defer_scroll_update)
        self.scroll.verticalScrollBar().sliderReleased.connect(self.real_scroll_handler)
        self.stack.addWidget(self.scroll)

    def showEvent(self, event: QEvent) -> None:
        """
        Intercepts Qt UI appearance updates forcing active focus contexts matching document environments seamlessly.

        Args:
            event (QEvent): The native framework visibility event object triggered implicitly.
        """
        super().showEvent(event)
        if self.view_mode == ViewMode.REFLOW:
            self.web.setFocus()
        else:
            self.setFocus()

        if getattr(self, "stack", None) and self.stack.currentIndex() == 2:
            self.txt_open_path.setFocus()
            self._populate_home_recents()

    def _populate_home_recents(self) -> None:
        """
        Translates globally tracked history records into list selection entities shown on initial placeholder empty states.
        """
        if not hasattr(self, "list_recent"):
            return
        self.list_recent.clear()

        if self.window() and hasattr(self.window(), "history_manager"):
            recent_pdfs = self.window().history_manager.history.get("pdf", [])
            for path in recent_pdfs[:13]:
                if os.path.exists(path):
                    name = os.path.basename(path)
                    item = QListWidgetItem(f"📄 {name}")
                    item.setToolTip(path)
                    item.setData(Qt.ItemDataRole.UserRole, path)
                    self.list_recent.addItem(item)

    def load_document(
        self,
        path: str,
        restore_state: bool = False,
        password: Optional[str] = None,
        is_retry: bool = False,
    ) -> None:
        """
        Consumes filepath strings mapping logic execution parsing rendering either markdown or PDF binary streams.
        Loads document using a background thread to prevent UI freezing.

        Args:
            path (str): Full validated filesystem pathway containing data.
            restore_state (bool): Instruction dictating utilization previously saved user coordinates locally stored. Defaults to False.
            password (Optional[str]): String checking presence/absence of password protection in currently open file. Defaults to None.
        """
        if path.lower().endswith(".md"):
            self._load_markdown(path)
            return

        self.load_progress = QProgressDialog(
            f"Loading {os.path.basename(path)}...", None, 0, 0, self
        )
        self.load_progress.setWindowTitle("Please Wait")
        self.load_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.load_progress.setCancelButton(None)
        self.load_progress.show()

        self._load_path = path
        self._load_restore_state = restore_state
        self._load_password = password
        self._load_is_retry = is_retry

        self.load_worker = DocumentLoadWorker(self.engine, path, password)
        self.load_worker.finished.connect(self._on_document_loaded)
        self.load_worker.error.connect(self._on_document_load_error)
        self.load_worker.start()

    def _on_document_loaded(self, doc):
        """Callback for when the thread successfully returns the PDF."""
        self.load_progress.accept()
        self.current_doc = doc

        path = self._load_path
        restore_state = self._load_restore_state
        is_retry = self._load_is_retry

        self._probe_base_page_size()
        self.current_path = path
        try:
            self._loaded_mtime = os.path.getmtime(path) if os.path.exists(path) else None
        except Exception:
            self._loaded_mtime = None
        self._update_tab_title(os.path.basename(path))

        if is_retry and hasattr(self, "show_toast"):
            QTimer.singleShot(
                50, lambda: self.show_toast("Document unlocked successfully.")
            )

        self.toolbar.show()
        self.stack.setCurrentIndex(0)
        self.view_mode = ViewMode.IMAGE
        if hasattr(self.window(), "_update_window_title"):
            self.window()._update_window_title()

        self.settings.setValue("lastFile", path)
        self.load_annotations()
        self._load_external_comments(path)
        QTimer.singleShot(500, lambda: self._detect_signatures(path))
        QTimer.singleShot(1000, self.index_pdf_for_ai)

        if restore_state:
            saved_page = self.settings.value("lastPage", 0, type=int)
            saved_scroll = self.settings.value("lastScrollY", 0, type=int)
            self.current_page_index = min(saved_page, self.current_doc.page_count - 1)
            self.rebuild_layout()
            self.update_view()
            QTimer.singleShot(
                100, lambda: self.scroll.verticalScrollBar().setValue(saved_scroll)
            )
        else:
            self.current_page_index = 0
            self.rebuild_layout()
            self.update_view()

        QTimer.singleShot(2000, self.extract_document_metadata)

    def _on_document_load_error(self, err_str):
        """Callback if the background thread encounters a loading exception."""
        self.load_progress.accept()
        path = self._load_path
        restore_state = self._load_restore_state
        is_retry = self._load_is_retry

        err_str_lower = err_str.lower()
        if "password" in err_str_lower or "encrypted" in err_str_lower:
            error_text = "Incorrect password. Please try again." if is_retry else None
            dialog = PasswordDialog(self, error_msg=error_text)
            if dialog.exec():
                pw = dialog.get_password()
                if pw:
                    self.load_document(path, restore_state, pw, is_retry=True)
            return

        QMessageBox.critical(
            self,
            "Document Load Error",
            f"Could not load the document. Please ensure it is a valid PDF format.\n\nDetails: {err_str}",
        )
        sys.stderr.write(f"Load error: {err_str}\n")

    def reload_document(self) -> None:
        """
        Refreshes the currently open PDF or Markdown tab.
        Checks if the file has changed on disk or is no longer present,
        prompting the user whether to reload or keep the existing state.
        """
        if not self.current_path:
            return

        if not os.path.exists(self.current_path):
            ret = QMessageBox.question(
                self,
                "File No Longer Present",
                f"The file is no longer present at:\n{self.current_path}\n\nDo you want to close this tab?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret == QMessageBox.StandardButton.Yes:
                tw = self._get_tab_widget()
                if tw:
                    idx = tw.indexOf(self)
                    if idx != -1:
                        main_win = self.window()
                        if hasattr(main_win, "close_tab") and tw == getattr(
                            main_win, "tabs_main", None
                        ):
                            main_win.close_tab(idx)
                        elif hasattr(main_win, "close_side_tab") and tw == getattr(
                            main_win, "tabs_side", None
                        ):
                            main_win.close_side_tab(idx)
                        else:
                            tw.removeTab(idx)
                            self.deleteLater()
            return

        current_mtime = None
        try:
            current_mtime = os.path.getmtime(self.current_path)
        except Exception:
            pass

        has_changed = (
            getattr(self, "_loaded_mtime", None) is not None
            and current_mtime is not None
            and current_mtime != self._loaded_mtime
        )

        if has_changed:
            ret = QMessageBox.question(
                self,
                "File Changed On Disk",
                f"The file '{os.path.basename(self.current_path)}' has been modified on disk.\n\n"
                f"Do you want to open the new file, or keep the currently open version?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        saved_page = self.current_page_index
        saved_scroll = self.scroll.verticalScrollBar().value()
        self._loaded_mtime = current_mtime
        self.load_document(self.current_path, restore_state=False)

        def _restore_pos():
            if self.current_doc:
                self.current_page_index = min(
                    saved_page, self.current_doc.page_count - 1
                )
                self.ensure_visible(self.current_page_index)
            self.scroll.verticalScrollBar().setValue(saved_scroll)

        QTimer.singleShot(350, _restore_pos)
        self.show_toast("Refreshed document 🔄")

    def _load_markdown(self, path: str) -> None:
        """
        Compiles raw markdown syntax representations generating reflow HTML internally displayed within WebEngine contexts.

        Args:
            path (str): Reference string accessing unformatted document text structurally.
        """
        self.current_path = path
        try:
            self._loaded_mtime = os.path.getmtime(path) if os.path.exists(path) else None
        except Exception:
            self._loaded_mtime = None
        self.settings.setValue("lastFile", path)
        self._update_tab_title(os.path.basename(path))

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            full_html = generate_markdown_html(text, self.theme_mode != 0)

            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                base_path = getattr(sys, "_MEIPASS")
                font_path = os.path.join(
                    base_path, "riemann", "assets", "fonts", "NotoColorEmoji.ttf"
                )
            else:
                base_path = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                font_path = os.path.join(
                    base_path, "assets", "fonts", "NotoColorEmoji.ttf"
                )

            font_uri = "file:///" + urllib.parse.quote(font_path.replace("\\", "/"))

            emoji_style = f"""
            <style>
                @font-face {{
                    font-family: "Riemann Noto Emoji";
                    src: url("{font_uri}") format("truetype");
                }}
                body, p, span, div, h1, h2, h3, h4, h5, h6, table, th, td, li, pre, code {{ 
                    font-family: inherit, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Riemann Noto Emoji", "Twemoji Mozilla" !important; 
                }}
            </style>
            """

            full_html += emoji_style
            web_view = self._get_or_create_web_view()

            web_view.page().settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
            )
            web_view.setZoomFactor(self.calculate_scale())

            web_view.setHtml(full_html)
            self.toolbar.show()
            self.stack.setCurrentIndex(1)
            self.view_mode = ViewMode.REFLOW

            if hasattr(self.window(), "_update_window_title"):
                self.window()._update_window_title()

            self.btn_facing.setEnabled(False)
            self.btn_ocr.setEnabled(False)

        except Exception as e:
            sys.stderr.write(f"Markdown Load Error: {e}\n")

    def save_document(self) -> None:
        """
        Copies memory mapped file allocations dumping identical structural variants externally safely preventing corruption reliably.
        """
        if not self.current_path or not os.path.exists(self.current_path):
            QMessageBox.warning(self, "Save Error", "No document loaded.")
            return

        start_dir = get_dialog_directory(self.settings)
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save PDF As",
            os.path.join(start_dir, os.path.basename(self.current_path)),
            "PDF Files (*.pdf)",
        )

        if dest:
            save_last_directory(self.settings, dest)
            try:
                shutil.copy2(self.current_path, dest)
                QMessageBox.information(self, "Success", f"Saved to {dest}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file:\n{e}")

    def toggle_annotation_mode(self, checked: bool = False) -> None:
        """Overrides mixin to manage viewport kinetic scrolling lock."""
        super().toggle_annotation_mode(checked)
        self._update_scroller_state()

    def set_tool(self, tool_id: str) -> None:
        """Overrides mixin to manage viewport kinetic scrolling lock."""
        super().set_tool(tool_id)
        self._update_scroller_state()

    def _update_scroller_state(self) -> None:
        """Dynamically locks and unlocks the scroll area for annotations."""
        if not hasattr(self, "scroll") or not self.scroll:
            return

        is_annotating = (
            hasattr(self, "anno_toolbar")
            and self.anno_toolbar.isVisible()
            and getattr(self, "current_tool", "nav") != "nav"
        )

        if is_annotating:
            QScroller.ungrabGesture(self.scroll.viewport())
        else:
            QScroller.grabGesture(
                self.scroll.viewport(),
                QScroller.ScrollerGestureType.LeftMouseButtonGesture,
            )

    def export_annotations(self) -> None:
        """
        Traverses deeply nested annotation JSON layouts outputting clean markdown textual variants suitable for academic review.
        """
        if not self.current_path or not self.annotations:
            QMessageBox.information(self, "Export", "No annotations to export.")
            return

        default_name = (
            os.path.splitext(os.path.basename(self.current_path))[0] + "_notes.md"
        )
        start_dir = get_dialog_directory(self.settings)
        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Notes",
            os.path.join(start_dir, default_name),
            "Markdown (*.md)",
        )

        if not dest_path:
            return

        save_last_directory(self.settings, dest_path)
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

    def get_open_with_menu(self, parent_widget=None) -> QMenu:
        """
        Constructs a QMenu offering options to open the current PDF
        in external applications (standard web browsers and system default viewer).
        """
        menu = QMenu("Open With...", parent_widget or self)
        if not getattr(self, "current_path", None) or not os.path.exists(self.current_path):
            act = menu.addAction("No document open")
            act.setEnabled(False)
            return menu

        path = os.path.abspath(self.current_path)

        def _open_in_browser(cmd):
            try:
                subprocess.Popen([cmd, path])
            except Exception as e:
                QMessageBox.warning(self, "Launch Error", f"Could not launch {cmd}: {e}")

        def _open_system_default():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

        def _choose_other_app():
            app_path, _ = QFileDialog.getOpenFileName(
                self, "Choose Application", "/usr/bin", "Executables (*)"
            )
            if app_path:
                try:
                    subprocess.Popen([app_path, path])
                except Exception as e:
                    QMessageBox.warning(self, "Launch Error", f"Could not launch {app_path}: {e}")

        act_default = menu.addAction("System Default Viewer")
        act_default.triggered.connect(_open_system_default)
        menu.addSeparator()

        browsers = [
            ("Google Chrome", ["google-chrome-stable", "google-chrome"]),
            ("Mozilla Firefox", ["firefox"]),
            ("Chromium", ["chromium-browser", "chromium"]),
            ("Brave Browser", ["brave-browser", "brave"]),
            ("Microsoft Edge", ["microsoft-edge-stable", "microsoft-edge"]),
        ]

        found_any_browser = False
        for label, binaries in browsers:
            for b in binaries:
                if shutil.which(b):
                    act = menu.addAction(label)
                    act.triggered.connect(
                        lambda checked=False, cmd=b: _open_in_browser(cmd)
                    )
                    found_any_browser = True
                    break

        if not found_any_browser:
            act_none = menu.addAction("No standard web browsers detected")
            act_none.setEnabled(False)

        menu.addSeparator()
        act_other = menu.addAction("Choose Other Application...")
        act_other.triggered.connect(_choose_other_app)

        return menu

    def show_open_with_menu(self) -> None:
        """Displays the 'Open With' external application menu below the toolbar button."""
        menu = self.get_open_with_menu()
        menu.exec(
            self.btn_open_external.mapToGlobal(
                QPoint(0, self.btn_open_external.height())
            )
        )

    def _setup_scroller(self) -> None:
        """
        Assigns physics-based smooth tracking variables mimicking native touch interactions predictably gracefully.
        """
        QScroller.grabGesture(
            self.scroll.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        scroller = QScroller.scroller(self.scroll.viewport())
        props = scroller.scrollerProperties()
        props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.01)
        props.setScrollMetric(QScrollerProperties.ScrollMetric.MaximumVelocity, 25)

        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.ScrollingCurve, QEasingCurve.Type.OutCubic
        )
        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.DragVelocitySmoothingFactor, 0.8
        )
        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.VerticalOvershootPolicy,
            QScrollerProperties.OvershootPolicy.OvershootAlwaysOn,
        )

        scroller.setScrollerProperties(props)

    def _get_closest_page(self, value: int) -> int:
        """
        Calculates the geometric closest page index dynamically.

        Args:
            value (int): The current vertical scrollbar position.

        Returns:
            int: The index of the page closest to the vertical center of the viewport.
        """
        if not self.current_doc:
            return self.current_page_index

        scale = self.calculate_scale()
        base_h = self._cached_base_size[1] if self._cached_base_size else 842
        ph = int(base_h * scale) + self.scroll_layout.spacing()
        threshold_y = value + (self.scroll.viewport().height() / 2)

        if ph <= 0:
            return self.current_page_index

        return (
            min(self.current_doc.page_count - 1, max(0, int(threshold_y / ph) * 2))
            if getattr(self, "facing_mode", False)
            else min(self.current_doc.page_count - 1, max(0, int(threshold_y / ph)))
        )

    def defer_scroll_update(self, value: int) -> None:
        """
        Schedules debounce timers throttling frequent update events efficiently.

        Args:
            value (int): Extracted positional marker mapping current visible offset calculations linearly.
        """
        if getattr(self, "_ignore_scroll", False):
            return

        closest = self._get_closest_page(value)
        if closest != self.current_page_index:
            self.current_page_index = closest
            if self.current_doc:
                self.txt_page.setText(str(closest + 1))

        self.scroll_timer.start()
        self._trigger_fullscreen_page_indicator()

    def _trigger_fullscreen_page_indicator(self) -> None:
        """Shows the floating page indicator near the scrollbar across all display modes."""
        if not self.current_doc or self.current_doc.page_count <= 0:
            return

        curr = self.current_page_index + 1
        total = self.current_doc.page_count
        self.page_indicator_popup.setText(f"{curr} / {total}")
        self.page_indicator_popup.adjustSize()

        self._page_indicator_fade_anim.stop()
        self.page_indicator_opacity.setOpacity(1.0)

        sb = self.scroll.verticalScrollBar()
        sb_width = sb.width() if sb.isVisible() else 14
        sb_val = sb.value()
        sb_max = sb.maximum()

        x = max(10, self.width() - sb_width - self.page_indicator_popup.width() - 15)
        if sb_max > 0:
            ratio = sb_val / sb_max
            y = int(40 + ratio * (self.height() - self.page_indicator_popup.height() - 80))
        else:
            y = (self.height() - self.page_indicator_popup.height()) // 2

        self.page_indicator_popup.move(x, y)
        self.page_indicator_popup.show()
        self.page_indicator_popup.raise_()

        self._page_indicator_timer.start(1200)

    def _start_page_indicator_fade(self) -> None:
        """Gradually fades out the page indicator over 800ms."""
        self._page_indicator_fade_anim.stop()
        self._page_indicator_fade_anim.setStartValue(self.page_indicator_opacity.opacity())
        self._page_indicator_fade_anim.setEndValue(0.0)
        self._page_indicator_fade_anim.start()

    def _on_page_indicator_fade_finished(self) -> None:
        """Hides the page indicator once faded out."""
        if self.page_indicator_opacity.opacity() <= 0.01:
            self.page_indicator_popup.hide()

    def real_scroll_handler(self) -> None:
        """
        Dispatches debounced execution queries checking layout dependencies implicitly managing viewport caching correctly.
        """
        if getattr(self, "_ignore_scroll", False):
            return

        self.scroll_timer.stop()
        self.on_scroll_changed(self.scroll.verticalScrollBar().value())

    def on_scroll_changed(self, value: int) -> None:
        """
        Determines closest structural bounds assessing which exact page currently holds optical prominence visibly actively.

        Args:
            value (int): Integer dimension resolving geometric distances mapped properly mathematically.
        """
        closest = self._get_closest_page(value)

        if closest != self.current_page_index:
            self.current_page_index = closest
            if self.current_doc:
                self.txt_page.setText(str(closest + 1))

        if self._virtual_enabled:
            s, e = self._virtual_range
            count = self.current_doc.page_count
            if (self.current_page_index > e - 5 and e < count) or (
                self.current_page_index < s + 3 and s > 0
            ):
                self.rebuild_layout()

        self.render_visible_pages()
        self._apply_signature_overlays()

    def ensure_visible(self, index: int) -> None:
        """
        Repackages positional logic forcefully updating viewport heights keeping explicit index markers visible reliably.

        Args:
            index (int): Specific logical page identifier needed onscreen safely centered actively.
        """
        if index in self.page_widgets:
            self.scroll.ensureWidgetVisible(self.page_widgets[index], 0, 0)
            return

        if self._virtual_enabled and self._cached_base_size:
            start, _ = self._virtual_range
            _, bh = self._cached_base_size
            ph = int(bh * self.calculate_scale()) + self.scroll_layout.spacing()
            top = self._top_spacer.height() if self._top_spacer else 0

            y = (
                top + max(0, (index // 2) - (start // 2)) * ph
                if getattr(self, "facing_mode", False)
                else top + max(0, index - start) * ph
            )
            self.scroll.verticalScrollBar().setValue(
                max(0, int(y - self.scroll.viewport().height() / 2))
            )

    def next_view(self) -> None:
        """
        Calculates positional increments validating bounds seamlessly advancing page index variables sequentially efficiently.
        """
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
        """
        Calculates positional decrements verifying lower bounds stepping backwards navigating efficiently preserving states.
        """
        step = 2 if self.facing_mode else 1
        new_idx = max(0, self.current_page_index - step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(new_idx)

    def start_middle_click_scroll(self, global_pos: QPoint) -> None:
        """Starts middle-click autoscroll navigation with explicit autoscroll cursor."""
        if not hasattr(self, "scroll") or not self.scroll:
            return
        self._autoscroll_active = True
        self._autoscroll_origin_global = global_pos
        self._autoscroll_speed_y = 0.0
        self._autoscroll_speed_x = 0.0
        QApplication.setOverrideCursor(Qt.CursorShape.SizeAllCursor)
        if not hasattr(self, "_autoscroll_nav_timer"):
            self._autoscroll_nav_timer = QTimer(self)
            self._autoscroll_nav_timer.setInterval(20)
            self._autoscroll_nav_timer.timeout.connect(self._on_autoscroll_nav_tick)
        self._autoscroll_nav_timer.start()

    def stop_middle_click_scroll(self) -> None:
        """Stops middle-click autoscroll navigation and restores cursor."""
        self._autoscroll_active = False
        if (
            hasattr(self, "_autoscroll_nav_timer")
            and self._autoscroll_nav_timer.isActive()
        ):
            self._autoscroll_nav_timer.stop()
        self._autoscroll_speed_y = 0.0
        self._autoscroll_speed_x = 0.0
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        if hasattr(self, "scroll") and self.scroll:
            self.scroll.viewport().unsetCursor()

    def _on_autoscroll_nav_tick(self) -> None:
        """Ticks middle-click autoscroll navigation."""
        if not getattr(self, "_autoscroll_active", False) or not self.scroll:
            return
        if self._autoscroll_speed_y != 0:
            vbar = self.scroll.verticalScrollBar()
            vbar.setValue(int(vbar.value() + self._autoscroll_speed_y))
        if self._autoscroll_speed_x != 0:
            hbar = self.scroll.horizontalScrollBar()
            hbar.setValue(int(hbar.value() + self._autoscroll_speed_x))

    def toggle_view_mode(self) -> None:
        """
        Transitions viewing context switching image pipelines converting explicitly formatted HTML representations dynamically.
        """
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        if self.view_mode == ViewMode.REFLOW:
            self._get_or_create_web_view()
            self.web.setZoomFactor(self.calculate_scale())

        self.stack.setCurrentIndex(1 if self.view_mode == ViewMode.REFLOW else 0)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)

        if not hasattr(self, "_rebuild_debounce_timer"):
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

    def toggle_facing_mode(self) -> None:
        """
        Swaps sequential presentation layouts utilizing two column grids dynamically tracking states internally consistently.
        """
        self.facing_mode = not self.facing_mode
        self.settings.setValue("facingMode", self.facing_mode)
        self.btn_facing.setChecked(self.facing_mode)

        if not hasattr(self, "_rebuild_debounce_timer"):
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

    def toggle_scroll_mode(self) -> None:
        """
        Updates persistent UI paradigms navigating pages natively using continuous vs locked configurations logically handled.
        """
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScrollMode", self.continuous_scroll)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)

        if not hasattr(self, "_rebuild_debounce_timer"):
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

    def toggle_reader_fullscreen(self) -> None:
        """
        Issues commands modifying native application sizing behaviors matching global full-screen modes natively resolving references.
        """
        from ...app import RiemannWindow

        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def update_fullscreen_icon(self, state: int) -> None:
        """
        Updates the fullscreen toggle icon dynamically based on the active viewing state.
        """
        icons = {0: "panel-top.svg", 1: "maximize.svg", 2: "minimize.svg"}
        icon_name = icons.get(state, "maximize.svg")
        self.btn_fullscreen.setIcon(self._get_icon(icon_name))

    def open_pdf_dialog(self) -> None:
        """
        Surfaces interactive system menus prompting selection processes loading file responses effectively mapping input data correctly.
        """
        start_dir = get_dialog_directory(self.settings)
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open PDF",
            start_dir,
            "Supported Files (*.pdf *.PDF *.md);;All Files (*)",
        )
        if not paths:
            return

        save_last_directory(self.settings, paths[0])
        self.load_document(paths[0])

        if len(paths) > 1:
            main_win = self.window()
            if hasattr(main_win, "new_pdf_tab"):
                for path in paths[1:]:
                    if hasattr(main_win, "add_to_history"):
                        main_win.add_to_history(path, "pdf")
                    main_win.new_pdf_tab(path)

    def scroll_page(self, direction: int) -> None:
        """
        Pushes view states scaling exactly viewport boundaries dynamically allowing fast navigation sequences reliably.

        Args:
            direction (int): Value resolving numerical step direction logic natively.
        """
        bar = self.scroll.verticalScrollBar()
        step = self.scroll.viewport().height() * 0.9
        bar.setValue(bar.value() + (direction * step))

    def scroll_page_length(self, direction: int) -> None:
        """
        Scrolls vertically by exactly one page length (or advances/recedes view in non-continuous mode).

        Args:
            direction (int): 1 to scroll down/next, -1 to scroll up/previous.
        """
        if not self.continuous_scroll and self.view_mode == ViewMode.IMAGE:
            if direction > 0:
                self.next_view()
            else:
                self.prev_view()
            return

        scale = self.calculate_scale()
        base_h = self._cached_base_size[1] if self._cached_base_size else 842
        spacing = (
            self.scroll_layout.spacing()
            if hasattr(self, "scroll_layout") and self.scroll_layout
            else 0
        )
        ph = int(base_h * scale) + spacing
        if ph <= 0:
            ph = self.scroll.viewport().height()

        bar = self.scroll.verticalScrollBar()
        bar.setValue(int(bar.value() + (direction * ph)))

    def jump_to_page(self, idx: int) -> None:
        """Jumps directly to the given 0-based page index, updating view and scroll position."""
        if not self.current_doc:
            return
        idx = max(0, min(self.current_doc.page_count - 1, idx))
        self.current_page_index = idx
        if hasattr(self, "txt_page"):
            self.txt_page.setText(str(idx + 1))
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
        if hasattr(self, "scroll") and self.scroll:
            self.scroll.setFocus()

    def on_page_input_return(self) -> None:
        """
        Parses manually edited textbox values verifying mathematical limits mapping results cleanly rebuilding bounds dynamically.
        """
        if not self.current_doc:
            return
        try:
            num = int(self.txt_page.text().strip())
            if 1 <= num <= self.current_doc.page_count:
                self.jump_to_page(num - 1)
            else:
                raise ValueError
        except ValueError:
            self.txt_page.setText(str(self.current_page_index + 1))

    # Alias for backwards compatibility
    on_page_text_submitted = on_page_input_return

    def show_toast(self, msg: str) -> None:
        """
        Presents non-blocking momentary information dialogues tracking system notifications properly timing hiding logically clearly.

        Args:
            msg (str): Explicit string format resolving message layout properly reliably.
        """
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
        """
        Inspects application routing pipelines matching distinct element triggers effectively controlling tool interactions properly.

        Args:
            source (QObject): Structural instance tracking emitted signal correctly identifying target contexts smoothly.
            event (QEvent): Execution type evaluating interaction methodology properly sorting pointer movements logically.

        Returns:
            bool: Handled flag skipping native execution reliably protecting custom routines fully efficiently safely.
        """
        if getattr(self, "_autoscroll_active", False):
            if event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
            ):
                if event.type() == QEvent.Type.MouseButtonPress:
                    self.stop_middle_click_scroll()
                return True
            elif event.type() == QEvent.Type.MouseMove:
                cur = event.globalPosition().toPoint()
                dy = cur.y() - self._autoscroll_origin_global.y()
                dx = cur.x() - self._autoscroll_origin_global.x()
                dy_eff = dy - 8 if dy > 8 else (dy + 8 if dy < -8 else 0)
                dx_eff = dx - 8 if dx > 8 else (dx + 8 if dx < -8 else 0)
                self._autoscroll_speed_y = (dy_eff * 0.12) * (
                    1.0 + abs(dy_eff) * 0.003
                )
                self._autoscroll_speed_x = (dx_eff * 0.12) * (
                    1.0 + abs(dx_eff) * 0.003
                )
                if abs(dy) > 8 and abs(dy) >= abs(dx):
                    QApplication.changeOverrideCursor(Qt.CursorShape.SizeVerCursor)
                elif abs(dx) > 8 and abs(dx) > abs(dy):
                    QApplication.changeOverrideCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    QApplication.changeOverrideCursor(Qt.CursorShape.SizeAllCursor)
                return True
            elif (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape
            ):
                self.stop_middle_click_scroll()
                return True

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.MiddleButton:
                if getattr(self, "_autoscroll_active", False):
                    self.stop_middle_click_scroll()
                else:
                    self.start_middle_click_scroll(
                        event.globalPosition().toPoint()
                    )
                return True

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and hasattr(self, "scroll")
            and source == self.scroll.viewport()
        ):
            self.clear_all_text_selections()

        if event.type() == QEvent.Type.Wheel:
            mod = event.modifiers()
            if mod & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.zoom_step(1.1)
                elif delta < 0:
                    self.zoom_step(0.9)
                return True

        if (
            hasattr(self, "anno_toolbar")
            and self.anno_toolbar.isVisible()
            and getattr(self, "current_tool", "nav") != "nav"
            and event.type() in (QEvent.Type.Wheel, QEvent.Type.NativeGesture)
        ):
            return True

        if event.type() == QEvent.Type.KeyPress:
            if source == getattr(self, "scroll", None) or isinstance(
                source, PageWidget
            ):
                key = event.key()
                if getattr(self, "view_mode", None) == ViewMode.IMAGE:
                    if key == Qt.Key.Key_Left:
                        self.prev_view()
                        return True
                    elif key == Qt.Key.Key_Right:
                        self.next_view()
                        return True

                    if not getattr(self, "continuous_scroll", True):
                        vbar = self.scroll.verticalScrollBar()
                        if vbar.maximum() == 0:
                            if key == Qt.Key.Key_Up:
                                self.prev_view()
                                return True
                            elif key == Qt.Key.Key_Down:
                                self.next_view()
                                return True

                    if key == Qt.Key.Key_Up:
                        vbar = self.scroll.verticalScrollBar()
                        vbar.setValue(vbar.value() - 50)
                        return True
                    elif key == Qt.Key.Key_Down:
                        vbar = self.scroll.verticalScrollBar()
                        vbar.setValue(vbar.value() + 50)
                        return True
                    elif key == Qt.Key.Key_Space:
                        mod = event.modifiers()
                        self.scroll_page(
                            -1 if mod & Qt.KeyboardModifier.ShiftModifier else 1
                        )
                        return True

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

            if (
                hasattr(self, "anno_toolbar")
                and self.anno_toolbar.isVisible()
                and self.current_tool != "nav"
            ):
                if event.type() == QEvent.Type.MouseButtonPress:
                    if self.current_tool == "note":
                        if self.handle_annotation_click(source, event):
                            return True
                        rx, ry = self._map_to_unrotated(
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        self.create_new_annotation(page_idx, rx, ry, type="note")
                        return True

                    elif self.current_tool == "text":
                        rx, ry = self._map_to_unrotated(
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        self.create_new_annotation(page_idx, rx, ry, type="text")
                        return True

                    elif self.current_tool in ("stamp_tick", "stamp_cross"):
                        rx, ry = self._map_to_unrotated(
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        self._add_anno_data(
                            page_idx,
                            {
                                "type": "stamp",
                                "subtype": self.current_tool,
                                "rel_pos": (rx, ry),
                                "color": self.pen_color,
                            },
                        )
                        return True

                    elif self.current_tool in (
                        "pen",
                        "highlight",
                        "markup_highlight",
                        "markup_underline",
                        "markup_strikeout",
                        "rect",
                        "oval",
                    ):
                        self.active_drawing = [QPoint(event.pos().x(), event.pos().y())]
                        if self.current_tool.startswith("markup"):
                            self.current_markup_rects = []
                        return True

                    elif self.current_tool == "eraser":
                        self._handle_eraser_click(source, event.pos(), page_idx)
                        return True

                elif event.type() == QEvent.Type.MouseMove and self.active_drawing:
                    if self.current_tool.startswith("markup"):
                        rect = QRect(self.active_drawing[0], event.pos()).normalized()
                        preview_color = QColor(self.pen_color)
                        preview_color.setAlpha(100)
                        source.set_markup_preview([rect], preview_color)

                    elif self.current_tool in ("rect", "oval"):
                        self.active_drawing.append(
                            QPoint(event.pos().x(), event.pos().y())
                        )
                        source.set_shape_preview(
                            self.active_drawing[0],
                            event.pos(),
                            self.current_tool,
                            self.pen_color,
                            self.pen_thickness,
                        )

                    else:
                        self.active_drawing.append(
                            QPoint(event.pos().x(), event.pos().y())
                        )
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
                        pts = []
                        for p in self.active_drawing:
                            pts.append(self._map_to_unrotated(p.x() / w, p.y() / h))

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

                    elif self.current_tool in ("rect", "oval") and self.active_drawing:
                        w, h = source.width(), source.height()
                        p1 = self.active_drawing[0]
                        p2 = self.active_drawing[-1]
                        rx1, ry1 = self._map_to_unrotated(p1.x() / w, p1.y() / h)
                        rx2, ry2 = self._map_to_unrotated(p2.x() / w, p2.y() / h)
                        self._add_anno_data(
                            page_idx,
                            {
                                "type": "drawing",
                                "subtype": self.current_tool,
                                "points": [(rx1, ry1), (rx2, ry2)],
                                "color": self.pen_color,
                                "thickness": self.pen_thickness,
                            },
                        )
                        self.active_drawing = []
                        source.clear_temp_stroke()
                        return True

                    elif self.current_tool.startswith("markup") and self.active_drawing:
                        rects, text = self._get_linear_text_selection(
                            page_idx, self.active_drawing[0], event.pos()
                        )

                        if rects:
                            w, h = source.width(), source.height()
                            normalized_rects = []
                            for r in rects:
                                rx1, ry1 = self._map_to_unrotated(
                                    r.left() / w, r.top() / h
                                )
                                rx2, ry2 = self._map_to_unrotated(
                                    r.right() / w, r.bottom() / h
                                )
                                normalized_rects.append([rx1, ry1, rx2, ry2])

                            self._add_anno_data(
                                page_idx,
                                {
                                    "type": "markup",
                                    "subtype": self.current_tool.replace("markup_", ""),
                                    "rects": normalized_rects,
                                    "text": text,
                                    "color": self.pen_color,
                                },
                            )
                        source.set_markup_preview([], QColor(0, 0, 0, 0))
                        self.active_drawing = []
                        return True

            elif (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                source.setFocus()
                self._mouse_press_pos = event.pos()
                self.is_selecting_text = False
                self.text_select_start = event.pos()

                now = time.time()
                last_time = getattr(self, "_last_click_time", 0.0)
                last_pos = getattr(self, "_last_click_pos", QPoint())
                dt = now - last_time
                dist = (event.pos() - last_pos).manhattanLength()

                if dt < 0.55 and dist < 18:
                    self._click_count = getattr(self, "_click_count", 1) + 1
                else:
                    self._click_count = 1

                self._last_click_time = now
                self._last_click_pos = event.pos()

                if self._click_count >= 3:
                    self._click_count = 3
                    self._just_selected_multi_click = True
                    rects, text = self._get_paragraph_at_pos(
                        page_idx, event.pos()
                    )
                    if rects and text:
                        self.clear_all_text_selections()
                        source.set_text_selection(rects)
                        self.current_selected_text = text
                    return True

                return True

            elif (
                event.type() == QEvent.Type.MouseButtonDblClick
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._click_count = 2
                self._last_click_time = time.time()
                self._last_click_pos = event.pos()
                self._just_selected_multi_click = True
                rects, text = self._get_word_at_pos(page_idx, event.pos())
                if rects and text:
                    self.clear_all_text_selections()
                    source.set_text_selection(rects)
                    self.current_selected_text = text
                return True

            elif event.type() == QEvent.Type.MouseMove:
                if getattr(self, "_autoscroll_active", False):
                    return True
                if (
                    hasattr(source, "link_rects")
                    and not getattr(self, "is_selecting_text", False)
                    and not self.active_drawing
                    and not self.is_snipping
                ):
                    hovered_url = None
                    for r, url in source.link_rects:
                        if r.contains(event.pos()):
                            hovered_url = url
                            break

                    if hovered_url:
                        if hovered_url.startswith("#page="):
                            target_page_str = hovered_url.split("page=")[-1]
                            self.link_tooltip.setText(
                                f"Jump to Page {target_page_str} ↗"
                            )
                        else:
                            self.link_tooltip.setText(hovered_url)
                        self.link_tooltip.adjustSize()
                        self.link_tooltip.move(
                            10, self.height() - self.link_tooltip.height() - 10
                        )
                        self.link_tooltip.show()
                        self.link_tooltip.raise_()

                        if hovered_url.startswith("#page=") or (
                            event.modifiers()
                            == Qt.KeyboardModifier.ControlModifier
                        ):
                            source.setCursor(Qt.CursorShape.PointingHandCursor)
                        else:
                            source.setCursor(Qt.CursorShape.IBeamCursor)
                    else:
                        hovered_comment = self._get_comment_at_pos(
                            page_idx, event.pos(), source.width(), source.height()
                        )
                        if hovered_comment:
                            author = hovered_comment.get("author") or "Comment"
                            contents = hovered_comment.get("contents", "")
                            preview = (
                                contents if len(contents) <= 90 else contents[:87] + "..."
                            )
                            self.link_tooltip.setText(f"💬 {author}: {preview}")
                            self.link_tooltip.adjustSize()
                            self.link_tooltip.move(
                                10, self.height() - self.link_tooltip.height() - 10
                            )
                            self.link_tooltip.show()
                            self.link_tooltip.raise_()
                            source.setCursor(Qt.CursorShape.PointingHandCursor)
                        else:
                            self.link_tooltip.hide()
                            source.setCursor(Qt.CursorShape.IBeamCursor)

                if (
                    (event.buttons() & Qt.MouseButton.LeftButton)
                    and hasattr(self, "_mouse_press_pos")
                    and not self.active_drawing
                    and not self.is_snipping
                ):
                    if not getattr(self, "is_selecting_text", False):
                        if (
                            event.pos() - self._mouse_press_pos
                        ).manhattanLength() >= 5:
                            self.is_selecting_text = True
                            self._just_selected_multi_click = False
                    if getattr(self, "is_selecting_text", False):
                        rects, text = self._get_linear_text_selection(
                            page_idx, self.text_select_start, event.pos()
                        )
                        source.set_text_selection(rects)
                        self.current_selected_text = text
                return True

            elif event.type() == QEvent.Type.MouseButtonRelease:
                if (
                    event.button() == Qt.MouseButton.LeftButton
                    and not self.active_drawing
                    and not self.is_snipping
                    and not getattr(self, "is_selecting_text", False)
                ):
                    if hasattr(source, "link_rects"):
                        pos = event.pos()
                        for rect, url in source.link_rects:
                            if rect.contains(pos):
                                self.clear_all_text_selections()
                                if url.startswith("#page="):
                                    try:
                                        target_page = (
                                            int(url.split("page=")[-1]) - 1
                                        )
                                        self.jump_to_page(target_page)
                                        return True
                                    except ValueError:
                                        pass
                                elif (
                                    event.modifiers()
                                    == Qt.KeyboardModifier.ControlModifier
                                ):
                                    main_win = self.window()
                                    if hasattr(main_win, "new_browser_tab"):
                                        main_win.new_browser_tab(url)
                                    else:
                                        QDesktopServices.openUrl(QUrl(url))
                                    return True

                if event.button() == Qt.MouseButton.LeftButton:
                    if getattr(self, "is_selecting_text", False):
                        self.is_selecting_text = False
                        rects, text = self._get_linear_text_selection(
                            page_idx, self.text_select_start, event.pos()
                        )
                        source.set_text_selection(rects)
                        self.current_selected_text = text
                    elif getattr(self, "_just_selected_multi_click", False):
                        # Multi-click selection (double or triple click): preserve highlight!
                        self._just_selected_multi_click = False
                    else:
                        # Single click without dragging: check if clicked on external comment or note
                        comment = self._get_comment_at_pos(
                            page_idx, event.pos(), source.width(), source.height()
                        )
                        if comment:
                            self.clear_all_text_selections()
                            self.show_external_comment_dialog(comment)
                            return True

                        if self.handle_annotation_click(source, event):
                            self.clear_all_text_selections()
                            return True

                        # Single click without dragging: clear selection immediately
                        self.clear_all_text_selections()
                return True

            elif (
                event.type() == QEvent.Type.KeyPress
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_C
            ):
                if getattr(self, "current_selected_text", ""):
                    QApplication.clipboard().setText(self.current_selected_text)
                    self.show_toast("Copied text to clipboard! 📋")
                return True

            elif event.type() == QEvent.Type.ContextMenu:
                if getattr(self, "current_selected_text", ""):
                    menu = QMenu(source)
                    copy_action = menu.addAction("Copy Text")
                    search_action = menu.addAction("Search Web")

                    action = menu.exec(event.globalPos())

                    if action == copy_action:
                        QApplication.clipboard().setText(self.current_selected_text)
                        self.show_toast("Copied text to clipboard! 📋")
                    elif action == search_action:
                        self._search_web_for_selected_text(self.current_selected_text)
                return True

        return super().eventFilter(source, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        Translates keyboard directives executing matching commands controlling specific system interactions robustly optimally gracefully.

        Args:
            event (QKeyEvent): Native object preserving stroke tracking details effectively fully accurately.
        """
        key = event.key()
        mod = event.modifiers()

        if key == Qt.Key.Key_Escape:
            if getattr(self, "_reader_fullscreen", False):
                if hasattr(self.window(), "exit_fullscreen"):
                    self.window().exit_fullscreen()
                event.accept()
                return

        if key == Qt.Key.Key_F11:
            self.toggle_reader_fullscreen()
            return

        if key == Qt.Key.Key_F5:
            self.reload_document()
            event.accept()
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
            elif key == Qt.Key.Key_A:
                self.apply_zoom_string("Auto Fit")
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
            elif key == Qt.Key.Key_PageDown:
                self.scroll_page_length(1)
                event.accept()
                return
            elif key == Qt.Key.Key_PageUp:
                self.scroll_page_length(-1)
                event.accept()
                return
            elif key == Qt.Key.Key_Space:
                self.scroll_page(-1 if mod & Qt.KeyboardModifier.ShiftModifier else 1)

            elif key == Qt.Key.Key_Up:
                if (
                    not self.continuous_scroll
                    and self.scroll.verticalScrollBar().maximum() == 0
                ):
                    self.prev_view()
                else:
                    self.scroll.verticalScrollBar().setValue(
                        self.scroll.verticalScrollBar().value() - 50
                    )
            elif key == Qt.Key.Key_Down:
                if (
                    not self.continuous_scroll
                    and self.scroll.verticalScrollBar().maximum() == 0
                ):
                    self.next_view()
                else:
                    self.scroll.verticalScrollBar().setValue(
                        self.scroll.verticalScrollBar().value() + 50
                    )

            elif key == Qt.Key.Key_Home:
                self.scroll.verticalScrollBar().setValue(0)
            elif key == Qt.Key.Key_End:
                self.scroll.verticalScrollBar().setValue(
                    self.scroll.verticalScrollBar().maximum()
                )

    def event(self, event: QEvent) -> bool:
        """
        Intercepts raw system events to support native gestures like pinch-to-zoom on trackpads.

        Args:
            event (QEvent): The native system event triggered by the user.

        Returns:
            bool: True if the native gesture event was intercepted and handled; otherwise delegates to the parent class.
        """
        if event.type() == QEvent.Type.NativeGesture:
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                delta = event.value()
                self.manual_scale = max(
                    0.1, min(self.manual_scale * (1.0 + delta), 5.0)
                )
                self.zoom_mode = ZoomMode.MANUAL
                if hasattr(self, "apply_visual_zoom"):
                    self.apply_visual_zoom()

                if not hasattr(self, "_zoom_debounce_timer"):
                    self._zoom_debounce_timer = QTimer(self)
                    self._zoom_debounce_timer.setSingleShot(True)
                    self._zoom_debounce_timer.setInterval(250)
                    self._zoom_debounce_timer.timeout.connect(self.on_zoom_finished)

                self._zoom_debounce_timer.start()
                return True
        return super().event(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """
        Manages rotational pointer input updating zoom calculations effectively bypassing default scrolling natively securely actively.

        Args:
            event (QWheelEvent): Complex parameter detailing positional offsets dynamically tracked explicitly locally reliably.
        """
        if (
            hasattr(self, "anno_toolbar")
            and self.anno_toolbar.isVisible()
            and getattr(self, "current_tool", "nav") != "nav"
        ):
            event.accept()
            return

        mod = event.modifiers()

        if mod & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_step(1.1)
            elif delta < 0:
                self.zoom_step(0.9)
            event.accept()
            return

        if mod & Qt.KeyboardModifier.AltModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                vbar = self.scroll.verticalScrollBar()
                vbar.setValue(vbar.value() - (delta * 3))
            event.accept()
            return

        if not self.continuous_scroll and self.view_mode == ViewMode.IMAGE:
            vbar = self.scroll.verticalScrollBar()
            if vbar.maximum() == 0:
                if not hasattr(self, "_scroll_accumulator"):
                    self._scroll_accumulator = 0

                delta = event.angleDelta().y()
                self._scroll_accumulator += delta

                if self._scroll_accumulator >= 200:
                    self.prev_view()
                    self._scroll_accumulator = 0
                elif self._scroll_accumulator <= -200:
                    self.next_view()
                    self._scroll_accumulator = 0
                event.accept()
                return

        if hasattr(self, "_scroll_accumulator"):
            self._scroll_accumulator = 0
        super().wheelEvent(event)

    def on_zoom_selected(self, idx: int) -> None:
        """
        Delegates drop-down list changes extracting selection context accurately updating UI metrics seamlessly dynamically accurately.

        Args:
            idx (int): Position integer referencing active string effectively properly efficiently implicitly.
        """
        self.apply_zoom_string(self.combo_zoom.currentText())
        if hasattr(self, "scroll") and self.scroll:
            self.scroll.setFocus()

    def on_zoom_text_entered(self) -> None:
        """
        Fetches modified input box properties returning parsed visual logic properly reliably dynamically systematically safely correctly.
        """
        self.apply_zoom_string(self.combo_zoom.lineEdit().text())
        if hasattr(self, "scroll") and self.scroll:
            self.scroll.setFocus()

    def apply_zoom_string(self, text: str) -> None:
        """
        Interprets input parsing numerical constraints checking boundary ranges cleanly resetting render properties effectively reliably natively.

        Args:
            text (str): Evaluation mapping resolving formatting rules efficiently globally correctly safely dynamically appropriately.
        """
        if "Auto" in text:
            self.zoom_mode = ZoomMode.AUTO_FIT
        elif "Width" in text:
            self.zoom_mode = ZoomMode.FIT_WIDTH
        elif "Height" in text:
            self.zoom_mode = ZoomMode.FIT_HEIGHT
        else:
            try:
                val = (
                    float(text.replace("%", "").strip())
                    if "%" in text
                    else float(text.strip())
                )
                self.manual_scale = max(
                    0.1, min(val / 100.0 if val > 5.0 else val, 5.0)
                )
                self.zoom_mode = ZoomMode.MANUAL
            except ValueError:
                pass
        self.on_zoom_changed_internal()

    def on_zoom_finished(self) -> None:
        """
        Fired when the user finishes zooming (debounce).
        Triggers a high-res re-render of the visible pages without destroying widgets.
        """
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)

        self.rendered_pages.clear()
        self.update_view()
        self._update_zoom_combo_text()

    def on_zoom_changed_internal(self) -> None:
        """Triggered by UI buttons, shortcuts, or explicit zoom changes."""
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)

        if (
            getattr(self, "view_mode", None) == ViewMode.REFLOW
            and hasattr(self, "web")
            and self.web
        ):
            self.web.setZoomFactor(self.calculate_scale())

        target_page = getattr(self, "current_page_index", 0)
        if hasattr(self, "apply_visual_zoom"):
            self.apply_visual_zoom()
            self.current_page_index = target_page
            self.txt_page.setText(str(target_page + 1))
        else:
            self._update_all_widget_sizes()
        self._update_zoom_combo_text()

        if not hasattr(self, "_zoom_debounce_timer"):
            self._zoom_debounce_timer = QTimer(self)
            self._zoom_debounce_timer.setSingleShot(True)
            self._zoom_debounce_timer.setInterval(250)
            self._zoom_debounce_timer.timeout.connect(self.on_zoom_finished)
        self._zoom_debounce_timer.start()

    def _update_zoom_combo_text(self) -> None:
        """Helper to update the UI combobox with the current scale."""
        current_scale = self.calculate_scale()
        pct = int(current_scale * 100)

        if self.zoom_mode == ZoomMode.AUTO_FIT:
            txt = f"Auto Fit ({pct}%)"
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            txt = f"Fit Width ({pct}%)"
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            txt = f"Fit Height ({pct}%)"
        else:
            txt = f"{pct}%"

        self.combo_zoom.setCurrentText(txt)
        fm = self.combo_zoom.fontMetrics()
        max_width = fm.horizontalAdvance(txt) + 40
        self.combo_zoom.setFixedWidth(max(100, max_width))

    def _update_all_widget_sizes(self) -> None:
        """
        Recompiles explicit hardware measurements adjusting all cached labels gracefully avoiding redundant evaluations properly systematically comprehensively effectively.
        """
        w, h = self._get_target_page_size()
        for lbl in self.page_widgets.values():
            lbl.setFixedSize(w, h)

    def zoom_step(self, factor: float) -> None:
        """
        Multiplies base properties determining incremental dimension shifts mapping rendering values reliably predictably exactly.

        Args:
            factor (float): Step coefficient actively shaping proportional bounds efficiently seamlessly completely automatically correctly.
        """
        self.manual_scale *= factor
        self.zoom_mode = ZoomMode.MANUAL
        self.on_zoom_changed_internal()

    def apply_theme(self) -> None:
        """
        Modifies localized style objects dynamically replacing raw background properties utilizing updated user settings directly safely optimally completely.
        """
        is_dark = self.theme_mode != 0
        pal = self.palette()
        color = QColor(30, 30, 30) if is_dark else QColor(240, 240, 240)
        pal.setColor(QPalette.ColorRole.Window, color)
        self.setPalette(pal)

        if hasattr(self, "web") and self.web:
            self.web.page().setBackgroundColor(color)

        bg_scroll = "#222" if is_dark else "#eee"
        self.scroll_content.setStyleSheet(
            f"#scrollContent {{ background-color: {bg_scroll}; }}"
        )

        fg = "#ddd" if is_dark else "#111"
        checked_bg = "rgba(60, 140, 255, 0.3)" if is_dark else "rgba(0, 100, 255, 0.2)"
        checked_border = "#50a0ff"

        sb_bg = "#2a2a2a" if is_dark else "#e0e0e0"
        sb_fg = "#ddd" if is_dark else "#111"
        input_bg = "#1e1e1e" if is_dark else "#ffffff"
        input_border = "#555" if is_dark else "#bbb"

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_p = getattr(sys, "_MEIPASS")
            arrow_path = os.path.join(
                base_p,
                "riemann",
                "assets",
                "icons",
                "chevron-down-white.svg" if is_dark else "chevron-down.svg",
            )
        else:
            base_p = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            arrow_path = os.path.join(
                base_p,
                "assets",
                "icons",
                "chevron-down-white.svg" if is_dark else "chevron-down.svg",
            )

        arrow_url = arrow_path.replace("\\", "/")

        self.toolbar.setStyleSheet(f"""
            QWidget {{ background: {color.name()}; color: {fg}; }}
            QPushButton {{ border: 1px solid transparent; padding: 6px; border-radius: 4px; background: transparent; }}
            QPushButton:hover {{ background: rgba(128, 128, 128, 0.2); }}
            QPushButton:checked {{ background-color: {checked_bg}; border: 1px solid {checked_border}; }}
            QComboBox {{ background-color: {input_bg}; color: {sb_fg}; border: 1px solid {input_border}; border-radius: 4px; padding: 4px; }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox::down-arrow {{ image: url("{arrow_url}"); width: 16px; height: 16px; }}
        """)

        self.search_bar.setStyleSheet(f"""
            QWidget {{ background-color: {sb_bg};
                color: {sb_fg}; }}
            QLineEdit {{ background-color: {input_bg}; 
                color: {sb_fg}; 
                border: 1px solid {input_border}; 
                border-radius: 4px; 
                padding: 4px; }}
            QPushButton {{ background: transparent; 
                border: none; }}
            QPushButton:hover {{ background: rgba(128,128,128,0.2); 
                border-radius: 4px; }}
        """)

        btn_sec_bg = "#2C2C30" if is_dark else "#E0E0E0"
        btn_sec_border = "#3F3F46" if is_dark else "#CCCCCC"
        btn_sec_hover = "#52525B" if is_dark else "#D0D0D0"
        btn_sec_fg = "#E0E0E0" if is_dark else "#111111"

        if hasattr(self, "btn_secure_export"):
            self.btn_secure_export.setStyleSheet(f"""
                QPushButton {{ padding: 6px 14px; border-radius: 4px; background-color: {btn_sec_bg}; color: {btn_sec_fg}; border: 1px solid {btn_sec_border}; }}
                QPushButton:hover {{ background-color: {btn_sec_hover}; border: 1px solid #999; }}
            """)

        if hasattr(self, "btn_save"):
            self._update_icons()

    def toggle_theme(self) -> None:
        """
        Cycles configuration properties through Light, Fast Dark, and Smart Dark modes.
        Saves preferences globally and triggers visual reconstructions safely.
        """
        self.theme_mode = (self.theme_mode + 1) % 3
        self.settings.setValue("themeMode", self.theme_mode)
        self.apply_theme()

        if not hasattr(self, "_rebuild_debounce_timer"):
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

        mode_names = ["Light Mode", "Fast Dark Mode", "Smart Dark Mode"]
        self.show_toast(f"Theme set to: {mode_names[self.theme_mode]}")

    def _setup_home_page(self) -> None:
        """
        Constructs default placeholder interface showing initial history interactions explicitly configuring bounds structurally precisely flawlessly correctly gracefully.
        """
        self.home_page_widget = QWidget()
        self.home_page_widget.setStyleSheet("""
            QWidget { background-color: #0f0f13; color: #eee; font-family: 'Segoe UI', system-ui, sans-serif; }
            QLineEdit { background: #1a1a20; border: 1px solid #333; border-radius: 20px; padding: 12px 20px; font-size: 15px; color: white; }
            QLineEdit:focus { border: 1px solid #ff4500; }
            QPushButton { background: transparent; border: 2px dashed #555; color: #aaa; border-radius: 12px; padding: 12px 24px; font-size: 15px; font-weight: bold; }
            QPushButton:hover { border-color: #ff4500; color: #fff; background: rgba(255, 69, 0, 0.1); }
            QListWidget { background: transparent; border: none; font-size: 14px; outline: none; }
            QListWidget::item { padding: 12px; border-radius: 8px; margin-bottom: 6px; background: rgba(30, 30, 35, 0.8); border: 1px solid #222; }
            QListWidget::item:hover { background: rgba(255, 69, 0, 0.1); border-color: #ff4500; }
            
            #dropZone {
                border: 3px dashed #555;
                border-radius: 15px;
                background: rgba(30, 30, 35, 0.4);
            }
        """)

        layout = QHBoxLayout(self.home_page_widget)
        layout.setContentsMargins(60, 60, 60, 60)

        left_layout = QVBoxLayout()
        left_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Riemann")
        title.setStyleSheet(
            "font-size: 54px; font-weight: 300; letter-spacing: 4px; color: #ff4500; margin-bottom: 10px; background: transparent;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("High-Performance Research Environment")
        subtitle.setStyleSheet(
            "font-size: 16px; color: #888; margin-bottom: 50px; background: transparent;"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.drop_zone = DropZoneLabel("Drop PDF Here")
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_zone.setStyleSheet(
            "font-size: 24px; color: #888; font-weight: bold; letter-spacing: 2px;"
        )
        self.drop_zone.setMinimumHeight(150)
        self.drop_zone.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.drop_zone.file_dropped.connect(self._on_file_dropped)

        self.txt_open_path = QLineEdit()
        self.txt_open_path.setPlaceholderText(
            "Paste PDF absolute path here and press Enter..."
        )
        self.txt_open_path.returnPressed.connect(self._on_home_path_entered)

        btn_browse = QPushButton("Browse Files (Ctrl+O)")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self.open_pdf_dialog)

        left_layout.addStretch()
        left_layout.addWidget(title)
        left_layout.addWidget(subtitle)
        left_layout.addWidget(self.drop_zone)
        left_layout.addSpacing(30)
        left_layout.addWidget(self.txt_open_path)
        left_layout.addSpacing(20)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(btn_browse)
        btn_layout.addStretch()
        left_layout.addLayout(btn_layout)
        left_layout.addStretch()

        right_layout = QVBoxLayout()
        recent_label = QLabel("Recent Documents")
        recent_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; margin-bottom: 15px; color: #ccc; background: transparent;"
        )

        self.list_recent = QListWidget()
        self.list_recent.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_recent.itemClicked.connect(self._on_recent_item_clicked)

        self.list_recent.setMinimumWidth(400)

        right_layout.addWidget(recent_label)
        right_layout.addWidget(self.list_recent)

        layout.addStretch(1)
        layout.addLayout(left_layout, 2)
        layout.addStretch(1)
        layout.addLayout(right_layout, 3)
        layout.addStretch(1)

    def _on_home_path_entered(self) -> None:
        """
        Parses text parameters opening target documents automatically removing redundant syntax completely correctly dynamically fully dynamically securely safely inherently effectively seamlessly actively completely automatically.
        """
        path = self.txt_open_path.text().strip().strip('"').strip("'")
        if os.path.exists(path) and os.path.isfile(path):
            self.load_document(path)
        else:
            self.show_toast("File not found on disk.")

    def _on_recent_item_clicked(self, item) -> None:
        """
        Pulls item bounds accessing background pathway objects natively initiating load behaviors properly cleanly effortlessly reliably properly dynamically correctly smoothly easily efficiently securely seamlessly completely transparently cleanly naturally easily transparently fully correctly logically automatically cleanly easily simply completely perfectly effectively predictably flawlessly naturally dynamically gracefully successfully appropriately automatically successfully transparently easily optimally.

        Args:
            item: User event marker triggering contextual file paths cleanly seamlessly properly fully.
        """
        path = item.data(Qt.ItemDataRole.UserRole)
        if os.path.exists(path):
            self.load_document(path)
        else:
            self.show_toast("File no longer exists at this location.")

    def _on_file_dropped(self, path: str) -> None:
        """Handles dropped file pathways."""
        if not path:
            return
        clean = QUrl(path).toLocalFile() if path.startswith("file://") else path
        abs_p = os.path.abspath(clean)
        if abs_p.lower().endswith(".pdf") or abs_p.lower().endswith(".md"):
            if not self.current_path:
                self.load_document(abs_p)
            else:
                main_win = self.window()
                if hasattr(main_win, "new_pdf_tab"):
                    main_win.new_pdf_tab(abs_p)
                else:
                    self.load_document(abs_p)

    def dragEnterEvent(self, event) -> None:
        """Accept drag if it contains local files."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        """Accept move if it contains local files."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        """Handle dropping files anywhere on the ReaderTab."""
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._on_file_dropped(url.toLocalFile())
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def _map_to_unrotated(self, rx: float, ry: float) -> Tuple[float, float]:
        """Maps x and y coordinates of rotated PDF to unrotated pixmaps

        Args:
            rx: Unrotated x coordinates
            ry: Unrotated y coordinates

        Returns:
            Tuple[float, float]: Rotated x and y coordinates"""
        rotation = getattr(self, "rotation", 0)
        if rotation == 90:
            return ry, 1.0 - rx
        elif rotation == 180:
            return 1.0 - rx, 1.0 - ry
        elif rotation == 270:
            return 1.0 - ry, rx
        return rx, ry

    def resizeEvent(self, event: Any) -> None:
        """
        Recalculates specific UI overlay positions consistently anchoring elements cleanly.

        Args:
            event (Any): Fired geometry update system event.
        """
        super().resizeEvent(event)

        if hasattr(self, "lbl_toast") and self.lbl_toast.isVisible():
            self.lbl_toast.move(
                (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
            )

        if (
            getattr(self, "current_doc", None)
            and getattr(self, "view_mode", None) == ViewMode.IMAGE
        ):
            if getattr(self, "zoom_mode", None) in (
                ZoomMode.FIT_WIDTH,
                ZoomMode.FIT_HEIGHT,
            ):
                if not hasattr(self, "_resize_timer"):
                    self._resize_timer = QTimer(self)
                    self._resize_timer.setSingleShot(True)
                    self._resize_timer.setInterval(150)
                    self._resize_timer.timeout.connect(self.on_zoom_changed_internal)
                self._resize_timer.start()

    def export_secure_pdf(self) -> None:
        """Prompts the user for a password and saves an encrypted copy."""
        if not hasattr(self, "current_path") or not self.current_path:
            return

        password, ok = QInputDialog.getText(
            self,
            "Secure PDF",
            "Enter a password to lock this PDF:",
            QLineEdit.EchoMode.Password,
        )

        if not ok or not password:
            return

        start_dir = get_dialog_directory(self.settings)
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Encrypted PDF",
            os.path.join(
                start_dir,
                os.path.basename(self.current_path).replace(".pdf", "_secure.pdf"),
            ),
            "PDF Files (*.pdf)",
        )

        if not save_path:
            return

        save_last_directory(self.settings, save_path)
        try:
            with pikepdf.Pdf.open(self.current_path) as pdf:
                encryption = pikepdf.Encryption(
                    user=password,
                    owner=password,
                    allow=pikepdf.Permissions(extract=False, modify_assembly=False),
                )

                pdf.save(save_path, encryption=encryption)

            QMessageBox.information(
                self, "Success", "Encrypted PDF saved successfully!"
            )

        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to encrypt PDF:\n{str(e)}"
            )

    def clear_all_text_selections(self) -> None:
        """Clears text selections and copied text across all page widgets."""
        self.current_selected_text = ""
        self.is_selecting_text = False
        if hasattr(self, "page_widgets"):
            for w in self.page_widgets.values():
                if hasattr(w, "set_text_selection") and getattr(
                    w, "selected_text_rects", None
                ):
                    w.set_text_selection([])

    def _get_linear_text_selection(
        self, page_idx: int, start_pos: QPoint, end_pos: QPoint
    ) -> tuple[List[QRect], str]:
        """
        Calculates character-level linear text selection with precise font metrics,
        robust reading order line-clustering, and clean multi-line box merging.
        """
        if not start_pos or not end_pos:
            return [], ""

        widget = self.page_widgets.get(page_idx)
        if not widget:
            return [], ""

        logical_w = widget.width()
        logical_h = widget.height()
        scale = self.calculate_scale()
        rotation = getattr(self, "rotation", 0)

        cache_key = (page_idx, scale, logical_w, logical_h, rotation)
        if not hasattr(self, "_char_geometry_cache"):
            self._char_geometry_cache = {}

        if cache_key not in self._char_geometry_cache:
            if page_idx not in self.text_segments_cache:
                self.text_segments_cache[page_idx] = (
                    self.current_doc.get_text_segments(page_idx)
                )

            segments = self.text_segments_cache[page_idx]
            raw_segments = []

            def get_char_weight(ch: str) -> float:
                if ch in "ijl|!':;., -`'\"1":
                    return 0.45
                elif ch in "mwMW@#%&":
                    return 1.35
                elif ch.isupper():
                    return 1.15
                elif ch in "frt":
                    return 0.65
                return 0.85

            for seg_idx, (text, (l, t, r, b)) in enumerate(segments):
                if not text:
                    continue

                seg_x = int(l * scale)
                seg_w = max(1, int((r - l) * scale))
                seg_h = max(1, int((t - b) * scale))
                seg_y = int(logical_h - (t * scale))

                # Align visual highlight box vertically with glyphs rather than font ascender ceiling
                y_adj = max(0, int(seg_h * 0.10))
                seg_y += y_adj
                seg_h = max(1, int(seg_h * 0.90))

                if rotation == 90:
                    seg_x, seg_y = int(logical_h) - seg_y - seg_h, seg_x
                    seg_w, seg_h = seg_h, seg_w
                elif rotation == 180:
                    seg_x, seg_y = (
                        int(logical_w) - seg_x - seg_w,
                        int(logical_h) - seg_y - seg_h,
                    )
                elif rotation == 270:
                    seg_x, seg_y = seg_y, int(logical_w) - seg_x - seg_w
                    seg_w, seg_h = seg_h, seg_w

                if seg_h > logical_h * 0.5 or seg_w > logical_w * 0.8:
                    continue

                total_weight = sum(get_char_weight(c) for c in text) or 1.0
                char_boxes = []
                cur_x = seg_x
                for ch in text:
                    w_ch = max(
                        1, int((get_char_weight(ch) / total_weight) * seg_w)
                    )
                    char_boxes.append((ch, QRect(cur_x, seg_y, w_ch, seg_h)))
                    cur_x += w_ch

                if char_boxes:
                    last_c, last_r = char_boxes[-1]
                    rem = (seg_x + seg_w) - (last_r.x() + last_r.width())
                    if rem != 0:
                        char_boxes[-1] = (
                            last_c,
                            QRect(
                                last_r.x(),
                                last_r.y(),
                                max(1, last_r.width() + rem),
                                last_r.height(),
                            ),
                        )

                raw_segments.append(
                    {
                        "seg_idx": seg_idx,
                        "text": text,
                        "x": seg_x,
                        "y": seg_y,
                        "w": seg_w,
                        "h": seg_h,
                        "char_boxes": char_boxes,
                    }
                )

            # Cluster segments into lines using vertical overlap
            lines: List[List[dict]] = []
            for seg_item in raw_segments:
                placed = False
                for line in lines:
                    ref = line[0]
                    overlap = min(
                        seg_item["y"] + seg_item["h"], ref["y"] + ref["h"]
                    ) - max(seg_item["y"], ref["y"])
                    min_h = min(seg_item["h"], ref["h"])
                    if min_h > 0 and (overlap / min_h) >= 0.40:
                        line.append(seg_item)
                        placed = True
                        break
                if not placed:
                    lines.append([seg_item])

            # Sort lines top-to-bottom by average vertical position
            lines.sort(key=lambda ln: sum(s["y"] for s in ln) / len(ln))

            # Sort segments left-to-right within each line and produce reading-order characters
            all_chars: List[tuple[str, QRect, int]] = []
            for line in lines:
                line.sort(key=lambda s: s["x"])
                for s_idx, s in enumerate(line):
                    if s_idx > 0:
                        prev_s = line[s_idx - 1]
                        gap = s["x"] - (prev_s["x"] + prev_s["w"])
                        if (
                            gap > 2
                            and not prev_s["text"].endswith(" ")
                            and not s["text"].startswith(" ")
                        ):
                            space_rect = QRect(
                                prev_s["x"] + prev_s["w"], s["y"], gap, s["h"]
                            )
                            all_chars.append((" ", space_rect, -1))
                    for ch, ch_r in s["char_boxes"]:
                        all_chars.append((ch, ch_r, s["seg_idx"]))

            if len(self._char_geometry_cache) > 8:
                self._char_geometry_cache.pop(
                    next(iter(self._char_geometry_cache)), None
                )
            self._char_geometry_cache[cache_key] = all_chars

        all_chars = self._char_geometry_cache[cache_key]
        if not all_chars:
            return [], ""

        def get_closest_char_idx(pos: QPoint) -> Optional[int]:
            if not all_chars:
                return None
            for idx, (_, r, _) in enumerate(all_chars):
                if r.contains(pos):
                    return idx

            px, py = pos.x(), pos.y()
            best_idx = None
            min_dist = float("inf")

            for idx, (_, r, _) in enumerate(all_chars):
                ry = r.center().y()
                rx = r.center().x()
                dy = abs(py - ry)
                dx = abs(px - rx)

                if dy <= r.height() * 0.8:
                    dist = dx + dy * 2.0
                    if dist < min_dist:
                        min_dist = dist
                        best_idx = idx
                elif dy < 35:
                    dist = dx * 1.5 + dy * 6.0
                    if dist < min_dist:
                        min_dist = dist
                        best_idx = idx

            if min_dist > 150:
                return None
            return best_idx

        start_idx = get_closest_char_idx(start_pos)
        end_idx = get_closest_char_idx(end_pos)

        if start_idx is None or end_idx is None:
            return [], ""

        min_idx = min(start_idx, end_idx)
        max_idx = max(start_idx, end_idx)

        selected_rects: List[QRect] = []
        selected_chars: List[str] = []

        current_box: Optional[QRect] = None
        prev_cy: Optional[float] = None

        for i in range(min_idx, max_idx + 1):
            ch, rect, _ = all_chars[i]
            selected_chars.append(ch)

            if current_box is None:
                current_box = QRect(rect)
                prev_cy = rect.center().y()
            else:
                if (
                    abs(rect.center().y() - prev_cy) < rect.height() * 0.5
                    and rect.left() <= current_box.right() + 8
                ):
                    current_box = current_box.united(rect)
                else:
                    selected_rects.append(current_box)
                    current_box = QRect(rect)
                    prev_cy = rect.center().y()

        if current_box is not None:
            selected_rects.append(current_box)

        return selected_rects, "".join(selected_chars)

    def _get_word_at_pos(
        self, page_idx: int, pos: QPoint
    ) -> tuple[List[QRect], str]:
        """Returns the bounding rectangle and text for the single word under pos."""
        widget = self.page_widgets.get(page_idx)
        if not widget:
            return [], ""

        scale = self.calculate_scale()
        logical_w = widget.width()
        logical_h = widget.height()
        rotation = getattr(self, "rotation", 0)
        cache_key = (page_idx, scale, logical_w, logical_h, rotation)

        self._get_linear_text_selection(page_idx, pos, pos)
        all_chars = self._char_geometry_cache.get(cache_key, [])
        if not all_chars:
            return [], ""

        hit_idx = None
        for idx, (_, r, _) in enumerate(all_chars):
            if r.contains(pos):
                hit_idx = idx
                break

        if hit_idx is None:
            px, py = pos.x(), pos.y()
            min_d = float("inf")
            for idx, (_, r, _) in enumerate(all_chars):
                d = abs(px - r.center().x()) + abs(py - r.center().y()) * 2.0
                if d < min_d:
                    min_d = d
                    hit_idx = idx
            if hit_idx is None or min_d > 80:
                return [], ""

        if all_chars[hit_idx][0].isspace():
            return [], ""

        start_i = hit_idx
        while start_i > 0 and not all_chars[start_i - 1][0].isspace():
            curr_r = all_chars[start_i][1]
            prev_r = all_chars[start_i - 1][1]
            vert_gap = abs(curr_r.center().y() - prev_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                break
            horiz_gap = curr_r.left() - prev_r.right()
            if horiz_gap > max(8.0, curr_r.height() * 0.6):
                break
            start_i -= 1

        end_i = hit_idx
        while end_i < len(all_chars) - 1 and not all_chars[end_i + 1][0].isspace():
            curr_r = all_chars[end_i][1]
            next_r = all_chars[end_i + 1][1]
            vert_gap = abs(next_r.center().y() - curr_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                break
            horiz_gap = next_r.left() - curr_r.right()
            if horiz_gap > max(8.0, curr_r.height() * 0.6):
                break
            end_i += 1

        word_rect = all_chars[start_i][1]
        word_chars = []
        for i in range(start_i, end_i + 1):
            ch, r, _ = all_chars[i]
            word_chars.append(ch)
            word_rect = word_rect.united(r)

        return [word_rect], "".join(word_chars)

    def _get_paragraph_at_pos(
        self, page_idx: int, pos: QPoint
    ) -> tuple[List[QRect], str]:
        """
        Returns the bounding rectangles and text for the entire sentence/paragraph under pos.
        Selection extends until a line break or horizontal whitespace is encountered.
        """
        widget = self.page_widgets.get(page_idx)
        if not widget:
            return [], ""

        scale = self.calculate_scale()
        logical_w = widget.width()
        logical_h = widget.height()
        rotation = getattr(self, "rotation", 0)
        cache_key = (page_idx, scale, logical_w, logical_h, rotation)

        self._get_linear_text_selection(page_idx, pos, pos)
        all_chars = self._char_geometry_cache.get(cache_key, [])
        if not all_chars:
            return [], ""

        hit_idx = None
        for idx, (_, r, _) in enumerate(all_chars):
            if r.contains(pos):
                hit_idx = idx
                break

        if hit_idx is None:
            px, py = pos.x(), pos.y()
            min_d = float("inf")
            for idx, (_, r, _) in enumerate(all_chars):
                d = abs(px - r.center().x()) + abs(py - r.center().y()) * 2.0
                if d < min_d:
                    min_d = d
                    hit_idx = idx
            if hit_idx is None or min_d > 80:
                return [], ""

        start_i = hit_idx
        while start_i > 0:
            curr_r = all_chars[start_i][1]
            prev_r = all_chars[start_i - 1][1]
            vert_gap = abs(curr_r.center().y() - prev_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                # Line break encountered
                break
            horiz_gap = curr_r.left() - prev_r.right()
            if horiz_gap > max(20.0, curr_r.height() * 1.5):
                # Significant horizontal whitespace encountered
                break
            start_i -= 1

        end_i = hit_idx
        while end_i < len(all_chars) - 1:
            curr_r = all_chars[end_i][1]
            next_r = all_chars[end_i + 1][1]
            vert_gap = abs(next_r.center().y() - curr_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                # Line break encountered
                break
            horiz_gap = next_r.left() - curr_r.right()
            if horiz_gap > max(20.0, curr_r.height() * 1.5):
                # Significant horizontal whitespace encountered
                break
            end_i += 1

        while start_i < end_i and all_chars[start_i][0].isspace():
            start_i += 1
        while end_i > start_i and all_chars[end_i][0].isspace():
            end_i -= 1

        selected_rects: List[QRect] = []
        selected_chars: List[str] = []
        current_box: Optional[QRect] = None

        for i in range(start_i, end_i + 1):
            ch, rect, _ = all_chars[i]
            selected_chars.append(ch)
            if current_box is None:
                current_box = QRect(rect)
            else:
                current_box = current_box.united(rect)

        if current_box is not None:
            selected_rects.append(current_box)

        return selected_rects, "".join(selected_chars)

    def _search_web_for_selected_text(self, text: str) -> None:
        """
        Formats a Google search URL for the selected text and opens it
        in the application's internal browser.

        Args:
            text (str): The selected text string to search for.
        """
        query = urllib.parse.quote_plus(text.strip())
        url_string = f"https://www.google.com/search?q={query}"

        if hasattr(self, "window") and hasattr(self.window(), "new_browser_tab"):
            self.window().new_browser_tab(url_string)
        else:
            QDesktopServices.openUrl(QUrl(url_string))

    def focusInEvent(self, event: Any) -> None:
        """
        Handles the event when the ReaderTab widget itself receives focus from the OS.
        Immediately forwards focus to the appropriate viewing canvas.
        """
        if getattr(self, "view_mode", None) == ViewMode.REFLOW:
            if hasattr(self, "web"):
                self.web.setFocus()
        else:
            if hasattr(self, "scroll"):
                self.scroll.setFocus()

        super().focusInEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        """
        Intercepts state changes, such as window activation from alt-tabbing,
        to ensure keyboard focus is restored to the correct canvas.
        """
        super().changeEvent(event)

        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            focus_widget = QApplication.focusWidget()
            if not isinstance(focus_widget, (QLineEdit, QComboBox)):
                if self.view_mode == ViewMode.REFLOW:
                    if hasattr(self, "web"):
                        self.web.setFocus()
                else:
                    self.setFocus()

    def focusNextPrevChild(self, next: bool) -> bool:
        """
        Preserves default Qt focus chaining without advancing PDF pages unexpectedly.
        """
        return super().focusNextPrevChild(next)

    def _get_or_create_web_view(self) -> QWebEngineView:
        """Instantiates the Chromium view only when actively needed."""
        if not hasattr(self, "web"):
            self.web = QWebEngineView()
            self.web.installEventFilter(self)
            self.stack.removeWidget(self._web_placeholder)
            self.stack.insertWidget(1, self.web)
            self.web.page().setBackgroundColor(
                QColor(30, 30, 30)
                if getattr(self, "theme_mode", 0) != 0
                else QColor(240, 240, 240)
            )
        return self.web

    def print_document(self) -> None:
        """
        Opens the system print dialog and prints the current document.
        Handles both REFLOW (WebEngine) and IMAGE (PDF) view modes.
        """
        if not self.current_path and self.view_mode != ViewMode.REFLOW:
            self.show_toast("No document to print.")
            return

        printer = QPrinter()
        printer.setResolution(300)
        dialog = QPrintDialog(printer, self)

        if dialog.exec() == QPrintDialog.DialogCode.Accepted:
            self.show_toast("Preparing print job...")

            if self.view_mode == ViewMode.REFLOW:
                self.web.page().print(
                    printer,
                    lambda success: self.show_toast(
                        "Print complete!" if success else "Print failed."
                    ),
                )
                return

            if self.current_doc:
                num_pages = self.current_doc.page_count
                progress = QProgressDialog(
                    "Preparing print job...", "Cancel", 0, num_pages, self
                )
                progress.setWindowTitle("Printing PDF")
                progress.setWindowModality(Qt.WindowModality.WindowModal)
                progress.setMinimumDuration(0)
                painter = QPainter()
                if painter.begin(printer):
                    try:
                        for page_idx in range(num_pages):
                            QApplication.processEvents()
                            if progress.wasCanceled():
                                printer.abort()
                                self.show_toast("Printing canceled.")
                                break

                            progress.setLabelText(
                                f"Rendering page {page_idx + 1} of {num_pages}..."
                            )
                            progress.setValue(page_idx)
                            if page_idx > 0:
                                printer.newPage()

                            res = self.current_doc.render_page(
                                page_idx, 2.5, self.theme_mode
                            )

                            if res and res.data:
                                img = QImage(
                                    res.data,
                                    res.width,
                                    res.height,
                                    QImage.Format.Format_RGB32,
                                )
                                pixmap = QPixmap.fromImage(img)
                                rect = printer.pageRect(QPrinter.Unit.DevicePixel)
                                print_w = int(rect.width())
                                print_h = int(rect.height())

                                scaled_pixmap = pixmap.scaled(
                                    print_w,
                                    print_h,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.FastTransformation,
                                )

                                x = int((rect.width() - scaled_pixmap.width()) / 2)
                                y = int((rect.height() - scaled_pixmap.height()) / 2)

                                painter.drawPixmap(x, y, scaled_pixmap)

                        if not progress.wasCanceled():
                            progress.setValue(num_pages)
                            self.show_toast("Print complete!")
                        else:
                            raise Exception

                    except Exception as e:
                        self.show_toast(f"Print failed: {str(e)}")
                        print(e)
                    finally:
                        painter.end()

    def toggle_autoscroll(self, direction: int) -> None:
        """Toggles the document auto-scrolling state and direction."""
        self.autoscroll_dir = direction

        if direction == 1:
            self.btn_autoscroll_up.setChecked(False)
            if self.btn_autoscroll_down.isChecked():
                self.autoscroll_timer.start()
            else:
                self.autoscroll_timer.stop()
        else:
            self.btn_autoscroll_down.setChecked(False)
            if self.btn_autoscroll_up.isChecked():
                self.autoscroll_timer.start()
            else:
                self.autoscroll_timer.stop()

    def _do_autoscroll(self) -> None:
        """Executes the incremental auto-scroll tick."""
        vbar = self.scroll.verticalScrollBar()
        speed = self.settings.value("app/autoscroll_speed", 5, type=int)
        new_val = vbar.value() + (speed * getattr(self, "autoscroll_dir", 1))

        if new_val >= vbar.maximum() and getattr(self, "autoscroll_dir", 1) == 1:
            vbar.setValue(vbar.maximum())
            self.btn_autoscroll_down.setChecked(False)
            self.autoscroll_timer.stop()

        elif new_val <= 0 and getattr(self, "autoscroll_dir", 1) == -1:
            vbar.setValue(0)
            self.btn_autoscroll_up.setChecked(False)
            self.autoscroll_timer.stop()

        else:
            vbar.setValue(new_val)

    def _get_icon(self, filename: str) -> QIcon:
        """
        Resolves the appropriate SVG or PNG asset path to construct an icon corresponding to the active theme mode.

        Args:
            filename (str): The base filename of the requested icon.

        Returns:
            QIcon: The instantiated Qt icon mapping to the internal resource.
        """
        is_dark = getattr(self, "theme_mode", 0) != 0
        if (
            is_dark
            and filename.endswith(".svg")
            and not filename.endswith("-white.svg")
        ):
            filename = filename.replace(".svg", "-white.svg")

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_path = getattr(sys, "_MEIPASS")
            path = os.path.join(base_path, "riemann", "assets", "icons", filename)
        else:
            base_path = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            path = os.path.join(base_path, "assets", "icons", filename)

        if not os.path.exists(path) and "-white.svg" in filename:
            path = path.replace("-white.svg", ".svg")

        return QIcon(path)

    def _update_icons(self) -> None:
        """Refreshes all icons dynamically when the theme changes."""
        self.btn_save.setIcon(self._get_icon("save.svg"))
        self.btn_rename.setIcon(self._get_icon("rename.svg"))
        self.btn_export.setIcon(self._get_icon("file-output.svg"))
        self.btn_open_external.setIcon(self._get_icon("airplay.svg"))
        self.btn_print.setIcon(self._get_icon("printer.svg"))

        self.btn_cite.setIcon(self._get_icon("text-quote.svg"))
        self.btn_rotate.setIcon(self._get_icon("rotate-cw.svg"))
        self.btn_rotate_ccw.setIcon(self._get_icon("rotate-ccw.svg"))
        self.btn_reflow.setIcon(self._get_icon("file-text.svg"))
        self.btn_facing.setIcon(self._get_icon("book-open.svg"))

        self.btn_scroll_mode.setIcon(self._get_icon("scroll.svg"))
        self.btn_annotate.setIcon(self._get_icon("pen-line.svg"))
        self.btn_snip.setIcon(self._get_icon("crop.svg"))
        self.btn_prev.setIcon(self._get_icon("chevron-left.svg"))
        self.btn_next.setIcon(self._get_icon("chevron-right.svg"))

        if hasattr(self, "btn_zoom_out") or hasattr(self, "btn_zoom_in"):
            self.btn_zoom_out.setIcon(self._get_icon("minus.svg"))
            self.btn_zoom_in.setIcon(self._get_icon("plus.svg"))

        self.btn_autoscroll_up.setIcon(self._get_icon("move-up.svg"))
        self.btn_autoscroll_down.setIcon(self._get_icon("move-down.svg"))

        if getattr(self, "theme_mode", 0) == 0:
            icon = "sun.svg"
        elif getattr(self, "theme_mode", 0) == 1:
            icon = "moon.svg"
        else:
            icon = "sun-moon.svg"
        self.btn_theme.setIcon(self._get_icon(icon))

        icons = {0: "panel-top.svg", 1: "maximize.svg", 2: "minimize.svg"}
        self.btn_fullscreen.setIcon(
            self._get_icon(
                icons.get(
                    getattr(self.window(), "_fullscreen_state", 0), "maximize.svg"
                )
            )
        )

        self.btn_ocr.setIcon(self._get_icon("scan-text.svg"))
        self.btn_search.setIcon(self._get_icon("search.svg"))
        self.btn_ai_search.setIcon(self._get_icon("sparkles.svg"))
        self.btn_secure_export.setIcon(self._get_icon("file-lock.svg"))
        self.btn_sign.setIcon(self._get_icon("signature.svg"))

        self.btn_find_prev.setIcon(self._get_icon("chevron-up.svg"))
        self.btn_find_next.setIcon(self._get_icon("chevron-down.svg"))
        self.btn_close_search.setIcon(self._get_icon("x.svg"))

        self.btn_ai_find.setIcon(self._get_icon("sparkles.svg"))
        self.btn_ai_prev.setIcon(self._get_icon("chevron-up.svg"))
        self.btn_ai_next.setIcon(self._get_icon("chevron-down.svg"))
        self.btn_close_ai_search.setIcon(self._get_icon("x.svg"))

        if hasattr(self, "anno_toolbar"):
            self.anno_toolbar._update_icons()

    def cleanup(self) -> None:
        """Explicitly severs references to heavy resources to trigger native destructors."""
        self.scroll_timer.stop()
        self.autoscroll_timer.stop()
        if hasattr(self, "_zoom_debounce_timer"):
            self._zoom_debounce_timer.stop()
        if hasattr(self, "_rebuild_debounce_timer"):
            self._rebuild_debounce_timer.stop()

        if hasattr(self, "load_worker") and self.load_worker:
            try:
                self.load_worker.finished.disconnect()
                self.load_worker.error.disconnect()
            except Exception:
                pass
            self.load_worker.engine = None
            if self.load_worker.isRunning():
                self.load_worker.requestInterruption()
                self.load_worker.quit()

                if not self.load_worker.wait(1000):
                    print("Worker thread stuck! Forcing termination...")
                    self.load_worker.terminate()
                    self.load_worker.wait()

            try:
                self.load_worker.deleteLater()
            except RuntimeError:
                pass
            self.load_worker = None

        self.rendered_pages.clear()
        self.text_segments_cache.clear()
        self.form_values_cache.clear()

        for w in self.page_widgets.values():
            w.clear()
            w.setParent(None)
            if hasattr(self, "scroll_layout"):
                self.scroll_layout.removeWidget(w)
            w.deleteLater()
        self.page_widgets.clear()

        for widgets_list in self.form_widgets.values():
            for fw in widgets_list:
                fw.deleteLater()
        self.form_widgets.clear()

        if hasattr(self, "current_doc") and self.current_doc:
            try:
                self.current_doc.close()
            except Exception:
                pass

        self.current_doc = None
        self.engine = None

        if hasattr(self, "web"):
            try:
                if shiboken6.isValid(self.web):
                    self.web.stop()
                    page = self.web.page()
                    if page and shiboken6.isValid(page):
                        profile = page.profile()
                        if profile:
                            profile.clearHttpCache()
                        page.deleteLater()

                    self.web.setHtml("")
                    self.web.setParent(None)
                    self.web.deleteLater()
            except RuntimeError:
                pass

            self.web = None

        if hasattr(self, "_kill_ai_engine"):
            self._kill_ai_engine()

        if hasattr(self, "window") and hasattr(self.window(), "_caption_worker"):
            worker = self.window()._caption_worker
            if worker and worker.isRunning():
                worker.requestInterruption()
                worker.quit()
                worker.wait(1000)

        QPixmapCache.clear()
        QApplication.processEvents()
        gc.collect()


class PreviewReaderTab(ReaderTab):
    """
    A lightweight, read-only PDF viewer for the Explorer preview.
    Inherits core rendering from ReaderTab but disables all heavy UI/formatting.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_preview = True
        self.current_path = ""

        if hasattr(self, "toolbar") and self.toolbar:
            self.toolbar.setVisible(False)
        if hasattr(self, "anno_toolbar") and self.anno_toolbar:
            self.anno_toolbar.setVisible(False)
        if hasattr(self, "search_bar") and self.search_bar:
            self.search_bar.setVisible(False)
        if hasattr(self, "ai_search_bar") and self.ai_search_bar:
            self.ai_search_bar.setVisible(False)
        if hasattr(self, "signature_banner") and self.signature_banner:
            self.signature_banner.setVisible(False)

        self._ignore_scroll = True
        self.scroll.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical { width: 8px; background: transparent; }"
        )

    def load_document(
        self,
        path: str,
        restore_state: bool = False,
        password: Optional[str] = None,
        is_retry: bool = False,
    ) -> None:
        self.current_path = path
        super().load_document(path, restore_state, password, is_retry)

    def cleanup(self):
        """Ensures PDFium background threads are killed when the preview is replaced."""
        if hasattr(self, "close_document"):
            self.close_document()
