"""
Citation hover cards in the reader.

Resting the mouse on an internal link for 0.3 s reads the text at the link's
target; when it looks like a bibliography entry (it names a year, an arXiv id
or a DOI), the CitationCard shows it. Reference texts are cached per link for
the open document.
"""

from PySide6.QtCore import QTimer

from ....core import citations
from ..citation_card import CitationCard
from .host import ReaderHostBase


class CitationsMixin(ReaderHostBase):
    def _setup_citation_card(self) -> None:
        self.citation_card = CitationCard(self)
        self.citation_card.apply_theme(self._home_dark)
        self.citation_card.go_to.connect(lambda page, top: self.navigate_to(page, top_pts=top))
        self.citation_card.open_beside.connect(self._open_beside)
        self._cite_timer = QTimer(self)
        self._cite_timer.setSingleShot(True)
        self._cite_timer.setInterval(300)
        self._cite_timer.timeout.connect(self._show_citation_card)
        self._cite_pending = None
        self._reference_cache = {}

    def _hover_internal_link(self, url: str, source, pos) -> None:
        if self._cite_pending is not None and self._cite_pending[0] == url:
            return
        self._cite_pending = (url, source, pos)
        self._cite_timer.start()

    def _hover_left_link(self) -> None:
        self._cite_pending = None
        self._cite_timer.stop()
        self.citation_card.leave_soon()

    def _show_citation_card(self) -> None:
        if self._cite_pending is None or not self.current_doc:
            return
        url, source, pos = self._cite_pending
        target = self._parse_internal_link(url)
        if target is None:
            return
        text = self._reference_cache.get(url)
        if text is None:
            text = citations.reference_text_at(self.current_doc, target[0], target[1])
            self._reference_cache[url] = text
        if citations.looks_like_reference(text):
            self.citation_card.show_for(text, target, source.mapTo(self, pos))

    def _open_beside(self, url: str) -> None:
        window = self.window()
        if hasattr(window, "open_url_beside"):
            window.open_url_beside(url)
        elif hasattr(window, "new_browser_tab"):
            window.new_browser_tab(url)
