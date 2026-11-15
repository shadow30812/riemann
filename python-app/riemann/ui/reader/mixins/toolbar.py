"""
The reader's toolbar, search bars, sidebar toggle, scroll area and icons.

Part of ReaderTab (riemann/ui/reader/tab.py). ReaderTab lists this mixin before
QWidget, so its Qt event handlers override QWidget's.
"""

import os
import sys
from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QCursor,
    QIcon,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from ...components import AnnotationToolbar, build_open_with_menu
from ..sidebar import PdfSidebarWidget
from ..toolbar_layout import ToolbarLayout
from ..notes import NotesPanel
from .host import ReaderHostBase


class ToolbarMixin(ReaderHostBase):
    anno_toolbar = None

    def setup_ui(self) -> None:
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

        bar_rows = QVBoxLayout(self.toolbar)
        bar_rows.setContentsMargins(0, 0, 0, 0)
        bar_rows.setSpacing(0)
        self._setup_toolbar_buttons()
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
        self.stack.addWidget(self.scroll)

        self._web_placeholder = QWidget()
        self.stack.addWidget(self._web_placeholder)

        self._setup_home_page()
        self.stack.addWidget(self.home_page_widget)

        self.sidebar_nav = PdfSidebarWidget(self, dark_mode=self._home_dark)
        self.sidebar_nav.set_mode(self.settings.value("reader/sidebar_mode", "outline", type=str))
        self.sidebar_nav.page_selected.connect(self.navigate_to)
        self.sidebar_nav.pages_menu_requested.connect(self.show_pages_menu)
        self.sidebar_nav.btn_thumbs.clicked.connect(self._remember_sidebar_state)
        self.sidebar_nav.btn_outline.clicked.connect(self._remember_sidebar_state)
        self.sidebar_nav.hide()

        self.content_container = QWidget()
        c_layout = QHBoxLayout(self.content_container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(0)
        c_layout.addWidget(self.sidebar_nav)
        c_layout.addWidget(self.stack, 1)
        self.notes_panel = NotesPanel()
        self.notes_panel.setFixedWidth(340)
        self.notes_panel.page_provider = lambda: self.current_page_index
        self.notes_panel.page_link_clicked.connect(self.navigate_to)
        self.notes_panel.web_link_clicked.connect(self._open_link_in_browser)
        self.notes_panel.apply_theme(self._home_dark)
        self.notes_panel.hide()
        c_layout.addWidget(self.notes_panel)
        self._setup_citation_card()
        layout.addWidget(self.content_container, 1)
        self.toolbar_layout = ToolbarLayout(self)
        self.toolbar_layout.apply()

        if not self.current_path:
            self.stack.setCurrentIndex(2)
            self.toolbar.hide()

        for widget_class in (QPushButton, QToolButton, QComboBox):
            for w in self.findChildren(widget_class):
                w.setCursor(Qt.CursorShape.PointingHandCursor)

        self.link_tooltip = QLabel(self)
        self.link_tooltip.hide()

    def _setup_toolbar_buttons(self) -> None:
        """Creates the toolbar's buttons; ToolbarLayout (set up in setup_ui) places them."""
        icon_size = QSize(20, 20)

        self.btn_toggle_sidebar = QPushButton()
        self.btn_toggle_sidebar.setIcon(self._get_icon("panel-left.svg"))
        self.btn_toggle_sidebar.setIconSize(icon_size)
        self.btn_toggle_sidebar.setToolTip("Sections and Thumbnails (Ctrl+Alt+S)")
        self.btn_toggle_sidebar.setCheckable(True)
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar)

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

        self.btn_read_aloud = QPushButton()
        self.btn_read_aloud.setCheckable(True)
        self.btn_read_aloud.setIcon(self._get_icon("volume-on.svg"))
        self.btn_read_aloud.setIconSize(icon_size)
        self.btn_read_aloud.setToolTip("Read aloud from this page (Ctrl+Shift+U)")
        self.btn_read_aloud.clicked.connect(self.toggle_read_aloud)

        self.btn_notes = QPushButton()
        self.btn_notes.setCheckable(True)
        self.btn_notes.setIcon(self._get_icon("sticky-note.svg"))
        self.btn_notes.setIconSize(icon_size)
        self.btn_notes.setToolTip("Notes on this paper (Ctrl+Alt+N)\nSaved as Markdown next to the PDF.")
        self.btn_notes.clicked.connect(self.toggle_notes)

        self.btn_present = QPushButton()
        self.btn_present.setIcon(self._get_icon("presentation.svg"))
        self.btn_present.setIconSize(icon_size)
        self.btn_present.setToolTip("Present (Shift+F5)\nFull screen, one page at a time. L for a laser pointer, Esc to stop.")
        self.btn_present.clicked.connect(self.start_presentation)

        self.btn_pages = QPushButton()
        self.btn_pages.setIcon(self._get_icon("file.svg"))
        self.btn_pages.setIconSize(icon_size)
        self.btn_pages.setToolTip("Edit pages: rotate, move, delete, extract, insert")
        self.btn_pages.clicked.connect(lambda: self.show_pages_menu())

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

        self.btn_nav_back = QPushButton()
        self.btn_nav_back.setIcon(self._get_icon("circle-arrow-left.svg"))
        self.btn_nav_back.setIconSize(icon_size)
        self.btn_nav_back.setToolTip("Back to where you were (Alt+Left)")
        self.btn_nav_back.clicked.connect(self.go_back)
        self.btn_nav_back.setEnabled(False)
        self.btn_nav_forward = QPushButton()
        self.btn_nav_forward.setIcon(self._get_icon("circle-arrow-right.svg"))
        self.btn_nav_forward.setIconSize(icon_size)
        self.btn_nav_forward.setToolTip("Forward again (Alt+Right)")
        self.btn_nav_forward.clicked.connect(self.go_forward)
        self.btn_nav_forward.setEnabled(False)

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
                "moon.svg" if self.theme_mode == 0 else "sun.svg"
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
        self.btn_ai_search.clicked.connect(self.toggle_ai_search_bar)

        self.btn_secure_export = QPushButton()
        self.btn_secure_export.setIcon(self._get_icon("file-lock.svg"))
        self.btn_secure_export.setIconSize(icon_size)
        self.btn_secure_export.setToolTip(
            "Export a password-protected copy of this PDF (Ctrl+Shift+S)"
        )
        self.btn_secure_export.clicked.connect(self.export_secure_pdf)

        self.btn_sign = QPushButton()
        self.btn_sign.setIcon(self._get_icon("signature.svg"))
        self.btn_sign.setIconSize(icon_size)
        self.btn_sign.setToolTip("Sign Document (PKCS#12)")
        self.btn_sign.clicked.connect(self.initiate_signing_flow)

    def toggle_sidebar(self) -> None:
        """Shows or hides the sections and thumbnails sidebar."""
        if self.is_preview or not self.current_doc:
            return
        self._set_sidebar_visible(not self.sidebar_nav.isVisible())
        self._remember_sidebar_state()

    def _set_sidebar_visible(self, visible: bool) -> None:
        if visible != self.sidebar_nav.isVisible():
            self.sidebar_nav.setVisible(visible)
            self._schedule_fit_refresh()
        self.btn_toggle_sidebar.setChecked(visible)

    def _remember_sidebar_state(self) -> None:
        self.settings.setValue("reader/sidebar_visible", self.sidebar_nav.isVisible())
        self.settings.setValue("reader/sidebar_mode", self.sidebar_nav.mode)

    def _update_page_input_width(self, text: str = "") -> None:
        """Fits the page-number box to its text."""
        if not text:
            text = self.txt_page.text() or "0"

        fm = self.txt_page.fontMetrics()
        width = fm.horizontalAdvance(text) + 40
        self.txt_page.setFixedWidth(max(40, min(width, 80)))

    def _setup_search_bar(self) -> None:
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

        sb_layout = QHBoxLayout(self.ai_search_bar)
        sb_layout.setContentsMargins(10, 5, 10, 5)

        self.txt_ai_search = QLineEdit()
        self.txt_ai_search.setPlaceholderText(
            "Ask AI to find concepts, meanings, or subjects..."
        )
        self.txt_ai_search.returnPressed.connect(
            lambda: self.ai_search(self.txt_ai_search.text())
        )

        self.btn_ai_find = QPushButton("Ask AI")
        self.btn_ai_find.setIcon(self._get_icon("sparkles.svg"))
        self.btn_ai_find.setObjectName("askAi")
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

    def _setup_scroll_area(self) -> None:
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

    def _get_icon(self, filename: str) -> QIcon:
        is_dark = self.theme_mode != 0
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
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            path = os.path.join(base_path, "assets", "icons", filename)

        if not os.path.exists(path) and "-white.svg" in filename:
            path = path.replace("-white.svg", ".svg")

        return QIcon(path)

    def _update_icons(self) -> None:
        self.btn_save.setIcon(self._get_icon("save.svg"))
        self.btn_rename.setIcon(self._get_icon("rename.svg"))
        self.btn_export.setIcon(self._get_icon("file-output.svg"))
        self.btn_open_external.setIcon(self._get_icon("airplay.svg"))
        self.btn_print.setIcon(self._get_icon("printer.svg"))

        self.btn_cite.setIcon(self._get_icon("text-quote.svg"))
        self.btn_rotate.setIcon(self._get_icon("rotate-cw.svg"))
        self.btn_rotate_ccw.setIcon(self._get_icon("rotate-ccw.svg"))
        self.btn_pages.setIcon(self._get_icon("file.svg"))
        self.btn_present.setIcon(self._get_icon("presentation.svg"))
        self.btn_read_aloud.setIcon(self._get_icon("volume-on.svg"))
        self.btn_notes.setIcon(self._get_icon("sticky-note.svg"))
        self.btn_reflow.setIcon(self._get_icon("file-text.svg"))
        self.btn_facing.setIcon(self._get_icon("book-open.svg"))

        self.btn_scroll_mode.setIcon(self._get_icon("scroll.svg"))
        self.btn_annotate.setIcon(self._get_icon("pen-line.svg"))
        self.btn_snip.setIcon(self._get_icon("crop.svg"))
        self.btn_prev.setIcon(self._get_icon("chevron-left.svg"))
        self.btn_next.setIcon(self._get_icon("chevron-right.svg"))
        self.btn_nav_back.setIcon(self._get_icon("circle-arrow-left.svg"))
        self.btn_nav_forward.setIcon(self._get_icon("circle-arrow-right.svg"))

        self.btn_zoom_out.setIcon(self._get_icon("minus.svg"))
        self.btn_zoom_in.setIcon(self._get_icon("plus.svg"))

        self.btn_autoscroll_up.setIcon(self._get_icon("move-up.svg"))
        self.btn_autoscroll_down.setIcon(self._get_icon("move-down.svg"))

        if self.theme_mode == 0:
            icon = "sun.svg"
        elif self.theme_mode == 1:
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

        self.anno_toolbar._update_icons()

    def get_open_with_menu(self, parent_widget=None) -> QMenu:
        """A menu of external applications that can open the current document."""
        return build_open_with_menu(
            parent_widget or self, self.current_path or "", "Open With..."
        )

    def show_open_with_menu(self) -> None:
        """Displays the 'Open With' external application menu below the toolbar button."""
        menu = self.get_open_with_menu()
        menu.exec(self.menu_anchor(self.btn_open_external))

    def menu_anchor(self, button) -> QPoint:
        """Below button when it is on screen, else at the pointer (the button may be folded into a menu)."""
        if button is not None and button.isVisible():
            return button.mapToGlobal(QPoint(0, button.height()))
        return QCursor.pos()
