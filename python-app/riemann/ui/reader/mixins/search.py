"""
Finding text in a document's pages and in the text of its annotations.

PageSearch is given document(), the open document or None, and
annotations(), the reader's annotations by page. matches yields each page
that holds the term, in order from a starting page in either direction
(wrapping round), with the term's boxes on it (None when they can't be
read); a page whose text can't be read is skipped. The reader shows a match
and asks for the next one only when showing it failed. In the reflowed view
the web view's own find is used instead.
"""

from collections.abc import Callable, Iterator
from typing import Any


class PageSearch:
    def __init__(self, document: Callable[[], Any], annotations: Callable[[], dict]) -> None:
        self.document = document
        self.annotations = annotations

    def matches(self, text: str, start: int, direction: int) -> Iterator[tuple[int, list | None]]:
        """(page, boxes) for each page holding text, from start on in direction (1 or -1)."""
        doc = self.document()
        term = text.strip().lower()
        if not doc or not term:
            return
        count = doc.page_count
        annotations = self.annotations()
        for i in range(count):
            idx = (start + i * direction) % count
            try:
                page_text = doc.get_page_text(idx).lower()
                anno_match = False
                if str(idx) in annotations:
                    for anno in annotations[str(idx)]:
                        if anno.get("type") in ("note", "text") and "text" in anno and term in anno["text"].lower():
                            anno_match = True
                            break
            except Exception:
                continue
            if term in page_text or anno_match:
                try:
                    rects = doc.search_page(idx, text.strip())
                except Exception:
                    rects = None
                yield idx, rects
