"""
Reader Tab Component.

The main aggregator class that combines all mixins to provide
the full PDF reading experience.
"""

import os
import shutil
import sys
import urllib.parse
from math import inf
from typing import Any, Dict, List, Optional, Set, Tuple

import pikepdf
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
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
    QStackedWidget,
    QTabWidget,
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
from .widgets import PageWidget

try:
    import riemann_core
except ImportError as e:
    print(f"CRITICAL: Could not import riemann_core backend.\nError: {e}")
    sys.exit(1)


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

        self.engine: Optional[riemann_core.PdfEngine] = None
        self.current_doc: Optional[riemann_core.RiemannDocument] = None
        self.current_path: Optional[str] = None
        self.current_page_index: int = 0

        self.theme_mode: int = self.settings.value("themeMode", 0, type=int)
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

    def _setup_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        """
        Allocates interactive push buttons resolving respective execution slot relationships natively.

        Args:
            layout (QHBoxLayout): Reference pointer tracking parent bounding alignments systematically.
        """
        self.btn_save = QPushButton("💾")
        self.btn_save.setToolTip("Save Copy of PDF")
        self.btn_save.clicked.connect(self.save_document)

        self.btn_rename = QPushButton("🏷️")
        self.btn_rename.setToolTip("Auto-Rename File using Metadata")
        self.btn_rename.clicked.connect(self.rename_current_pdf)

        self.btn_export = QPushButton("📤")
        self.btn_export.setToolTip("Export Annotations to Markdown")
        self.btn_export.clicked.connect(self.export_annotations)

        self.btn_print = QPushButton("🖨️")
        self.btn_print.setToolTip("Print Document (Ctrl+P)")
        self.btn_print.clicked.connect(self.print_document)

        self.btn_cite = QPushButton("📑")
        self.btn_cite.setToolTip("Copy BibTeX Citation")
        self.btn_cite.clicked.connect(self.copy_citation)

        self.btn_rotate = QPushButton("↻")
        self.btn_rotate.setToolTip("Rotate PDF 90°")
        self.btn_rotate.clicked.connect(self.rotate_document)

        self.btn_rotate_ccw = QPushButton("↺")
        self.btn_rotate_ccw.setToolTip("Rotate PDF -90°")
        self.btn_rotate_ccw.clicked.connect(self.rotate_document_ccw)

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
        self.combo_zoom.setFixedWidth(120)

        self.btn_theme = QPushButton("🌓")
        self.btn_theme.setToolTip("Cycle Theme (Light / Fast Dark / Smart Dark)")
        self.btn_theme.clicked.connect(self.toggle_theme)

        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.clicked.connect(self.toggle_reader_fullscreen)

        self.btn_ocr = QPushButton("👁️")
        self.btn_ocr.setToolTip("OCR Current Page")
        self.btn_ocr.clicked.connect(self.perform_ocr_current_page)

        self.btn_search = QPushButton("🔍")
        self.btn_search.setCheckable(True)
        self.btn_search.clicked.connect(self.toggle_search_bar)

        self.btn_ai_search = QPushButton("✨")
        self.btn_ai_search.setToolTip("AI Semantic Search (Ctrl+I)")
        self.btn_ai_search.setCheckable(True)
        self.btn_ai_search.setStyleSheet(
            "color: #9b59b6; font-weight: bold; font-size: 16px;"
        )
        self.btn_ai_search.clicked.connect(self.toggle_ai_search_bar)

        self.btn_secure_export = QPushButton("🔒 Lock | Save")
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

        self.btn_sign = QPushButton("🖋️")
        self.btn_sign.setToolTip("Sign Document (PKCS#12)")
        self.btn_sign.clicked.connect(self.initiate_signing_flow)

        widgets = [
            self.btn_save,
            self.btn_print,
            self.btn_rename,
            self.btn_export,
            self.btn_sign,
            self.btn_rotate,
            self.btn_rotate_ccw,
            self.btn_reflow,
            self.btn_facing,
            self.btn_scroll_mode,
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
            self.combo_zoom,
            self.btn_theme,
            self.btn_fullscreen,
            self.btn_secure_export,
        ]
        for w in widgets:
            layout.addWidget(w)

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

        self.btn_ai_find = QPushButton("Ask AI ✨")
        self.btn_ai_find.setStyleSheet(
            "background-color: #7b4bce; color: white; border-radius: 4px; padding: 4px 10px;"
        )
        self.btn_ai_find.clicked.connect(
            lambda: self.ai_search(self.txt_ai_search.text())
        )

        self.btn_ai_prev = QPushButton("▲")
        self.btn_ai_prev.setStyleSheet("color: #e6d0ff; font-weight: bold;")
        self.btn_ai_prev.clicked.connect(self.ai_find_prev)

        self.btn_ai_next = QPushButton("▼")
        self.btn_ai_next.setStyleSheet("color: #e6d0ff; font-weight: bold;")
        self.btn_ai_next.clicked.connect(self.ai_find_next)

        self.btn_close_ai_search = QPushButton("✕")
        self.btn_close_ai_search.setFlat(True)
        self.btn_close_ai_search.setStyleSheet("color: #e6d0ff; font-weight: bold;")
        self.btn_close_ai_search.clicked.connect(self.toggle_ai_search_bar)

        sb_layout.addWidget(QLabel("✨ AI Search:"))
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
            for path in recent_pdfs[:10]:
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

        Args:
            path (str): Full validated filesystem pathway containing data.
            restore_state (bool): Instruction dictating utilization previously saved user coordinates locally stored. Defaults to False.
            password (Optional[str]): String checking presence/absence of password protection in currently open file. Defaults to None.
        """
        if path.lower().endswith(".md"):
            self._load_markdown(path)
            return

        try:
            self.current_doc = self.engine.load_document(path, password)
            self._probe_base_page_size()
            self.current_path = path
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
            QTimer.singleShot(500, lambda: self._detect_signatures(path))
            QTimer.singleShot(1000, self.index_pdf_for_ai)

            if restore_state:
                saved_page = self.settings.value("lastPage", 0, type=int)
                saved_scroll = self.settings.value("lastScrollY", 0, type=int)
                self.current_page_index = min(
                    saved_page, self.current_doc.page_count - 1
                )
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

        except Exception as e:
            err_str = str(e).lower()
            if (
                "password" in err_str
                or "encrypted" in err_str
                or "format error" in err_str
                or "error" in err_str
            ):
                error_text = (
                    "Incorrect password. Please try again." if is_retry else None
                )
                dialog = PasswordDialog(self, error_msg=error_text)
                if dialog.exec():
                    pw = dialog.get_password()
                    if pw:
                        self.load_document(path, restore_state, pw, is_retry=True)
                return

            sys.stderr.write(f"Load error: {e}\n")

    def _load_markdown(self, path: str) -> None:
        """
        Compiles raw markdown syntax representations generating reflow HTML internally displayed within WebEngine contexts.

        Args:
            path (str): Reference string accessing unformatted document text structurally.
        """
        self.current_path = path
        self.settings.setValue("lastFile", path)
        self._update_tab_title(os.path.basename(path))

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            full_html = generate_markdown_html(text, self.theme_mode != 0)
            web_view = self._get_or_create_web_view()
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
        """
        Traverses deeply nested annotation JSON layouts outputting clean markdown textual variants suitable for academic review.
        """
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
        """
        Assigns physics-based smooth tracking variables mimicking native touch interactions predictably gracefully.
        """
        QScroller.grabGesture(
            self.scroll.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        props = QScroller.scroller(self.scroll.viewport()).scrollerProperties()
        props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.5)
        props.setScrollMetric(QScrollerProperties.ScrollMetric.MaximumVelocity, 0.8)
        QScroller.scroller(self.scroll.viewport()).setScrollerProperties(props)

    def _get_closest_page(self, value: int) -> int:
        """Calculates the geometric closest page index dynamically."""
        if not self.current_doc:
            return self.current_page_index

        center = value + (self.scroll.viewport().height() / 2)

        if self._virtual_enabled and self._cached_base_size:
            _, base_h = self._cached_base_size
            ph = int(base_h * self.calculate_scale()) + self.scroll_layout.spacing()
            if ph > 0:
                return min(self.current_doc.page_count - 1, max(0, int(center / ph)))

        closest, min_dist = self.current_page_index, inf
        for idx, widget in self.page_widgets.items():
            try:
                row_widget = widget.parentWidget()
                if not row_widget:
                    continue
                w_center = row_widget.pos().y() + (widget.height() / 2)
                dist = abs(w_center - center)
                if dist < min_dist:
                    min_dist = dist
                    closest = idx
            except Exception:
                continue
        return closest

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

        if closest != self.current_page_index:
            self.current_page_index = closest
            if self.current_doc:
                self.txt_page.setText(str(closest + 1))

        if self._virtual_enabled:
            s, e = self._virtual_range
            count = self.current_doc.page_count
            if (self.current_page_index > e - 10 and e < count) or (
                self.current_page_index < s + 10 and s > 0
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
            y = top + max(0, index - start) * ph
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

    def toggle_view_mode(self) -> None:
        """
        Transitions viewing context switching image pipelines converting explicitly formatted HTML representations dynamically.
        """
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        if self.view_mode == ViewMode.REFLOW:
            self._get_or_create_web_view()
        self.stack.setCurrentIndex(1 if self.view_mode == ViewMode.REFLOW else 0)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)
        self.update_view()

    def toggle_facing_mode(self) -> None:
        """
        Swaps sequential presentation layouts utilizing two column grids dynamically tracking states internally consistently.
        """
        self.facing_mode = not self.facing_mode
        self.settings.setValue("facingMode", self.facing_mode)
        self.btn_facing.setChecked(self.facing_mode)
        self.rebuild_layout()
        self.update_view()

    def toggle_scroll_mode(self) -> None:
        """
        Updates persistent UI paradigms navigating pages natively using continuous vs locked configurations logically handled.
        """
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScrollMode", self.continuous_scroll)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)
        self.rebuild_layout()
        self.update_view()

    def toggle_reader_fullscreen(self) -> None:
        """
        Issues commands modifying native application sizing behaviors matching global full-screen modes natively resolving references.
        """
        from ...app import RiemannWindow

        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def open_pdf_dialog(self) -> None:
        """
        Surfaces interactive system menus prompting selection processes loading file responses effectively mapping input data correctly.
        """
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF (*.pdf)")
        if path:
            self.load_document(path)

    def scroll_page(self, direction: int) -> None:
        """
        Pushes view states scaling exactly viewport boundaries dynamically allowing fast navigation sequences reliably.

        Args:
            direction (int): Value resolving numerical step direction logic natively.
        """
        bar = self.scroll.verticalScrollBar()
        step = self.scroll.viewport().height() * 0.9
        bar.setValue(bar.value() + (direction * step))

    def on_page_input_return(self) -> None:
        """
        Parses manually edited textbox values verifying mathematical limits mapping results cleanly rebuilding bounds dynamically.
        """
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
                        rx, ry = self._map_to_unrotated(
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        self.create_new_annotation(page_idx, rx, ry)
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

            elif (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                source.setFocus()
                self.is_selecting_text = True
                self.text_select_start = event.pos()
                source.set_text_selection([])
                self.current_selected_text = ""
                return True

            elif event.type() == QEvent.Type.MouseMove and getattr(
                self, "is_selecting_text", False
            ):
                drag_rect = QRect(self.text_select_start, event.pos()).normalized()
                rects, text = self._get_intersecting_text_data(page_idx, drag_rect)
                source.set_text_selection(rects)
                self.current_selected_text = text
                return True

            elif event.type() == QEvent.Type.MouseButtonRelease and getattr(
                self, "is_selecting_text", False
            ):
                self.is_selecting_text = False
                drag_rect = QRect(self.text_select_start, event.pos()).normalized()
                rects, text = self._get_intersecting_text_data(page_idx, drag_rect)
                source.set_text_selection(rects)
                self.current_selected_text = text
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

    def event(self, event: QEvent) -> bool:
        """
        Intercepts raw system events to support native gestures like pinch-to-zoom on trackpads.
        """
        if event.type() == QEvent.Type.NativeGesture:
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                delta = event.value()
                self.manual_scale = max(
                    0.1, min(self.manual_scale * (1.0 + delta), 5.0)
                )
                self.zoom_mode = ZoomMode.MANUAL

                if not hasattr(self, "_zoom_debounce_timer"):
                    self._zoom_debounce_timer = QTimer(self)
                    self._zoom_debounce_timer.setSingleShot(True)
                    self._zoom_debounce_timer.setInterval(100)
                    self._zoom_debounce_timer.timeout.connect(
                        self.on_zoom_changed_internal
                    )

                self._zoom_debounce_timer.start()
                return True
        return super().event(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """
        Manages rotational pointer input updating zoom calculations effectively bypassing default scrolling natively securely actively.

        Args:
            event (QWheelEvent): Complex parameter detailing positional offsets dynamically tracked explicitly locally reliably.
        """
        mod = event.modifiers()

        # if mod & Qt.KeyboardModifier.ControlModifier:
        #     delta = event.angleDelta().y()
        #     if delta != 0:
        #         factor = 1.0 + (delta / 1200.0)
        #         self.manual_scale = max(0.1, min(self.manual_scale * factor, 5.0))
        #         self.zoom_mode = ZoomMode.MANUAL

        #         if not hasattr(self, "_zoom_debounce_timer"):
        #             self._zoom_debounce_timer = QTimer(self)
        #             self._zoom_debounce_timer.setSingleShot(True)
        #             self._zoom_debounce_timer.setInterval(100)
        #             self._zoom_debounce_timer.timeout.connect(
        #                 self.on_zoom_changed_internal
        #             )

        #         self._zoom_debounce_timer.start()
        #     event.accept()
        #     return

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

    def on_zoom_changed_internal(self) -> None:
        """
        Executes unified internal state rebuild updating explicit dimension mappings enforcing redrawing completely robustly efficiently safely natively.
        """
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)
        self._update_all_widget_sizes()
        self.rebuild_layout()
        self.rendered_pages.clear()
        self.update_view()

        if self.zoom_mode == ZoomMode.AUTO_FIT:
            txt = "Auto Fit"
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            txt = "Fit Width"
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            txt = "Fit Height"
        else:
            txt = f"{int(self.manual_scale * 100)}%"
        self.combo_zoom.setCurrentText(txt)

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

        bg_scroll = "#222" if is_dark else "#eee"
        self.scroll_content.setStyleSheet(
            f"#scrollContent {{ background-color: {bg_scroll}; }}"
        )

        fg = "#ddd" if is_dark else "#111"

        checked_bg = "rgba(60, 140, 255, 0.3)" if is_dark else "rgba(0, 100, 255, 0.2)"
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

        sb_bg = "#2a2a2a" if is_dark else "#e0e0e0"
        sb_fg = "#ddd" if is_dark else "#111"
        input_bg = "#1e1e1e" if is_dark else "#ffffff"
        input_border = "#555" if is_dark else "#bbb"

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
        """
        Cycles configuration properties through Light, Fast Dark, and Smart Dark modes.
        Saves preferences globally and triggers visual reconstructions safely.
        """
        self.theme_mode = (self.theme_mode + 1) % 3
        self.settings.setValue("themeMode", self.theme_mode)
        self.apply_theme()
        self.rebuild_layout()
        self.rendered_pages.clear()
        self.update_view()

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

        right_layout.addWidget(recent_label)
        right_layout.addWidget(self.list_recent)

        layout.addStretch(1)
        layout.addLayout(left_layout, 2)
        layout.addStretch(1)
        layout.addLayout(right_layout, 2)
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

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Encrypted PDF",
            self.current_path.replace(".pdf", "_secure.pdf"),
            "PDF Files (*.pdf)",
        )

        if not save_path:
            return

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

    def _get_intersecting_text_data(
        self, page_idx: int, drag_rect: QRect
    ) -> tuple[List[QRect], str]:
        """
        Calculates character-level intersections with the drag rectangle and extracts the precise text.

        Args:
            page_idx (int): The index of the currently processed document page.
            drag_rect (QRect): The user's selection drag bounding box.

        Returns:
            tuple[List[QRect], str]: A tuple containing the list of character bounding boxes
                                     and the concatenated string of selected text.
        """
        if not drag_rect or drag_rect.isEmpty():
            return [], ""

        scale = self.calculate_scale()
        base_w, base_h = (
            self._cached_base_size if self._cached_base_size else (595, 842)
        )
        rotation = getattr(self, "rotation", 0)

        if rotation in (90, 270):
            logical_w, logical_h = base_h * scale, base_w * scale
        else:
            logical_w, logical_h = base_w * scale, base_h * scale

        if page_idx not in self.text_segments_cache:
            self.text_segments_cache[page_idx] = self.current_doc.get_text_segments(
                page_idx
            )

        segments = self.text_segments_cache[page_idx]
        intersecting_rects = []
        selected_text_pieces = []

        for text, (l, t, r, b) in segments:
            char_count = len(text)
            if char_count == 0:
                continue

            char_w = (r - l) / char_count
            segment_chars = []

            for i, char in enumerate(text):
                char_l = l + i * char_w
                char_r = char_l + char_w

                x = int(char_l * scale)
                w_rect = int((char_r - char_l) * scale)
                h_rect = int((t - b) * scale)
                y = int(logical_h - (t * scale))

                if h_rect < 0:
                    y += h_rect
                    h_rect = abs(h_rect)

                if rotation == 90:
                    x, y = int(logical_h) - y - h_rect, x
                    w_rect, h_rect = h_rect, w_rect
                elif rotation == 180:
                    x, y = int(logical_w) - x - w_rect, int(logical_h) - y - h_rect
                elif rotation == 270:
                    x, y = y, int(logical_w) - x - w_rect
                    w_rect, h_rect = h_rect, w_rect

                char_rect = QRect(x, y, max(1, w_rect), h_rect)

                if drag_rect.intersects(char_rect):
                    intersecting_rects.append(char_rect)
                    segment_chars.append(char)

            if segment_chars:
                selected_text_pieces.append("".join(segment_chars))

        return intersecting_rects, " ".join(selected_text_pieces)

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

    def _get_or_create_web_view(self) -> QWebEngineView:
        """Instantiates the Chromium view only when actively needed."""
        if not hasattr(self, "web"):
            self.web = QWebEngineView()
            self.web.installEventFilter(self)
            self.stack.removeWidget(self._web_placeholder)
            self.stack.insertWidget(1, self.web)
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
