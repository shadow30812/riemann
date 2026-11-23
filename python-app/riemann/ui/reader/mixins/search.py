"""
Search Mixin.

Handles finding text within the PDF document.
"""

from PySide6.QtWebEngineWidgets import QWebEngineView

from ....core.constants import ViewMode
from .host import ReaderHostBase


class SearchMixin(ReaderHostBase):
    """Search in the page view and the reflowed view, including the text of annotations."""

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
        """
        Initiates a forward-directional search for the currently entered search term.
        Delegates to the appropriate backend depending on the active ViewMode.
        """
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(self.txt_search.text())
        else:
            self._find_text(1)

    def find_prev(self) -> None:
        """
        Initiates a backward-directional search for the currently entered search term.
        Delegates to the appropriate backend depending on the active ViewMode.
        """
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(
                self.txt_search.text(), QWebEngineView.FindFlag.FindBackward
            )
        else:
            self._find_text(-1)

    def _find_text(self, direction: int) -> None:
        """
        Finds the next (direction 1) or previous (-1) match in the pages and
        annotations, and shows it.
        """
        if not self.current_doc:
            return
        term = self.txt_search.text().strip().lower()
        if not term:
            return

        start = self.current_page_index + direction
        count = self.current_doc.page_count

        for i in range(count):
            idx = (start + i * direction) % count
            try:
                text = self.current_doc.get_page_text(idx).lower()
                anno_match = False
                if str(idx) in self.annotations:
                    for anno in self.annotations[str(idx)]:
                        if anno.get("type") in ("note", "text") and "text" in anno and term in anno["text"].lower():
                            anno_match = True
                            break
                if term in text or anno_match:
                    self.current_page_index = idx
                    try:
                        rects = self.current_doc.search_page(
                            idx, self.txt_search.text().strip()
                        )
                        self.search_result = (idx, rects)
                    except Exception:
                        self.search_result = None

                    if idx in self.rendered_pages:
                        self.rendered_pages.remove(idx)

                    if not self.continuous_scroll or (
                        self.continuous_scroll
                        and self._virtual_enabled
                        and (
                            idx < self._virtual_range[0]
                            or idx >= self._virtual_range[1]
                        )
                    ):
                        self.rebuild_layout()

                    self.update_view()
                    self.ensure_visible(idx)
                    return
            except Exception:
                continue
        self.show_toast(f"No matches for '{term}'")
