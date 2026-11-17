"""
Background page rendering for the reader.

PDF pages are rasterised on a worker thread, so scrolling never waits on
PDFium. The worker renders through the tab's PageRenderer
(core/render_process.py), which runs PDFium in a child process of its own, so
a slow page in one tab never holds up another. Finished pages are
kept in a process-wide LRU cache so that rebuilding the virtual layout or
scrolling back over recent pages does not render them again.

The Rust core renders at most 64 megapixels per page (MAX_PAGE_PIXELS) and
reports the scale it used. page_image records any shortfall as the image's
device pixel ratio (1.0 for a full render), which the reader multiplies into
the pixmap's ratio so an oversized page is stretched to its slot instead of
shown smaller. Stand-ins stretched to a page slot obey the same limit.

page_image wraps the bytes the Rust core rendered into without copying them;
PySide keeps the bytes object alive for as long as the QImage. Each page is
therefore held once, not twice. Overlays are painted on a QPixmap while the
QImage is still referenced, so Qt copies before drawing and the bytes are
never written to.

Notes:

PageRenderWorker: (generation, page, render scale, theme, is_thumbnail, image,
metadata) where metadata is (links, form widgets) the first time a page is
seen.

run: Links and form fields are fetched here too, so the UI thread never has to
wait on the PDFium lock while this thread renders. A page that fails to render
is logged once per document with the error and reported through page_failed,
so the reader can say so on the page instead of leaving it blank.
"""

import atexit
import threading
import weakref
from collections import OrderedDict
from typing import Dict, Hashable, Optional, Tuple

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from ...core import render_process
from ...core.log import log


# Rendered pages are always opaque (PDFium draws onto a white background), so
# their BGRA bytes are exactly Qt's RGB32 layout (0xffRRGGBB). RGB32 is also the
# format QPixmap uses on screen, so QPixmap.fromImage shares the buffer instead
# of scanning it for transparency and converting it, which took 10-13 ms per
# page at Fit Width when the images were tagged ARGB32.
PAGE_IMAGE_FORMAT = QImage.Format.Format_RGB32


MAX_PAGE_PIXELS = 64_000_000


def page_image(res, requested_scale: float) -> QImage:
    """A QImage of a render result, marked with how far short of requested_scale it fell."""
    img = QImage(res.data, res.width, res.height, PAGE_IMAGE_FORMAT)
    actual = getattr(res, "scale", requested_scale)
    if requested_scale > 0 and actual < requested_scale * 0.999:
        img.setDevicePixelRatio(actual / requested_scale)
    return img


render_process._return_page_buffers_to_os()


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
    """
    Stops every render thread. Runs on aboutToQuit and again at interpreter
    exit, so scripts and tests that never start an event loop or quit the app
    do not end with Qt aborting over a thread that is still running.
    """
    for worker in list(_LIVE_WORKERS):
        try:
            worker.stop()
            worker.wait(2000)
        except RuntimeError:
            pass
    render_process.close_all()


atexit.register(stop_all_render_workers)

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

    page_rendered = Signal(object, int, float, int, bool, QImage, object)
    page_failed = Signal(object, int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cond = threading.Condition()
        self._jobs: Dict[JobKey, tuple] = {}
        self._focus = 0
        self._doc = None
        self._generation = None
        self._stopped = False
        self._meta_sent = set()
        self._failed = set()
        _LIVE_WORKERS.add(self)

    def set_document(self, doc, generation) -> None:
        with self._cond:
            self._doc = doc
            self._generation = generation
            self._jobs.clear()
            self._meta_sent = set()
            self._failed = set()

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
                img = page_image(res, render_scale)
            except Exception as error:
                if page_idx not in self._failed:
                    self._failed.add(page_idx)
                    log.warning("Could not render page %d", page_idx + 1, exc_info=True)
                    self.page_failed.emit(generation, page_idx, str(error) or type(error).__name__)
                continue

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
