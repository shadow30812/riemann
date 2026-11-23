"""
Reader Tab Component.

ReaderTab combines the mixins in mixins/ and owns the parts that are classes
of their own (the reading ruler, read aloud, citation cards, form saving,
document metadata and text search). Each part gets what it needs as
functions in its constructor and reports through signals; __init__ connects
those signals to the reader's widgets.

Notes:

__init__: the scroll handler is throttled, not debounced: while scrolling
continuously it still runs every few frames, so pages keep loading during the
scroll.

The keyboard selection is kept as two caret positions (anchor and focus)
between characters of one page: position i sits just before character i, so the
selected characters are [min(anchor, focus), max(anchor, focus)).
"""

import os
import sys
from typing import Any

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QRubberBand,
    QTabWidget,
    QWidget,
)

from ...core import pdf_annotations
from ...core.constants import ViewMode, ZoomMode
from ...core.render_process import PageRenderer
from ...core.settings import app_settings
from ..palette import CONTENT, overlay_style, tokens
from ..shortcuts import share_keys
from .mixins.ai import AiMixin
from .mixins.annotations import AnnotationsMixin
from .mixins.appearance import AppearanceMixin
from .mixins.citations import CitationHover
from .mixins.document import DocumentMixin
from .mixins.forms import FormSaver
from .mixins.input import InputMixin
from .mixins.metadata import DocumentMetadata
from .mixins.navigation import NavigationMixin
from .mixins.pages import PagesMixin
from .mixins.read_aloud import ReadAloud
from .mixins.rendering import RenderingMixin
from .mixins.ruler import ReadingRuler
from .mixins.search import PageSearch
from .mixins.selection import SelectionMixin
from .mixins.signatures import SignaturesMixin
from .mixins.toolbar import ToolbarMixin
from .notes import notes_path_for
from .render_worker import PageRenderWorker
from .widgets import PageWidget

try:
    import riemann_core
except ImportError as e:
    print(f"CRITICAL: Could not import riemann_core backend.\nError: {e}")
    sys.exit(1)


