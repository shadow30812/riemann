"""
Citation hover cards in the reader.

Resting the mouse on an internal link for 0.3 s reads the text at the link's
target; when it looks like a bibliography entry (it names a year, an arXiv id
or a DOI), the CitationCard shows it. Reference texts are cached per link
until forget() is called for a new document.

CitationHover is given document(), the open document or None, and the widget
the card floats over. go_to(page, top) and open_beside(url) pass on the
card's buttons.
"""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QPoint, QTimer, Signal
from PySide6.QtWidgets import QWidget

from ....core import citations
from ..citation_card import CitationCard
from .navigation import parse_internal_link

HOVER_MS = 300


class CitationHover(QObject):
    go_to = Signal(int, object)
    open_beside = Signal(str)

    def __init__(self, document: Callable[[], Any], parent: QWidget, dark: bool = False) -> None:
        super().__init__(parent)
        self.document = document
        self._widget = parent
        self.card = CitationCard(parent)
        self.card.apply_theme(dark)
        self.card.go_to.connect(self.go_to)
        self.card.open_beside.connect(self.open_beside)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(HOVER_MS)
        self._timer.timeout.connect(self._show)
        self._pending: tuple[str, QWidget, QPoint] | None = None
        self._cache: dict[str, str] = {}

    def apply_theme(self, dark: bool) -> None:
        self.card.apply_theme(dark)

    def forget(self) -> None:
        """Drops the reference texts read so far, for a new document."""
        self._cache = {}

    def hover(self, url: str, source: QWidget, pos: QPoint) -> None:
        """The mouse rests on the internal link url, at pos in source."""
        if self._pending is not None and self._pending[0] == url:
            return
        self._pending = (url, source, pos)
        self._timer.start()

    def left(self) -> None:
        """The mouse has left the link."""
        self._pending = None
        self._timer.stop()
        self.card.leave_soon()

    def _show(self) -> None:
        doc = self.document()
        if self._pending is None or not doc:
            return
        url, source, pos = self._pending
        target = parse_internal_link(url)
        if target is None:
            return
        text = self._cache.get(url)
        if text is None:
            text = citations.reference_text_at(doc, target[0], target[1])
            self._cache[url] = text
        if citations.looks_like_reference(text):
            self.card.show_for(text, target, source.mapTo(self._widget, pos))
