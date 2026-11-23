"""
The reading ruler: a band over one line of text, with the rest of the page
dimmed, so the eye keeps its place.

It follows the mouse over a page, and Alt+Down and Alt+Up move it a line at a
time, on to the next or previous page at either end. Ctrl+Alt+L turns it on
and off; the choice is stored in READING_RULER_KEY, which the Accessibility
section of Settings switches too (RiemannWindow.apply_accessibility tells
open readers).

ReadingRuler is given two functions: page_chars(page), the character boxes
of a page, and page_count(). It reports through signals: page_needed(page)
before stepping onto a page, so the reader can lay it out; band_changed(page,
band, scroll), the page and the box to draw, or (None, None) to draw none, and
whether to scroll the band into view; and toast for messages.

Lines come from the character boxes text selection uses (text_lines): boxes
in reading order are one line while their vertical centres agree and the text
does not jump back to the left. Two columns read down one column and then the
other, so a line never spans both. The lines of a page are cached against the
character list they came from, which the reader replaces when the zoom
changes. Pages without text (scans before OCR) get no ruler.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, QRect, Signal

from ....core.settings import READING_RULER_KEY, app_settings

RULER_PAD = 3


def text_lines(chars: list[tuple]) -> list[QRect]:
    """The bounding box of each line of text, in reading order."""
    lines: list[QRect] = []
    current: QRect | None = None
    last: QRect | None = None
    for ch, rect, *_ in chars:
        if rect is None or rect.isEmpty() or not ch.strip():
            continue
        same_line = (
            current is not None
            and last is not None
            and abs(rect.center().y() - current.center().y()) <= max(rect.height(), current.height()) / 2
            and rect.left() >= last.left() - max(2, last.width())
        )
        if same_line:
            current = current.united(rect)
        else:
            if current is not None:
                lines.append(current)
            current = QRect(rect)
        last = rect
    if current is not None:
        lines.append(current)
    return lines


def line_at(lines: list[QRect], pos: QPoint) -> int | None:
    """The line under pos, else the nearest one above or below in pos's column, else the nearest."""
    if not lines:
        return None
    for i, line in enumerate(lines):
        if line.adjusted(-RULER_PAD, -RULER_PAD, RULER_PAD, RULER_PAD).contains(pos):
            return i
    column = [i for i, line in enumerate(lines) if line.left() <= pos.x() <= line.right()]
    pool = column or range(len(lines))
    return min(pool, key=lambda i: abs(lines[i].center().y() - pos.y()))


class ReadingRuler(QObject):
    band_changed = Signal(object, object, bool)
    page_needed = Signal(int)
    toast = Signal(str)

    def __init__(
        self,
        page_chars: Callable[[int], list],
        page_count: Callable[[], int],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_chars = page_chars
        self.page_count = page_count
        self._on: bool | None = None
        self.where: tuple[int, int] | None = None
        self._cache: dict[int, tuple[int, list[QRect]]] = {}

    def enabled(self) -> bool:
        if self._on is None:
            self._on = app_settings().value(READING_RULER_KEY, False, type=bool)
        return bool(self._on)

    def set_enabled(self, on: bool) -> None:
        self._on = bool(on)
        if not on:
            self._show(None)

    def toggle(self, current_page: int) -> None:
        """Turns the ruler on (on current_page) or off, and stores the choice."""
        on = not self.enabled()
        app_settings().setValue(READING_RULER_KEY, on)
        self.set_enabled(on)
        if on:
            self.step(0, current_page)
        self.toast.emit("Reading ruler on (Alt+Down and Alt+Up move it)" if on else "Reading ruler off")

    def lines(self, page: int) -> list[QRect]:
        chars = self.page_chars(page)
        cached = self._cache.get(page)
        if cached is None or cached[0] != id(chars):
            cached = (id(chars), text_lines(chars))
            self._cache[page] = cached
        return cached[1]

    def follow_pointer(self, page: int | None, pos: QPoint) -> None:
        if not self.enabled() or page is None:
            return
        index = line_at(self.lines(page), pos)
        self._show(None if index is None else (page, index), scroll=False)

    def step(self, delta: int, current_page: int) -> None:
        """Moves the ruler delta lines (0 shows it on current_page), across pages."""
        if not self.enabled():
            return
        forward = delta >= 0
        if self.where is None:
            page, index = current_page, 0 if forward else None
        else:
            page, index = self.where[0], self.where[1] + delta
        count = self.page_count()
        while 0 <= page < count:
            self.page_needed.emit(page)
            lines = self.lines(page)
            if index is None:
                index = len(lines) - 1
            if 0 <= index < len(lines):
                self._show((page, index))
                return
            page, index = (page + 1, 0) if forward else (page - 1, None)

    def _show(self, where: tuple[int, int] | None, scroll: bool = True) -> None:
        self.where = where
        if where is None:
            self.band_changed.emit(None, None, False)
            return
        line = self.lines(where[0])[where[1]]
        band = line.adjusted(-RULER_PAD * 3, -RULER_PAD, RULER_PAD * 3, RULER_PAD)
        self.band_changed.emit(where[0], band, scroll)
