"""
The reading ruler: a band over one line of text, with the rest of the page
dimmed, so the eye keeps its place.

It follows the mouse over a page, and Alt+Down and Alt+Up move it a line at a
time, on to the next or previous page at either end. Ctrl+Alt+L turns it on
and off; the choice is stored in READING_RULER_KEY, which the Accessibility
section of Settings switches too (RiemannWindow.apply_accessibility tells
open readers).

Lines come from the character boxes text selection uses (text_lines): boxes
in reading order are one line while their vertical centres agree and the text
does not jump back to the left. Two columns read down one column and then the
other, so a line never spans both. The lines of a page are cached against the
character list they came from, which _get_page_chars replaces when the zoom
changes. Pages without text (scans before OCR) get no ruler.
"""


from PySide6.QtCore import QPoint, QRect

from ....core.settings import READING_RULER_KEY, app_settings
from .host import ReaderHostBase

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


class RulerMixin(ReaderHostBase):
    _ruler_on: bool | None = None
    _ruler: tuple[int, int] | None = None
    _ruler_cache: dict[int, tuple[int, list[QRect]]] | None = None

    def ruler_enabled(self) -> bool:
        if self._ruler_on is None:
            self._ruler_on = app_settings().value(READING_RULER_KEY, False, type=bool)
        return bool(self._ruler_on)

    def set_reading_ruler(self, on: bool) -> None:
        self._ruler_on = bool(on)
        if not on:
            self._show_ruler(None)

    def toggle_reading_ruler(self) -> None:
        on = not self.ruler_enabled()
        app_settings().setValue(READING_RULER_KEY, on)
        self.set_reading_ruler(on)
        if on and self.current_doc:
            self.ruler_step(0)
        self.show_toast("Reading ruler on (Alt+Down and Alt+Up move it)" if on else "Reading ruler off")

    def _ruler_lines(self, page: int) -> list[QRect]:
        chars = self._get_page_chars(page)
        if self._ruler_cache is None:
            self._ruler_cache = {}
        cached = self._ruler_cache.get(page)
        if cached is None or cached[0] != id(chars):
            cached = (id(chars), text_lines(chars))
            self._ruler_cache[page] = cached
        return cached[1]

    def ruler_follow_pointer(self, page: int, pos: QPoint) -> None:
        if not self.ruler_enabled() or page is None:
            return
        index = line_at(self._ruler_lines(page), pos)
        self._show_ruler(None if index is None else (page, index), scroll=False)

    def ruler_step(self, delta: int) -> None:
        """Moves the ruler delta lines (0 shows it on the current page), across pages."""
        if not self.ruler_enabled() or not self.current_doc:
            return
        forward = delta >= 0
        if self._ruler is None:
            page, index = self.current_page_index, 0 if forward else None
        else:
            page, index = self._ruler[0], self._ruler[1] + delta
        count = self.current_doc.page_count
        while 0 <= page < count:
            if page not in self.page_widgets:
                self.jump_to_page(page)
            lines = self._ruler_lines(page)
            if index is None:
                index = len(lines) - 1
            if 0 <= index < len(lines):
                self._show_ruler((page, index))
                return
            page, index = (page + 1, 0) if forward else (page - 1, None)

    def _show_ruler(self, where: tuple[int, int] | None, scroll: bool = True) -> None:
        self._ruler = where
        band = None
        if where is not None:
            line = self._ruler_lines(where[0])[where[1]]
            band = line.adjusted(-RULER_PAD * 3, -RULER_PAD, RULER_PAD * 3, RULER_PAD)
        for idx, widget in self.page_widgets.items():
            if hasattr(widget, "set_ruler"):
                widget.set_ruler(band if where is not None and idx == where[0] else None)
        if where is not None and scroll:
            top = self._page_top_y(where[0])
            widget = self.page_widgets.get(where[0])
            if top is not None and widget is not None:
                row = widget.parentWidget()
                x = (row.x() if row else 0) + widget.x() + band.center().x()
                self.scroll.ensureVisible(int(x), int(top + band.center().y()), 40, 160)
