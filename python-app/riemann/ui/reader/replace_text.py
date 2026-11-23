"""
Replace text: new words drawn over old ones, as an annotation.

A "replace" annotation is a FreeText box with an opaque background in the
page's own colour and the new text on top (core/pdf_annotations.py writes
it). Its look is taken from what it covers:

- the box comes from the selection's character boxes, widened or deepened
  when the new text needs more room (nothing reflows);
- the background is the most common colour in a thin ring around the
  selection, sampled from the page rendered without the dark theme, so
  tinted paper doesn't get a white patch;
- the font is one of the PDF standard fonts (Helvetica, Times or Courier, in
  bold or italic as the original), picked from the original font's flags and,
  because PDFium often can't read those for built-in fonts, its name;
- size, colour, line pitch and baseline come from the original characters.

The old text stays in the file under the box; search, copy and the AI tools
still find it. The dialog says so and offers to add a redaction mark under
the same selection, which Apply redactions then removes, leaving the box.
"""

import colorsys
import itertools
from collections import Counter
from collections.abc import Sequence
from statistics import median

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..palette import CONTENT, dialog_icon

SERIF_HINTS = ("times", "roman", "serif", "georgia", "garamond", "palatino", "minion", "cambria", "baskerville",
               "caslon", "charter", "utopia", "bookman", "century", "nimbusrom", "cmr", "lmroman", "book antiqua")
FIXED_HINTS = ("courier", "mono", "consol", "typewriter", "cmtt", "code", "menlo", "inconsolata")
BOLD_HINTS = ("bold", "black", "heavy", "semibold", "demi", "-bd")
ITALIC_HINTS = ("italic", "oblique", "-it", "slanted")
STANDARD = {
    ("sans", False, False): "Helvetica",
    ("sans", True, False): "Helvetica-Bold",
    ("sans", False, True): "Helvetica-Oblique",
    ("sans", True, True): "Helvetica-BoldOblique",
    ("serif", False, False): "Times-Roman",
    ("serif", True, False): "Times-Bold",
    ("serif", False, True): "Times-Italic",
    ("serif", True, True): "Times-BoldItalic",
    ("fixed", False, False): "Courier",
    ("fixed", True, False): "Courier-Bold",
    ("fixed", False, True): "Courier-Oblique",
    ("fixed", True, True): "Courier-BoldOblique",
}
QT_FAMILIES = {
    "Helvetica": ["Helvetica", "Arial", "Liberation Sans", "Nimbus Sans"],
    "Times": ["Times New Roman", "Times", "Liberation Serif", "Nimbus Roman"],
    "Courier": ["Courier New", "Courier", "Liberation Mono", "Nimbus Mono PS"],
}
DESCENT = 0.22


def standard_font(name: str, serif: bool, fixed: bool, bold: bool, italic: bool) -> str:
    """The standard PDF font closest to a font with this name and these flags."""
    lower = name.lower().split("+")[-1]
    if fixed or any(h in lower for h in FIXED_HINTS):
        family = "fixed"
    elif (serif or any(h in lower for h in SERIF_HINTS)) and "sans" not in lower:
        family = "serif"
    else:
        family = "sans"
    bold = bold or any(h in lower for h in BOLD_HINTS)
    italic = italic or any(h in lower for h in ITALIC_HINTS)
    return STANDARD[(family, bold, italic)]


def qt_families(font: str) -> list[str]:
    return QT_FAMILIES[font.split("-")[0]]


def text_width(text: str, font: str, size: float) -> float:
    from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS

    widths = CORE_FONT_METRICS[font].character_widths
    fallback = widths.get("default", 500)
    return sum(widths.get(ch, fallback) for ch in text) * size / 1000.0


