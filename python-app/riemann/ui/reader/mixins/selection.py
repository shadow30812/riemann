"""
Text selection: caret, keyboard and mouse selection, word and paragraph picks, and the character geometry behind them.

Part of ReaderTab (riemann/ui/reader/tab.py). ReaderTab lists this mixin before
QWidget, so its Qt event handlers override QWidget's.

Notes:

_handle_selection_key: No caret yet: start at the beginning of the page in
view. A caret between two lines belongs to the line before it right after
Shift+End, and to the line after it otherwise. Words end at whitespace or at
the end of a line.

_pdf_box_to_widget: Loose boxes span the font's full ascent; trimming the top
keeps the highlight on the glyphs.

_runs_from_char_boxes: A generated space reaches up to the glyph that follows
it.

_get_linear_text_selection: Selection works on caret positions between
characters: a letter is only included once the pointer passes its middle, as in
text editors.
"""

from typing import List, Optional, Tuple
from PySide6.QtCore import (
    QPoint,
    QRect,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QDesktopServices,
    QKeyEvent,
)
from PySide6.QtWebEngineCore import QWebEnginePage
from ....core.constants import ViewMode
from .host import ReaderHostBase
from ....core.settings import search_url


class SelectionMixin(ReaderHostBase):
    _sel_anchor = None
    _sel_at_line_end = False
    _sel_focus = None
    _sel_page = None
    current_selected_text = ''
    is_selecting_text = False

    def select_all_text(self) -> None:
        """
        Executes global selection bindings natively supported within reflow architectures.
        Throws a contextual toast warning on generic image view modes.
        """
        if self.view_mode == ViewMode.REFLOW:
            self.web.page().triggerAction(QWebEnginePage.WebAction.SelectAll)
        else:
            self.show_toast(
                "Select All is currently only supported in Reflow (Web/Markdown) mode."
            )

    def clear_all_text_selections(self) -> None:
        """
        Clears text selections and copied text across all page widgets. The
        keyboard caret survives (collapsed onto its moving end) so Shift+arrows
        can start a new selection from the last click.
        """
        self.current_selected_text = ""
        self.is_selecting_text = False
        if self._sel_focus is not None:
            self._sel_anchor = self._sel_focus
        for w in self.page_widgets.values():
            if hasattr(w, "set_text_selection") and getattr(
                w, "selected_text_rects", None
            ):
                w.set_text_selection([])

    def clear_text_selection_state(self) -> None:
        """Forgets the selection and the keyboard caret entirely."""
        self._sel_page = None
        self._sel_anchor = None
        self._sel_focus = None
        self.clear_all_text_selections()

    def _set_caret_at_pos(self, page_idx: int, pos: QPoint) -> None:
        """Places the keyboard caret next to the character under pos."""
        if self.view_mode != ViewMode.IMAGE or not self.current_doc:
            return
        try:
            all_chars = self._get_page_chars(page_idx)
            idx = self._closest_char_index(all_chars, pos)
        except Exception:
            idx = None
        if idx is None:
            self._sel_page = self._sel_anchor = self._sel_focus = None
            return
        caret = idx + (1 if pos.x() > all_chars[idx][1].center().x() else 0)
        self._sel_page, self._sel_anchor, self._sel_focus = page_idx, caret, caret

    def _extend_selection_to_pos(self, page_idx: int, pos: QPoint) -> bool:
        """Shift+click: extends the current selection on this page up to pos."""
        if self._sel_anchor is None or self._sel_page != page_idx:
            return False
        all_chars = self._get_page_chars(page_idx)
        idx = self._closest_char_index(all_chars, pos)
        if idx is None:
            return False
        self._sel_focus = idx + (1 if pos.x() > all_chars[idx][1].center().x() else 0)
        self._apply_keyboard_selection()
        return True

    def _apply_keyboard_selection(self) -> None:
        """Paints the anchor..focus range and keeps the moving end in view."""
        page_idx = self._sel_page
        widget = self.page_widgets.get(page_idx)
        all_chars = self._get_page_chars(page_idx) if widget else []
        if not all_chars:
            return
        n = len(all_chars)
        self._sel_anchor = max(0, min(n, self._sel_anchor))
        self._sel_focus = max(0, min(n, self._sel_focus))
        lo, hi = sorted((self._sel_anchor, self._sel_focus))

        for idx, w in self.page_widgets.items():
            if idx != page_idx and getattr(w, "selected_text_rects", None):
                w.set_text_selection([])

        if lo == hi:
            widget.set_text_selection([])
            self.current_selected_text = ""
        else:
            rects, text = self._rects_for_char_range(all_chars, lo, hi - 1)
            widget.set_text_selection(rects)
            self.current_selected_text = text

        focus_rect = all_chars[min(self._sel_focus, n - 1)][1]
        top = self._page_top_y(page_idx)
        if top is not None:
            row = widget.parentWidget()
            x = (row.x() if row else 0) + widget.x() + focus_rect.center().x()
            self.scroll.ensureVisible(int(x), int(top + focus_rect.center().y()), 40, 60)

    def _handle_selection_key(self, event: QKeyEvent) -> bool:
        """
        Shift+Left/Right extend the selection by one character (with Ctrl, by a
        word), Shift+Up/Down by one line and Shift+Home/End to the line edges.
        """
        mods = event.modifiers()
        if not (mods & Qt.KeyboardModifier.ShiftModifier):
            return False
        key = event.key()
        if key not in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        ):
            return False
        if self.view_mode != ViewMode.IMAGE or not self.current_doc:
            return False

        if (
            self._sel_anchor is None
            or self._sel_page not in self.page_widgets
        ):
            page_idx = self.current_page_index
            if page_idx not in self.page_widgets or not self._get_page_chars(page_idx):
                return True
            self._sel_page, self._sel_anchor, self._sel_focus = page_idx, 0, 0

        all_chars = self._get_page_chars(self._sel_page)
        if not all_chars:
            return True
        n = len(all_chars)
        focus = max(0, min(n, self._sel_focus))

        def same_line(a: int, b: int) -> bool:
            ra, rb = all_chars[a][1], all_chars[b][1]
            return abs(ra.center().y() - rb.center().y()) < max(ra.height(), 1) * 0.5

        def line_bounds(i: int) -> Tuple[int, int]:
            start = end = i
            while start > 0 and same_line(start - 1, i):
                start -= 1
            while end < n - 1 and same_line(end + 1, i):
                end += 1
            return start, end

        at_line_end = self._sel_at_line_end and focus > 0
        ref = focus - 1 if at_line_end else min(focus, n - 1)
        self._sel_at_line_end = False

        word = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Right:
            if word:
                i = focus
                while i < n and all_chars[i][0].isspace():
                    i += 1
                while i < n and not all_chars[i][0].isspace():
                    i += 1
                    if i < n and not same_line(i - 1, i):
                        break
                focus = i
            else:
                focus = min(n, focus + 1)
        elif key == Qt.Key.Key_Left:
            if word:
                i = focus
                while i > 0 and all_chars[i - 1][0].isspace():
                    i -= 1
                while i > 0 and not all_chars[i - 1][0].isspace():
                    i -= 1
                    if i > 0 and not same_line(i - 1, i):
                        break
                focus = i
            else:
                focus = max(0, focus - 1)
        elif key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            start, end = line_bounds(ref)
            if key == Qt.Key.Key_Home:
                focus = start
            else:
                focus = end + 1
                self._sel_at_line_end = True
        else:
            cur = all_chars[ref][1]
            cy = cur.center().y()
            cx = cur.right() if at_line_end or focus > ref else cur.left()
            half = max(cur.height(), 1) * 0.5
            going_up = key == Qt.Key.Key_Up
            if going_up:
                candidates = [i for i, (_, r, _) in enumerate(all_chars) if r.center().y() < cy - half]
            else:
                candidates = [i for i, (_, r, _) in enumerate(all_chars) if r.center().y() > cy + half]
            if candidates:
                ys = [all_chars[i][1].center().y() for i in candidates]
                target_y = max(ys) if going_up else min(ys)
                on_line = [
                    i
                    for i in candidates
                    if abs(all_chars[i][1].center().y() - target_y)
                    < max(all_chars[i][1].height(), 1) * 0.5
                ]
                best = min(on_line, key=lambda i: abs(all_chars[i][1].center().x() - cx))
                focus = best + (1 if cx > all_chars[best][1].center().x() else 0)
                last_on_line = max(on_line)
                if focus == last_on_line + 1:
                    self._sel_at_line_end = True
            else:
                focus = 0 if going_up else n

        self._sel_focus = focus
        self._apply_keyboard_selection()
        return True

    def _get_page_chars(self, page_idx: int) -> List[tuple]:
        """
        Returns the page's characters in reading order as (char, QRect, run)
        tuples in widget coordinates, built from PDFium's glyph boxes and
        clustered into lines. Results are cached per page geometry.
        """
        widget = self.page_widgets.get(page_idx)
        if not widget or not self.current_doc:
            return []

        logical_w = widget.width()
        logical_h = widget.height()
        scale = self.calculate_scale()
        rotation = self.rotation

        cache_key = (page_idx, scale, logical_w, logical_h, rotation)
        if self._char_geometry_cache is None:
            self._char_geometry_cache = {}

        if cache_key not in self._char_geometry_cache:
            if page_idx not in self.text_segments_cache:
                self.text_segments_cache[page_idx] = self._page_char_runs(page_idx)

            raw_segments = []
            for seg_idx, (text, boxes) in enumerate(self.text_segments_cache[page_idx]):
                char_boxes = []
                for ch, box in zip(text, boxes):
                    rect = self._pdf_box_to_widget(box, scale, logical_w, logical_h, rotation)
                    char_boxes.append((ch, rect))
                if not char_boxes:
                    continue
                bounds = QRect(char_boxes[0][1])
                for _, r in char_boxes[1:]:
                    bounds = bounds.united(r)
                if bounds.height() > logical_h * 0.5 or bounds.width() > logical_w * 0.8:
                    continue
                raw_segments.append(
                    {
                        "seg_idx": seg_idx,
                        "text": text,
                        "x": bounds.x(),
                        "y": bounds.y(),
                        "w": bounds.width(),
                        "h": bounds.height(),
                        "char_boxes": char_boxes,
                    }
                )

            lines: List[List[dict]] = []
            for seg_item in raw_segments:
                placed = False
                for line in lines:
                    ref = line[0]
                    overlap = min(
                        seg_item["y"] + seg_item["h"], ref["y"] + ref["h"]
                    ) - max(seg_item["y"], ref["y"])
                    min_h = min(seg_item["h"], ref["h"])
                    if min_h > 0 and (overlap / min_h) >= 0.40:
                        line.append(seg_item)
                        placed = True
                        break
                if not placed:
                    lines.append([seg_item])

            lines.sort(key=lambda ln: sum(s["y"] for s in ln) / len(ln))

            all_chars: List[tuple[str, QRect, int]] = []
            for line in lines:
                line.sort(key=lambda s: s["x"])
                for s_idx, s in enumerate(line):
                    if s_idx > 0:
                        prev_s = line[s_idx - 1]
                        gap = s["x"] - (prev_s["x"] + prev_s["w"])
                        if (
                            gap > 2
                            and not prev_s["text"].endswith(" ")
                            and not s["text"].startswith(" ")
                        ):
                            space_rect = QRect(
                                prev_s["x"] + prev_s["w"], s["y"], gap, s["h"]
                            )
                            all_chars.append((" ", space_rect, -1))
                    for ch, ch_r in s["char_boxes"]:
                        all_chars.append((ch, ch_r, s["seg_idx"]))

            if len(self._char_geometry_cache) > 8:
                self._char_geometry_cache.pop(
                    next(iter(self._char_geometry_cache)), None
                )
            self._char_geometry_cache[cache_key] = all_chars

        return self._char_geometry_cache[cache_key]

    @staticmethod
    def _pdf_box_to_widget(
        box: Tuple[float, float, float, float],
        scale: float,
        logical_w: float,
        logical_h: float,
        rotation: int,
    ) -> QRect:
        """Maps a (left, top, right, bottom) box in PDF points to widget pixels."""
        l, t, r, b = box
        x = l * scale
        w = max(1.0, (r - l) * scale)
        h = max(1.0, (t - b) * scale)
        y = logical_h - t * scale
        y += h * 0.10
        h *= 0.90
        if rotation == 90:
            x, y, w, h = logical_h - y - h, x, h, w
        elif rotation == 180:
            x, y = logical_w - x - w, logical_h - y - h
        elif rotation == 270:
            x, y, w, h = y, logical_w - x - w, h, w
        return QRect(round(x), round(y), max(1, round(w)), max(1, round(h)))

    def _page_char_runs(
        self, page_idx: int
    ) -> List[Tuple[str, List[Tuple[float, float, float, float]]]]:
        """
        Splits a page's characters into runs of text on one line, each with a
        box per character in PDF points. Uses PDFium's own glyph boxes when the
        core provides them, and estimates widths inside each text segment
        otherwise (older core builds).
        """
        doc = self.current_doc
        if hasattr(doc, "get_char_boxes"):
            return self._runs_from_char_boxes(doc.get_char_boxes(page_idx))
        return self._runs_from_segments(doc.get_text_segments(page_idx))

    @staticmethod
    def _runs_from_char_boxes(chars: List[tuple]) -> List[tuple]:
        runs: List[tuple] = []
        text: List[str] = []
        boxes: List[list] = []

        def flush():
            while text and text[-1].isspace():
                text.pop()
                boxes.pop()
            if text:
                runs.append(("".join(text), [tuple(b) for b in boxes]))
            text.clear()
            boxes.clear()

        for ch, (l, t, r, b) in chars:
            if ch in ("\r", "\n", "\x02"):
                flush()
                continue
            empty = (r - l) <= 0.01 or (t - b) <= 0.01
            if ch.isspace() and empty:
                if text:
                    pl, pt, pr, pb = boxes[-1]
                    text.append(" ")
                    boxes.append([pr, pt, pr, pb])
                continue
            if empty:
                continue
            if boxes:
                pl, pt, pr, pb = boxes[-1]
                line_h = max(pt - pb, t - b, 1.0)
                jumped = abs((t + b) / 2 - (pt + pb) / 2) > line_h * 0.6
                if jumped or l < pl - line_h:
                    flush()
            if text and text[-1] == " ":
                boxes[-1][2] = max(boxes[-1][0], l)
            text.append(ch)
            boxes.append([l, t, r, b])
        flush()
        return runs

    @staticmethod
    def _runs_from_segments(segments: List[tuple]) -> List[tuple]:
        from PySide6.QtGui import QFont, QFontMetricsF

        metrics = QFontMetricsF(QFont("Serif", 100))
        runs = []
        for text, (l, t, r, b) in segments:
            if not text:
                continue
            advances = [max(1.0, metrics.horizontalAdvance(c)) for c in text]
            total = sum(advances)
            boxes, x = [], l
            for adv in advances:
                w = (adv / total) * (r - l)
                boxes.append((x, t, x + w, b))
                x += w
            runs.append((text, boxes))
        return runs

    @staticmethod
    def _closest_char_index(all_chars: List[tuple], pos: QPoint) -> Optional[int]:
        """Finds the character under pos, or the nearest one on the same line."""
        if not all_chars:
            return None
        for idx, (_, r, _) in enumerate(all_chars):
            if r.contains(pos):
                return idx

        px, py = pos.x(), pos.y()
        best_idx = None
        min_dist = float("inf")

        for idx, (_, r, _) in enumerate(all_chars):
            ry = r.center().y()
            rx = r.center().x()
            dy = abs(py - ry)
            dx = abs(px - rx)

            if dy <= r.height() * 0.8:
                dist = dx + dy * 2.0
                if dist < min_dist:
                    min_dist = dist
                    best_idx = idx
            elif dy < 35:
                dist = dx * 1.5 + dy * 6.0
                if dist < min_dist:
                    min_dist = dist
                    best_idx = idx

        if min_dist > 150:
            return None
        return best_idx

    @staticmethod
    def _rects_for_char_range(
        all_chars: List[tuple], min_idx: int, max_idx: int
    ) -> tuple[List[QRect], str]:
        """Merges the boxes of a character range into per-line highlight rects."""
        selected_rects: List[QRect] = []
        selected_chars: List[str] = []

        current_box: Optional[QRect] = None
        prev_cy: Optional[float] = None

        for i in range(min_idx, max_idx + 1):
            ch, rect, _ = all_chars[i]

            if current_box is None:
                current_box = QRect(rect)
                prev_cy = rect.center().y()
            else:
                same_line = abs(rect.center().y() - prev_cy) < rect.height() * 0.5
                if same_line and rect.left() <= current_box.right() + 8:
                    current_box = current_box.united(rect)
                else:
                    selected_rects.append(current_box)
                    current_box = QRect(rect)
                    prev_cy = rect.center().y()
                    if (
                        not same_line
                        and selected_chars
                        and not selected_chars[-1].isspace()
                        and not ch.isspace()
                    ):
                        selected_chars.append("\n")
            selected_chars.append(ch)

        if current_box is not None:
            selected_rects.append(current_box)

        return selected_rects, "".join(selected_chars)

    def _get_linear_text_selection(
        self, page_idx: int, start_pos: QPoint, end_pos: QPoint
    ) -> tuple[List[QRect], str]:
        """
        Calculates the character-level linear text selection between two points
        and remembers it as the anchor/focus of the keyboard selection.
        """
        if not start_pos or not end_pos:
            return [], ""

        all_chars = self._get_page_chars(page_idx)
        if not all_chars:
            return [], ""

        start_idx = self._closest_char_index(all_chars, start_pos)
        end_idx = self._closest_char_index(all_chars, end_pos)

        if start_idx is None or end_idx is None:
            return [], ""

        def caret(idx: int, pos: QPoint) -> int:
            return idx + (1 if pos.x() > all_chars[idx][1].center().x() else 0)

        anchor = caret(start_idx, start_pos)
        focus = caret(end_idx, end_pos)
        self._sel_page, self._sel_anchor, self._sel_focus = page_idx, anchor, focus
        lo, hi = sorted((anchor, focus))
        if lo == hi:
            return [], ""
        return self._rects_for_char_range(all_chars, lo, hi - 1)

    def _get_word_at_pos(
        self, page_idx: int, pos: QPoint
    ) -> tuple[List[QRect], str]:
        """Returns the bounding rectangle and text for the single word under pos."""
        widget = self.page_widgets.get(page_idx)
        if not widget:
            return [], ""

        all_chars = self._get_page_chars(page_idx)
        if not all_chars:
            return [], ""

        hit_idx = None
        for idx, (_, r, _) in enumerate(all_chars):
            if r.contains(pos):
                hit_idx = idx
                break

        if hit_idx is None:
            px, py = pos.x(), pos.y()
            min_d = float("inf")
            for idx, (_, r, _) in enumerate(all_chars):
                d = abs(px - r.center().x()) + abs(py - r.center().y()) * 2.0
                if d < min_d:
                    min_d = d
                    hit_idx = idx
            if hit_idx is None or min_d > 80:
                return [], ""

        if all_chars[hit_idx][0].isspace():
            return [], ""

        start_i = hit_idx
        while start_i > 0 and not all_chars[start_i - 1][0].isspace():
            curr_r = all_chars[start_i][1]
            prev_r = all_chars[start_i - 1][1]
            vert_gap = abs(curr_r.center().y() - prev_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                break
            horiz_gap = curr_r.left() - prev_r.right()
            if horiz_gap > max(8.0, curr_r.height() * 0.6):
                break
            start_i -= 1

        end_i = hit_idx
        while end_i < len(all_chars) - 1 and not all_chars[end_i + 1][0].isspace():
            curr_r = all_chars[end_i][1]
            next_r = all_chars[end_i + 1][1]
            vert_gap = abs(next_r.center().y() - curr_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                break
            horiz_gap = next_r.left() - curr_r.right()
            if horiz_gap > max(8.0, curr_r.height() * 0.6):
                break
            end_i += 1

        self._sel_page, self._sel_anchor, self._sel_focus = page_idx, start_i, end_i + 1
        word_rect = all_chars[start_i][1]
        word_chars = []
        for i in range(start_i, end_i + 1):
            ch, r, _ = all_chars[i]
            word_chars.append(ch)
            word_rect = word_rect.united(r)

        return [word_rect], "".join(word_chars)

    def _get_paragraph_at_pos(
        self, page_idx: int, pos: QPoint
    ) -> tuple[List[QRect], str]:
        """
        Returns the bounding rectangles and text for the entire sentence/paragraph under pos.
        Selection extends until a line break or horizontal whitespace is encountered.
        """
        widget = self.page_widgets.get(page_idx)
        if not widget:
            return [], ""

        all_chars = self._get_page_chars(page_idx)
        if not all_chars:
            return [], ""

        hit_idx = None
        for idx, (_, r, _) in enumerate(all_chars):
            if r.contains(pos):
                hit_idx = idx
                break

        if hit_idx is None:
            px, py = pos.x(), pos.y()
            min_d = float("inf")
            for idx, (_, r, _) in enumerate(all_chars):
                d = abs(px - r.center().x()) + abs(py - r.center().y()) * 2.0
                if d < min_d:
                    min_d = d
                    hit_idx = idx
            if hit_idx is None or min_d > 80:
                return [], ""

        start_i = hit_idx
        while start_i > 0:
            curr_r = all_chars[start_i][1]
            prev_r = all_chars[start_i - 1][1]
            vert_gap = abs(curr_r.center().y() - prev_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                break
            horiz_gap = curr_r.left() - prev_r.right()
            if horiz_gap > max(20.0, curr_r.height() * 1.5):
                break
            start_i -= 1

        end_i = hit_idx
        while end_i < len(all_chars) - 1:
            curr_r = all_chars[end_i][1]
            next_r = all_chars[end_i + 1][1]
            vert_gap = abs(next_r.center().y() - curr_r.center().y())
            if vert_gap > curr_r.height() * 0.5:
                break
            horiz_gap = next_r.left() - curr_r.right()
            if horiz_gap > max(20.0, curr_r.height() * 1.5):
                break
            end_i += 1

        while start_i < end_i and all_chars[start_i][0].isspace():
            start_i += 1
        while end_i > start_i and all_chars[end_i][0].isspace():
            end_i -= 1
        self._sel_page, self._sel_anchor, self._sel_focus = page_idx, start_i, end_i + 1

        selected_rects: List[QRect] = []
        selected_chars: List[str] = []
        current_box: Optional[QRect] = None

        for i in range(start_i, end_i + 1):
            ch, rect, _ = all_chars[i]
            selected_chars.append(ch)
            if current_box is None:
                current_box = QRect(rect)
            else:
                current_box = current_box.united(rect)

        if current_box is not None:
            selected_rects.append(current_box)

        return selected_rects, "".join(selected_chars)

    def _search_web_for_selected_text(self, text: str) -> None:
        """Searches the web for text in a new browser tab."""
        url_string = search_url(text)

        if hasattr(self.window(), "new_browser_tab"):
            self.window().new_browser_tab(url_string)
        else:
            QDesktopServices.openUrl(QUrl(url_string))