class ReaderTab(
    ToolbarMixin,
    NavigationMixin,
    SelectionMixin,
    InputMixin,
    AppearanceMixin,
    DocumentMixin,
    PagesMixin,
    QWidget,
    RenderingMixin,
    AnnotationsMixin,
    AiMixin,
    SignaturesMixin,
):
    """
    The PDF and Markdown reader tab: a QWidget whose behaviour comes from the mixins in
    mixins/.
    """

    signatures_detected = Signal(list)
    is_preview = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.settings: QSettings = app_settings()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

        self.engine: riemann_core.PdfEngine | None = None
        self.current_doc: riemann_core.RiemannDocument | None = None
        self.current_path: str | None = None
        self.current_page_index: int = 0

        self.theme_mode: int = 0
        self.zoom_mode: ZoomMode = ZoomMode.FIT_WIDTH
        self.manual_scale: float = 1.0
        self.facing_mode: bool = False
        self.continuous_scroll: bool = True
        self.view_mode: ViewMode = ViewMode.IMAGE
        self.is_annotating: bool = False

        self.current_tool: str = "nav"
        self.pen_color: str = CONTENT["pen"]
        self.pen_thickness: int = 3
        self.active_drawing: list[QPoint] = []
        self.annotations: dict[str, list[dict[str, Any]]] = {}
        self.undo_stack: list[tuple[str, int, int]] = []
        self.redo_stack: list[tuple[str, dict]] = []

        self.is_snipping: bool = False
        self.snip_start: QPoint = QPoint()
        self.snip_band: QRubberBand | None = None
        self._pending_snip_image = None
        self.latex_model = None

        self.form_widgets: dict[int, list[QWidget]] = {}
        self.form_values_cache: dict[tuple[int, int], Any] = {}
        self.page_widgets: dict[int, PageWidget] = {}
        self.rendered_pages: set[int] = set()
        self.search_result: tuple[int, list[tuple[float, ...]]] | None = None
        self.text_segments_cache: dict[int, list[tuple[str, list[tuple[float, ...]]]]] = {}

        self.virtual_threshold: int = 15
        self._virtual_enabled: bool = False
        self._top_spacer: QWidget | None = None
        self._bottom_spacer: QWidget | None = None
        self._virtual_range: tuple[int, int] = (0, 0)
        self._cached_base_size: tuple[int, int] | None = None
        self._doc_generation: int = 0
        self._render_worker: PageRenderWorker | None = None
        self.page_renderer: PageRenderer | None = None
        self._raw_links_cache: dict[int, list] = {}
        self._raw_forms_cache: dict[int, list] = {}

        self._autoscroll_active: bool = False
        self._autoscroll_origin_global: QPoint | None = None
        self._autoscroll_speed_y: float = 0.0
        self._autoscroll_speed_x: float = 0.0

        self._click_count: int = 0
        self._last_click_time: float = 0.0
        self._last_click_pos: QPoint = QPoint()
        self._just_selected_multi_click: bool = False

        self.ruler = ReadingRuler(self._get_page_chars, self._page_count, self)
        self.ruler.page_needed.connect(self._lay_out_page)
        self.ruler.band_changed.connect(self._show_ruler_band)
        self.ruler.toast.connect(self.show_toast)
        self.read_aloud = ReadAloud(self._get_page_chars, self._page_count, self)
        self.read_aloud.page_needed.connect(self._lay_out_page)
        self.read_aloud.page_turned.connect(self.jump_to_page)
        self.read_aloud.sentence_shown.connect(self._show_spoken)
        self.read_aloud.word_shown.connect(self._show_spoken_word)
        self.read_aloud.reading_changed.connect(self._show_reading_aloud)
        self.read_aloud.toast.connect(self.show_toast)
        self.forms = FormSaver(self.pdf_path, lambda: self.form_values_cache, self)
        self.forms.saved.connect(self._on_forms_saved)
        self.forms.toast.connect(self.show_toast)
        self.search = PageSearch(lambda: self.current_doc, lambda: self.annotations)
        self.metadata = DocumentMetadata(
            lambda: getattr(self.window(), "library_manager", None), lambda: self.current_path, self
        )
        self.metadata.toast.connect(self.show_toast)
        self.metadata.renaming.connect(lambda _old: self._get_annotation_path())
        self.metadata.renamed.connect(self._on_renamed)

        self._init_backend()
        self.setup_ui()
        self.apply_theme()
        self._setup_scroller()

        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.setInterval(40)
        self.scroll_timer.timeout.connect(self.real_scroll_handler)

        self.autoscroll_timer = QTimer(self)
        self.autoscroll_timer.setInterval(30)
        self.autoscroll_timer.timeout.connect(self._do_autoscroll)

        self.external_comments: dict[int, list[dict[str, Any]]] = {}

        self.page_indicator_popup = QLabel(self)
        self.page_indicator_popup.setStyleSheet(overlay_style())
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
        self._page_indicator_timer.setInterval(self.PAGE_INDICATOR_MS)
        self._page_indicator_timer.timeout.connect(self._start_page_indicator_fade)

        self._init_shortcuts()

    def _init_shortcuts(self) -> None:
        """
        Registers widget-specific keyboard shortcuts mapped to primary application functionality.
        """
        shortcuts = [
            ("Ctrl+F", self.toggle_search_bar),
            ("Ctrl+I", self.toggle_ai_search_bar),
            ("Ctrl+S", self.save_document),
            ("Ctrl+Alt+S", self.toggle_sidebar),
            ("Ctrl+P", self.print_document),
            ("Ctrl+A", self.select_all_text),
            ("Ctrl+Shift+A", self.btn_annotate.click),
            ("Ctrl+Z", self.undo_annotation),
            ("Ctrl+Shift+Z", self.redo_annotation),
            ("Ctrl+R", self.rotate_document),
            ("Ctrl+Shift+R", self.rotate_document_ccw),
            ("Ctrl+Shift+S", self.protect_or_unlock),
            ("Page Down", lambda: self.scroll_page_length(1)),
            ("Page Up", lambda: self.scroll_page_length(-1)),
            ("F5", self.reload_document),
            ("Shift+F5", self.start_presentation),
            ("Ctrl+Shift+U", self.toggle_read_aloud),
            ("Ctrl+Alt+N", self.toggle_notes),
            ("Alt+Left", self.go_back),
            ("Alt+Right", self.go_forward),
            ("Ctrl+Alt+L", lambda: self.ruler.toggle(self.current_page_index)),
            ("Alt+Down", lambda: self.ruler.step(1, self.current_page_index)),
            ("Alt+Up", lambda: self.ruler.step(-1, self.current_page_index)),
        ]
        for seq, slot in shortcuts:
            QShortcut(QKeySequence(seq), self).activated.connect(slot)

        sc1 = QShortcut(QKeySequence("Ctrl+Tab"), self)
        sc1.setContext(Qt.ShortcutContext.WindowShortcut)
        sc1.activated.connect(lambda: self.cycle_tab(1))

        sc2 = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        sc2.setContext(Qt.ShortcutContext.WindowShortcut)
        sc2.activated.connect(lambda: self.cycle_tab(-1))
        share_keys(self)

    def _page_count(self) -> int:
        return self.current_doc.page_count if self.current_doc else 0

    def _lay_out_page(self, page: int) -> None:
        """Builds page's widget when it hasn't been yet (only pages near the view are)."""
        if page not in self.page_widgets:
            self.jump_to_page(page)

    def _scroll_to_box(self, page: int, box: QRect, margin_y: int) -> None:
        """Scrolls so box, in page's coordinates, is in view."""
        top = self._page_top_y(page)
        widget = self.page_widgets.get(page)
        if top is not None and widget is not None:
            row = widget.parentWidget()
            x = (row.x() if row else 0) + widget.x() + box.center().x()
            self.scroll.ensureVisible(int(x), int(top + box.center().y()), 40, margin_y)

    def _show_ruler_band(self, page: int | None, band: QRect | None, scroll: bool) -> None:
        for idx, widget in self.page_widgets.items():
            if hasattr(widget, "set_ruler"):
                widget.set_ruler(band if page is not None and idx == page else None)
        if page is not None and scroll:
            self._scroll_to_box(page, band, 160)

    def toggle_read_aloud(self) -> None:
        self.read_aloud.toggle(self.current_page_index)

    def _show_spoken(self, page: int | None, rects: list) -> None:
        for idx, widget in self.page_widgets.items():
            if hasattr(widget, "set_spoken_rects"):
                widget.set_spoken_rects(rects if idx == page else [])
        if page is not None and rects:
            self._scroll_to_box(page, rects[0], 120)

    def _show_spoken_word(self, page: int | None, rects: list) -> None:
        for idx, widget in self.page_widgets.items():
            if hasattr(widget, "set_word_rects"):
                widget.set_word_rects(rects if idx == page else [])

    def _show_reading_aloud(self, on: bool) -> None:
        button = self.btn_read_aloud
        if button is not None:
            button.setChecked(on)

    def flush_forms_to_pdf(self) -> None:
        self.forms.flush()

    def _on_forms_saved(self, path: str) -> None:
        sig = pdf_annotations.file_signature(path)
        if self._anno_file_sig is not None:
            self._anno_file_sig = sig
            self._write_annotation_json()
        if path == self.current_path:
            try:
                self._loaded_mtime = os.path.getmtime(path)
            except OSError:
                pass

    def toggle_search_bar(self) -> None:
        """
        Toggles the visibility of the search interface.
        When hiding the interface, clears any active search results and forces a re-render.
        """
        vis = not self.search_bar.isVisible()
        self.search_bar.setVisible(vis)
        self.btn_search.setChecked(vis)
        if vis:
            self.txt_search.setFocus()
            self.txt_search.selectAll()
        else:
            self.search_result = None
            self.rendered_pages.clear()
            self.update_view()

    def find_next(self) -> None:
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(self.txt_search.text())
        else:
            self._find_text(1)

    def find_prev(self) -> None:
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(self.txt_search.text(), QWebEngineView.FindFlag.FindBackward)
        else:
            self._find_text(-1)

    def _find_text(self, direction: int) -> None:
        """Shows the next (direction 1) or previous (-1) match in the pages and annotations."""
        if not self.current_doc:
            return
        term = self.txt_search.text().strip().lower()
        if not term:
            return
        for idx, rects in self.search.matches(self.txt_search.text(), self.current_page_index + direction, direction):
            try:
                self.current_page_index = idx
                self.search_result = (idx, rects) if rects is not None else None
                if idx in self.rendered_pages:
                    self.rendered_pages.remove(idx)
                if not self.continuous_scroll or (
                    self.continuous_scroll
                    and self._virtual_enabled
                    and (idx < self._virtual_range[0] or idx >= self._virtual_range[1])
                ):
                    self.rebuild_layout()
                self.update_view()
                self.ensure_visible(idx)
                return
            except Exception:
                continue
        self.show_toast(f"No matches for '{term}'")

    @property
    def document_metadata(self) -> dict | None:
        return self.metadata.data

    def extract_document_metadata(self) -> None:
        if not self.current_path or not self.current_doc or self.converted_pdf:
            return
        self.metadata.extract(lambda: self.current_doc.get_page_text(0))

    def _on_renamed(self, old_path: str, new_path: str) -> None:
        self.current_path = new_path
        notes = self.notes_panel
        if notes is not None:
            notes.save()
            old_notes = notes_path_for(old_path)
            if os.path.exists(old_notes):
                try:
                    os.replace(old_notes, notes_path_for(new_path))
                except OSError as e:
                    print(f"[Warning] Could not move notes for {new_path}: {e}")
            notes.path = notes_path_for(new_path)
        self._anno_json_cache = None
        self._update_tab_title(os.path.basename(new_path))

    def _setup_citation_card(self) -> None:
        self.citations = CitationHover(lambda: self.current_doc, self, self._home_dark)
        self.citations.go_to.connect(lambda page, top: self.navigate_to(page, top_pts=top))
        self.citations.open_beside.connect(self._open_beside)

    def _open_beside(self, url: str) -> None:
        window = self.window()
        if hasattr(window, "open_url_beside"):
            window.open_url_beside(url)
        elif hasattr(window, "new_browser_tab"):
            window.new_browser_tab(url)

    def _get_tab_widget(self) -> QTabWidget | None:
        """The QTabWidget this tab sits in, or None."""
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                return parent
            parent = parent.parent()
        return None

    def cycle_tab(self, delta: int) -> None:
        """Switches to the tab delta places along, wrapping around."""
        tw = self._get_tab_widget()
        if tw:
            count = tw.count()
            next_idx = (tw.currentIndex() + delta) % count
            tw.setCurrentIndex(next_idx)

    def _update_tab_title(self, title: str) -> None:
        """
        Shows title on this tab. The full name is set; the tab bar elides it only when
        the tabs no longer fit.
        """
        tw = self._get_tab_widget()
        if tw:
            idx = tw.indexOf(self)
            if idx != -1:
                tw.setTabText(idx, title)
                if hasattr(self.window(), "_update_tab_tooltip"):
                    self.window()._update_tab_tooltip(tw, idx)
                if hasattr(self.window(), "_update_window_title"):
                    self.window()._update_window_title()

    def _init_backend(self) -> None:
        try:
            self.engine = riemann_core.PdfEngine()
        except Exception as e:
            sys.stderr.write(f"Backend Initialization Error: {e}\n")


    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        if self.view_mode == ViewMode.REFLOW:
            self.web.setFocus()
        else:
            self.setFocus()

        if self.stack and self.stack.currentIndex() == 2:
            self.txt_open_path.setFocus()
            self._populate_home_recents()


    def toggle_annotation_mode(self, checked: bool = False) -> None:
        """Overrides mixin to manage viewport kinetic scrolling lock."""
        super().toggle_annotation_mode(checked)
        self._update_scroller_state()

    def set_tool(self, tool_id: str) -> None:
        """Overrides mixin to manage viewport kinetic scrolling lock."""
        super().set_tool(tool_id)
        self._update_scroller_state()




