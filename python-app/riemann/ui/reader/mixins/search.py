"""
Search Mixin.

Handles finding text within the PDF document.
"""

from PySide6.QtWebEngineWidgets import QWebEngineView

from ....core.constants import ViewMode


class SearchMixin:
    """Methods for search functionality."""

    def toggle_search_bar(self) -> None:
        """Toggles search bar visibility."""
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
        """Find next text occurrence."""
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(self.txt_search.text())
        else:
            self._find_text(1)

    def find_prev(self) -> None:
        """Find previous text occurrence."""
        if self.view_mode == ViewMode.REFLOW:
            self.web.findText(
                self.txt_search.text(), QWebEngineView.FindFlag.FindBackward
            )
        else:
            self._find_text(-1)

    def _find_text(self, direction: int) -> None:
        """Backend search logic."""
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
                if term in text:
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
