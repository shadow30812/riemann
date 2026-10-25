"""
Background page rendering for the reader.

PDF pages are rasterised on a worker thread (the Rust core releases the GIL
while PDFium renders), so scrolling never waits on PDFium. Finished pages are
kept in a process-wide LRU cache so that rebuilding the virtual layout or
scrolling back over recent pages does not render them again.
"""

import threading
import weakref
from collections import OrderedDict
from typing import Dict, Hashable, Optional, Tuple

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class PageImageCache:
    """A byte-budgeted LRU cache of rendered page images shared by all tabs."""

    def __init__(self, budget_bytes: int = 384 * 1024 * 1024) -> None:
        self.budget = budget_bytes
        self.used = 0
        self._items: "OrderedDict[Hashable, QImage]" = OrderedDict()

    def get(self, key: Hashable) -> Optional[QImage]:
        img = self._items.get(key)
        if img is not None:
            self._items.move_to_end(key)
        return img

    def put(self, key: Hashable, img: QImage) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self.used -= old.sizeInBytes()
        self._items[key] = img
        self.used += img.sizeInBytes()
        while self.used > self.budget and len(self._items) > 1:
            _, evicted = self._items.popitem(last=False)
            self.used -= evicted.sizeInBytes()

    def find_any(self, owner: Hashable, page_idx: int, theme: int) -> Optional[QImage]:
        """Returns the most recent image of a page at any scale, used as a stand-in."""
        for key in reversed(self._items):
            if key[0] == owner and key[1] == page_idx and key[3] == theme:
                return self._items[key]
        return None

    def drop_page(self, owner: Hashable, page_idx: int) -> None:
        for key in [k for k in self._items if k[0] == owner and k[1] == page_idx]:
            self.used -= self._items.pop(key).sizeInBytes()

    def drop_owner(self, owner: Hashable) -> None:
        for key in [k for k in self._items if k[0] == owner]:
            self.used -= self._items.pop(key).sizeInBytes()


PAGE_CACHE = PageImageCache()

_LIVE_WORKERS: "weakref.WeakSet[PageRenderWorker]" = weakref.WeakSet()


def stop_all_render_workers() -> None:
    """Stops every render thread; called when the application is quitting."""
    for worker in list(_LIVE_WORKERS):
        worker.stop()
        worker.wait(2000)

# A job is keyed by (page index, is_thumbnail) and carries the render
# parameters plus the generation of the document it was requested for.
JobKey = Tuple[int, bool]


class PageRenderWorker(QThread):
    """
    Renders queued pages in priority order and emits them as QImages.

    Pages closest to the reading position come first, and a cheap thumbnail
    pass runs ahead of the full-resolution pass so blank pages are filled
    almost immediately while scrolling fast.
    """

    # (generation, page, render scale, theme, is_thumbnail, image, metadata)
    # where metadata is (links, form widgets) the first time a page is seen.
    page_rendered = Signal(object, int, float, int, bool, QImage, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cond = threading.Condition()
        self._jobs: Dict[JobKey, tuple] = {}
        self._focus = 0
        self._doc = None
        self._generation = None
        self._stopped = False
        self._meta_sent = set()
        _LIVE_WORKERS.add(self)

    def set_document(self, doc, generation) -> None:
        with self._cond:
            self._doc = doc
            self._generation = generation
            self._jobs.clear()
            self._meta_sent = set()

    def set_focus(self, page_idx: int) -> None:
        with self._cond:
            self._focus = page_idx

    def request(
        self, page_idx: int, render_scale: float, theme: int, thumbnail: bool
    ) -> None:
        with self._cond:
            self._jobs[(page_idx, thumbnail)] = (render_scale, theme, self._generation)
            self._cond.notify()

    def cancel_page(self, page_idx: int) -> None:
        with self._cond:
            self._jobs.pop((page_idx, True), None)
            self._jobs.pop((page_idx, False), None)

    def clear(self) -> None:
        with self._cond:
            self._jobs.clear()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._jobs.clear()
            self._doc = None
            self._cond.notify_all()

    def _priority(self, key: JobKey) -> float:
        idx, thumbnail = key
        distance = abs(idx - self._focus)
        return distance * 0.5 if thumbnail else distance + 0.3

    def run(self) -> None:
        while True:
            with self._cond:
                while not self._stopped and not self._jobs:
                    self._cond.wait()
                if self._stopped:
                    return
                key = min(self._jobs, key=self._priority)
                render_scale, theme, generation = self._jobs.pop(key)
                doc = self._doc
                if generation != self._generation:
                    continue

            if doc is None:
                continue

            page_idx, thumbnail = key
            try:
                res = doc.render_page(page_idx, render_scale, theme)
                img = QImage(
                    res.data, res.width, res.height, QImage.Format.Format_ARGB32
                ).copy()
            except Exception:
                continue

            # Links and form fields are fetched here too, so the UI thread never
            # has to wait on the PDFium lock while this thread renders.
            meta = None
            if page_idx not in self._meta_sent:
                self._meta_sent.add(page_idx)
                try:
                    links = doc.get_links(page_idx) if hasattr(doc, "get_links") else []
                    forms = doc.get_form_widgets(page_idx)
                    meta = (links, forms)
                except Exception:
                    meta = None

            with self._cond:
                if self._stopped:
                    return
            self.page_rendered.emit(
                generation, page_idx, render_scale, theme, thumbnail, img, meta
            )