def ring_colour(pixels: bytes, width: int, height: int, box: tuple[int, int, int, int], ring: int = 3) -> str:
    """
    The most common colour in a ring just outside box (pixel coordinates,
    BGRA pixels). Colours are counted in coarse buckets so anti-aliasing
    doesn't split the paper colour; the winning bucket's average is returned.
    """
    x1, y1, x2, y2 = box
    counts: Counter = Counter()
    sums: dict[tuple, list[int]] = {}

    def add(x: int, y: int) -> None:
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 4
            rgb = (pixels[i + 2], pixels[i + 1], pixels[i])
            key = tuple(c // 8 for c in rgb)
            counts[key] += 1
            total = sums.setdefault(key, [0, 0, 0])
            for k in range(3):
                total[k] += rgb[k]

    for d in range(1, ring + 1):
        for x in range(x1 - d, x2 + d + 1):
            add(x, y1 - d)
            add(x, y2 + d)
        for y in range(y1 - d + 1, y2 + d):
            add(x1 - d, y)
            add(x2 + d, y)
    if not counts:
        return CONTENT["paper"]
    key, n = counts.most_common(1)[0]
    r, g, b = (round(c / n) for c in sums[key])
    return QColor(r, g, b).name()


def themed(colour: str, theme_mode: int) -> QColor:
    """colour as the page theme shows the page: inverted (1) or with its lightness inverted (2)."""
    c = QColor(colour)
    if theme_mode == 1:
        return QColor.fromRgb(255 - c.red(), 255 - c.green(), 255 - c.blue())
    if theme_mode == 2:
        h, l, s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
        r, g, b = colorsys.hls_to_rgb(h, 1.0 - l, s)
        return QColor.fromRgbF(r, g, b)
    return c


def overlay_from_selection(
    rects: Sequence[tuple[float, float, float, float]],
    styles: Sequence[tuple],
    page_size: tuple[float, float],
    text: str,
    background: str,
) -> dict:
    """
    A "replace" annotation for text over a selection. rects are the
    selection's line boxes (relative, top-left); styles are get_char_styles
    entries for the page; page_size is the displayed page in points.
    """
    width, height = page_size
    lines = sorted(rects, key=lambda r: r[1])
    l = min(r[0] for r in lines)
    t = min(r[1] for r in lines)
    r_ = max(r[2] for r in lines)
    b = max(r[3] for r in lines)

    def inside(box) -> bool:
        cx, cy = (box[0] + box[2]) / 2 / width, 1 - (box[1] + box[3]) / 2 / height
        return any(x1 <= cx <= x2 and y1 <= cy <= y2 for x1, y1, x2, y2 in lines)

    chosen = [s for s in styles if s[0].strip() and inside(s[1])]
    size = median(s[2] for s in chosen) if chosen else max(4.0, (lines[0][3] - lines[0][1]) * height * 0.85)
    if chosen:
        name, serif, fixed, bold, italic = Counter((s[3], s[4], s[5], s[6], s[7]) for s in chosen).most_common(1)[0][0]
        colour = Counter(s[8] or CONTENT["ink"] for s in chosen).most_common(1)[0][0]
    else:
        name, serif, fixed, bold, italic, colour = "", False, False, False, False, CONTENT["ink"]
    font = standard_font(name, serif, fixed, bold, italic)
    first_height = (lines[0][3] - lines[0][1]) * height
    baseline = max(size * 0.7, first_height - DESCENT * size)
    tops = [ln[1] * height for ln in lines]
    pitches = [b2 - a2 for a2, b2 in itertools.pairwise(tops) if b2 - a2 > size * 0.5]
    leading = median(pitches) if pitches else size * 1.2

    new_lines = text.splitlines() or [""]
    needed_w = max(text_width(line, font, size) for line in new_lines) + size * 0.1
    needed_h = baseline + (len(new_lines) - 1) * leading + DESCENT * size
    r_ = max(r_, l + needed_w / width)
    b = max(b, t + needed_h / height)
    return {
        "type": "replace",
        "rects": [[l, t, min(r_, 1.0), min(b, 1.0)]],
        "text": text,
        "font": font,
        "size": round(size, 3),
        "baseline": round(baseline, 3),
        "leading": round(leading, 3),
        "color": colour,
        "background": background,
    }


class ReplaceTextDialog(QDialog):
    """Asks for the new text; remove_original is whether to mark the old text for redaction."""

    def __init__(self, original: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Replace text")
        layout = QVBoxLayout(self)
        dialog_icon(self, "replace")
        self.editor = QPlainTextEdit(original)
        self.editor.selectAll()
        layout.addWidget(self.editor)
        note = QLabel(
            "The new text covers the old. The old text stays in the file, so search, copy and the AI tools still find "
            "it."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.remove_box = QCheckBox(
            "Remove the original text too (marks it for redaction; Apply redactions removes it)"
        )
        layout.addWidget(self.remove_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(460, 220)

    def text(self) -> str:
        return self.editor.toPlainText()

    def remove_original(self) -> bool:
        return self.remove_box.isChecked()