class PreviewReaderTab(ReaderTab):
    """
    A lightweight, read-only PDF viewer.

    It shares the rendering core with ReaderTab but has no toolbar and skips
    annotations, signature validation, AI indexing and metadata extraction.
    Double-clicking its tab title switches it to the full reader.
    """

    is_preview = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_path = ""

        for bar in (
            "toolbar",
            "anno_toolbar",
            "search_bar",
            "ai_search_bar",
            "signature_banner",
            "sidebar_nav",
        ):
            widget = getattr(self, bar, None)
            if widget is not None:
                widget.setVisible(False)

        self.preview_banner = QLabel(
            "Preview mode  ·  double-click the tab title to open the full reader"
        )
        self.preview_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._style_preview_banner(self._home_dark)
        self.preview_banner.setVisible(False)
        self.layout().insertWidget(0, self.preview_banner)

    def _style_preview_banner(self, dark: bool) -> None:
        t = tokens(dark)
        self.preview_banner.setStyleSheet(
            f"QLabel {{ background: {t['accent_soft']}; color: {t['text']};"
            f" border-bottom: 1px solid {t['border']}; padding: 4px; font-size: 11px; }}"
        )

    def apply_ui_theme(self, dark: bool) -> None:
        super().apply_ui_theme(dark)
        self._style_preview_banner(dark)

    def load_document(
        self,
        path: str,
        password: str | None = None,
        is_retry: bool = False,
    ) -> None:
        self.current_path = path
        super().load_document(path, password, is_retry)

    def _on_document_loaded(self, doc, state=(False, False)):
        super()._on_document_loaded(doc, state)
        self.preview_banner.setVisible(True)

    def _show_text_document(self, path: str, converter: str = "") -> None:
        super()._show_text_document(path, converter)
        self.toolbar.setVisible(False)
        self.preview_banner.setVisible(True)
