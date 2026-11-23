"""
What the reader mixins expect from the class that combines them.

ReaderTab (ui/reader/tab.py) is a QWidget built from about fifteen mixins, and
each mixin reads state and widgets that another one creates. ReaderHost lists
that shared surface in one place: the document state set in ReaderTab.__init__,
the widgets built by ToolbarMixin.setup_ui, and the few methods every mixin
calls. Mixins derive from ReaderHostBase, which is ReaderHost for type checkers
and plain object at run time, so editors and pyright can flag a misspelt
attribute without changing the class layout Qt sees.

tests/test_toolbar_layout.py checks that a fresh ReaderTab really has every
name declared here. Add an attribute here when a second mixin starts using it.

State created later (after a document loads, or on first use) has a class-level
default on the mixin that owns it, usually None, so the mixins test it with
"is None" rather than hasattr. The ones more than one mixin reads are listed
here too.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Set, Tuple

from PySide6.QtCore import QPoint, QSettings, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QRubberBand,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ....core.constants import ViewMode, ZoomMode

if TYPE_CHECKING:
    from ..toolbar_layout import ToolbarLayout
    from ..widgets import PageWidget


class ReaderHost(Protocol):
    settings: QSettings
    engine: Any
    current_doc: Any
    current_path: Optional[str]
    current_page_index: int

    theme_mode: int
    zoom_mode: ZoomMode
    manual_scale: float
    facing_mode: bool
    continuous_scroll: bool
    view_mode: ViewMode

    is_annotating: bool
    current_tool: str
    pen_color: str
    pen_thickness: int
    active_drawing: List[QPoint]
    annotations: Dict[str, List[Dict[str, Any]]]
    is_snipping: bool
    snip_band: Optional[QRubberBand]

    page_widgets: Dict[int, "PageWidget"]
    rendered_pages: Set[int]
    form_widgets: Dict[int, List[QWidget]]
    _cached_base_size: Optional[Tuple[int, int]]
    _virtual_range: Tuple[int, int]
    rotation: int
    web: Optional[Any]
    metadata_worker: Optional[Any]
    edit_scratch: Optional[str]
    text_document: bool
    converted_pdf: Optional[str]
    converted_banner: Optional[QWidget]
    _document_sections: Tuple[Any, ...]
    document_password: Optional[str]
    document_encrypted: bool
    document_signed: bool
    _signed_edit_accepted: bool
    sig_worker: Optional[Any]
    _rebuild_debounce_timer: Optional[QTimer]
    _zoom_debounce_timer: Optional[QTimer]
    _char_geometry_cache: Optional[Dict[Any, Any]]

    toolbar: QWidget
    toolbar_layout: "ToolbarLayout"
    stack: QStackedWidget
    scroll: QScrollArea
    scroll_content: QWidget
    scroll_layout: QVBoxLayout
    search_bar: QWidget
    txt_search: QLineEdit
    ai_search_bar: QWidget
    txt_page: QLineEdit
    combo_zoom: QComboBox
    link_tooltip: QLabel
    sidebar_nav: QWidget
    notes_panel: QWidget
    list_recent: QListWidget

    def show_toast(self, msg: str) -> None: ...

    def _schedule_rebuild(self) -> None: ...

    def _schedule_zoom_finished(self) -> None: ...

    def _get_icon(self, name: str) -> QIcon: ...

    def rebuild_layout(self) -> None: ...

    def update_view(self) -> None: ...

    def calculate_scale(self) -> float: ...

    def load_document(self, path: str, *args: Any, **kwargs: Any) -> None: ...

    def open_here(self, path: str) -> None: ...

    def window(self) -> QWidget: ...

    def pdf_path(self) -> str: ...

    def has_unsaved_page_edits(self) -> bool: ...

    def resolve_page_edits(self) -> bool: ...

    def _render_text_page(self) -> None: ...

    def _live_metadata_worker(self) -> Optional[Any]: ...

    def ruler_enabled(self) -> bool: ...

    def ruler_follow_pointer(self, page: int, pos: QPoint) -> None: ...


if TYPE_CHECKING:
    ReaderHostBase = ReaderHost
else:
    ReaderHostBase = object
