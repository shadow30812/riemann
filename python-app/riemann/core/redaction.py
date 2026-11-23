"""
Applying redaction marks: removing what lies under them from the file itself.

apply_redactions takes Riemann's marks (relative, top-left rects on the page
as displayed, the same space every Riemann annotation uses) and rewrites the
marked pages with pikepdf. It runs through the reader's page-edit path, so it
works on the scratch copy and the file on disk changes only on Save.

What happens under a mark:

- Text is removed glyph by glyph. The page's content stream is interpreted
  (graphics and text state, fonts, Form XObjects) to find where each glyph
  is; a glyph whose box overlaps a mark is cut out of its string and replaced
  by the TJ spacing it took up, so every other glyph keeps its exact position
  and its original codes, font, size, colour and render mode. Invisible text
  (an OCR layer, render mode 3) goes the same way.
- Images are blacked out pixel by pixel (their soft mask made opaque there);
  an image entirely inside a mark is dropped.
- Vector paths are dropped only when they lie entirely inside a mark.
- Annotations, links and form fields that touch a mark are removed, and so
  are the applied marks themselves. Riemann annotations whose ids are in
  keep (a replace-text overlay over its own mark) stay.
- A black box is painted over each mark, and the page's stored thumbnail is
  dropped.

A Form XObject or image that needs changing is copied for this page first, so
other pages that share it are untouched, and the page's (copied) XObject list
then keeps only what its content still draws: a stale entry would keep the
original, unredacted object in the file. PDFium can't be used for the text,
because its editing API can set a text object's character codes but never
read them; rebuilding text from Unicode breaks subset fonts.

Where removal can't be exact, the whole Apply stops and names the page rather
than remove too little or too much (RedactionError): fonts whose glyph
positions can't be worked out (CMaps other than Identity-H, vertical writing,
missing widths), text used as a clipping path, inline images and image masks
that are only partly covered, and nesting deeper than MAX_DEPTH.

The file is saved in full (never an incremental update), and qpdf writes only
the objects still in use, so the old content streams and images are not left
behind. Before the result replaces the source it is checked with PDFium
(verify_redaction): no character may remain mostly inside a mark, and every
character clear of the marks must still be there, in the same place to
within POSITION_TOLERANCE points.

Not covered, as the Apply dialog says: document metadata (XMP), bookmarks,
attachments, the tagged-PDF tree's /ActualText, and font subsets, which keep
the outlines and Unicode mappings of removed glyphs.
"""

import os
import shutil
import tempfile
import zlib
from collections.abc import Iterable, Sequence
from typing import Any

from .page_edit import PageEditError, _open
from .pdf_annotations import NAME_PREFIX, _inherited, _page_space

Box = tuple[float, float, float, float]
Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MAX_DEPTH = 12
POSITION_TOLERANCE = 0.01
PATH_OPS = {"m", "l", "c", "v", "y", "h", "re"}
PAINT_OPS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
SHOW_OPS = {"Tj", "TJ", "'", '"'}


class RedactionError(PageEditError):
    """Apply stopped because a page can't be redacted exactly."""

    def __init__(self, page: int, reason: str):
        super().__init__(f"Page {page + 1}: {reason} Nothing was changed.")
        self.page = page


def mul(m: Matrix, n: Matrix) -> Matrix:
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def transform(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def envelope(m: Matrix, x1: float, y1: float, x2: float, y2: float) -> Box:
    points = [transform(m, x, y) for x, y in ((x1, y1), (x2, y1), (x1, y2), (x2, y2))]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def contains(outer: Box, inner: Box) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _num(value: Any) -> float:
    return float(value)


def _matrix(value: Any) -> Matrix:
    try:
        m = tuple(float(v) for v in value)
    except Exception:
        return IDENTITY
    return m if len(m) == 6 else IDENTITY


class _Font:
    """How far each glyph of a font advances, and how tall its glyphs are, in text space."""

    def __init__(self, font: Any):
        self.problem = ""
        self.two_byte = False
        self.widths: dict[int, float] = {}
        self.default = 0.0
        self.scale = 0.001
        self.ascent, self.descent = 0.8, -0.2
        try:
            self._read(font)
        except Exception as e:
            self.problem = f"its font {self.name(font)} can't be read ({type(e).__name__})."

    @staticmethod
    def name(font: Any) -> str:
        try:
            return str(font.get("/BaseFont", "")).lstrip("/").split("+")[-1] or "without a name"
        except Exception:
            return "without a name"

    def _read(self, font: Any) -> None:
        subtype = str(font.get("/Subtype", ""))
        descriptor = font.get("/FontDescriptor")
        if subtype == "/Type0":
            if str(font.get("/Encoding", "")) != "/Identity-H":
                self.problem = f"its font {self.name(font)} uses a character map Riemann can't follow."
                return
            self.two_byte = True
            cid = font.DescendantFonts[0]
            descriptor = cid.get("/FontDescriptor")
            self.default = _num(cid.get("/DW", 1000)) * self.scale
            self._read_cid_widths(cid.get("/W"))
        elif subtype == "/Type3":
            matrix = _matrix(font.get("/FontMatrix"))
            self.scale = matrix[0]
            self._read_simple_widths(font)
            bbox = font.get("/FontBBox")
            if bbox is not None:
                y1, y2 = sorted((_num(bbox[1]) * matrix[3], _num(bbox[3]) * matrix[3]))
                self.ascent, self.descent = max(y2, 0.0), min(y1, 0.0)
            return
        elif "/Widths" in font:
            self._read_simple_widths(font)
        elif not self._read_standard_widths(font):
            self.problem = f"its font {self.name(font)} has no glyph widths."
            return
        if descriptor is not None:
            ascent = _num(descriptor.get("/Ascent", 0)) * 0.001
            descent = _num(descriptor.get("/Descent", 0)) * 0.001
            if ascent > 0:
                self.ascent = max(ascent, 0.75)
            if descent < 0:
                self.descent = min(descent, -0.2)
            if "/MissingWidth" in descriptor and subtype != "/Type0":
                self.default = _num(descriptor.MissingWidth) * self.scale

    def _read_simple_widths(self, font: Any) -> None:
        first = int(font.get("/FirstChar", 0))
        for i, w in enumerate(font.get("/Widths", [])):
            self.widths[first + i] = _num(w) * self.scale

    def _read_cid_widths(self, w: Any) -> None:
        if w is None:
            return
        items = list(w)
        i = 0
        while i < len(items):
            start = int(items[i])
            nxt = items[i + 1]
            if hasattr(nxt, "__iter__") and not isinstance(nxt, (int, float)):
                for k, width in enumerate(nxt):
                    self.widths[start + k] = _num(width) * self.scale
                i += 2
            else:
                end, width = int(nxt), _num(items[i + 2]) * self.scale
                for code in range(start, min(end, start + 65535) + 1):
                    self.widths[code] = width
                i += 3

    def _read_standard_widths(self, font: Any) -> bool:
        from pypdf._codecs import charset_encoding
        from pypdf._codecs.adobe_glyphs import adobe_glyphs
        from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS

        metrics = CORE_FONT_METRICS.get(self.name(font))
        if metrics is None:
            return False
        builtin = {"Symbol": "/Symbol", "ZapfDingbats": "/ZapfDingbats"}.get(self.name(font), "/StandardEncoding")
        encoding = font.get("/Encoding")
        base, differences = builtin, None
        if encoding is not None and hasattr(encoding, "get"):
            base = str(encoding.get("/BaseEncoding", builtin))
            differences = encoding.get("/Differences")
        elif encoding is not None:
            base = str(encoding)
        chars = list(charset_encoding.get(base, charset_encoding[builtin]))
        if differences is not None:
            code = 0
            for item in differences:
                if isinstance(item, int):
                    code = item
                    continue
                if 0 <= code < 256:
                    chars[code] = adobe_glyphs.get(str(item), "")
                code += 1
        widths = metrics.character_widths
        fallback = widths.get("default", 500)
        for code, char in enumerate(chars):
            self.widths[code] = widths.get(char, fallback) * self.scale
        self.default = fallback * self.scale
        descriptor = metrics.font_descriptor
        self.ascent = max(descriptor.ascent * 0.001, 0.75)
        self.descent = min(descriptor.descent * 0.001, -0.2)
        return True

    def codes(self, data: bytes) -> list[bytes]:
        if not self.two_byte:
            return [data[i : i + 1] for i in range(len(data))]
        return [data[i : i + 2] for i in range(0, len(data) - 1, 2)]

    def width(self, code: bytes) -> float:
        return self.widths.get(int.from_bytes(code, "big"), self.default)


class _State:
    __slots__ = ("ctm", "font", "rise", "size", "tc", "th", "tl", "tr", "tw")

    def __init__(self, ctm: Matrix = IDENTITY):
        self.ctm = ctm
        self.font: _Font | None = None
        self.size = 0.0
        self.tc = self.tw = self.tl = self.rise = 0.0
        self.th = 1.0
        self.tr = 0

    def copy(self) -> "_State":
        other = _State(self.ctm)
        for key in self.__slots__:
            setattr(other, key, getattr(self, key))
        return other


class _Redactor:
    """Rewrites one page's content (and the forms it draws) so nothing remains under the boxes."""

    def __init__(self, pdf: Any, page_no: int, boxes: list[Box]):
        self.pdf = pdf
        self.page_no = page_no
        self.boxes = boxes
        self.fonts: dict[Any, _Font] = {}
        self.fresh = 0

    def stop(self, reason: str) -> None:
        raise RedactionError(self.page_no, reason)

    def hits(self, box: Box) -> bool:
        return any(overlaps(box, b) for b in self.boxes)

    def covered(self, box: Box) -> bool:
        return any(contains(b, box) for b in self.boxes)

    def font(self, resources: Any, name: Any) -> _Font | None:
        try:
            obj = resources.Font[name]
        except Exception:
            return None
        key = obj.objgen if obj.is_indirect else id(obj)
        if key not in self.fonts:
            self.fonts[key] = _Font(obj)
        return self.fonts[key]

    def new_name(self, xobjects: Any) -> Any:
        import pikepdf

        while True:
            self.fresh += 1
            name = pikepdf.Name(f"/RmRedact{self.fresh}")
            if name not in xobjects:
                return name

    def run(
        self, instructions: Sequence[Any], resources: Any, state: _State, depth: int
    ) -> tuple[list[Any], Any, bool]:
        """Returns (new instructions, resources to use with them, whether anything changed)."""
        import pikepdf

        if depth > MAX_DEPTH:
            self.stop("its content nests too deeply.")
        out: list[Any] = []
        stack: list[_State] = []
        tm = tlm = IDENTITY
        path: list[Any] = []
        path_points: list[tuple[float, float]] = []
        clipping = False
        changed = False
        xobjects_copy = None

        for instruction in instructions:
            op = str(instruction.operator)
            operands = list(instruction.operands) if op != "INLINE IMAGE" else []

            if op in PATH_OPS or op in ("W", "W*"):
                path.append(instruction)
                if op == "re":
                    x, y, w, h = (_num(v) for v in operands)
                    path_points += [
                        transform(state.ctm, px, py) for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
                    ]
                elif op in ("W", "W*"):
                    clipping = True
                elif op != "h":
                    values = [_num(v) for v in operands]
                    path_points += [
                        transform(state.ctm, values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)
                    ]
                continue
            if op in PAINT_OPS:
                drop = False
                if op != "n" and not clipping and path_points:
                    xs, ys = [p[0] for p in path_points], [p[1] for p in path_points]
                    drop = self.covered((min(xs), min(ys), max(xs), max(ys)))
                if drop:
                    changed = True
                else:
                    out.extend(path)
                    out.append(instruction)
                path, path_points, clipping = [], [], False
                continue
            if path:
                out.extend(path)
                path, path_points, clipping = [], [], False

            if op == "q":
                stack.append(state.copy())
            elif op == "Q":
                if stack:
                    state = stack.pop()
            elif op == "cm" and len(operands) == 6:
                state.ctm = mul(tuple(_num(v) for v in operands), state.ctm)
            elif op == "gs" and operands:
                try:
                    gs = resources.ExtGState[operands[0]]
                    if "/Font" in gs:
                        state.font = _Font(gs.Font[0])
                        state.size = _num(gs.Font[1])
                except Exception:
                    pass
            elif op == "BT":
                tm = tlm = IDENTITY
            elif op == "Tf" and len(operands) == 2:
                state.font = self.font(resources, operands[0])
                state.size = _num(operands[1])
            elif op == "Tc" and operands:
                state.tc = _num(operands[0])
            elif op == "Tw" and operands:
                state.tw = _num(operands[0])
            elif op == "Tz" and operands:
                state.th = _num(operands[0]) / 100.0
            elif op == "TL" and operands:
                state.tl = _num(operands[0])
            elif op == "Ts" and operands:
                state.rise = _num(operands[0])
            elif op == "Tr" and operands:
                state.tr = int(_num(operands[0]))
            elif op in ("Td", "TD") and len(operands) == 2:
                tx, ty = _num(operands[0]), _num(operands[1])
                if op == "TD":
                    state.tl = -ty
                tlm = mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
                tm = tlm
            elif op == "Tm" and len(operands) == 6:
                tlm = tm = tuple(_num(v) for v in operands)
            elif op == "T*":
                tlm = mul((1.0, 0.0, 0.0, 1.0, 0.0, -state.tl), tlm)
                tm = tlm

            if op in SHOW_OPS:
                if op in ("'", '"'):
                    if op == '"':
                        state.tw, state.tc = _num(operands[0]), _num(operands[1])
                    tlm = mul((1.0, 0.0, 0.0, 1.0, 0.0, -state.tl), tlm)
                    tm = tlm
                replacement, tm = self.show(instruction, op, operands, state, tm)
                if replacement is None:
                    out.append(instruction)
                else:
                    out.extend(replacement)
                    changed = True
                continue

            if op == "INLINE IMAGE":
                box = envelope(state.ctm, 0, 0, 1, 1)
                if self.hits(box):
                    if not self.covered(box):
                        self.stop("an inline image is only partly covered by a mark.")
                    changed = True
                    continue
            elif op == "Do" and operands:
                try:
                    xobject = resources.XObject[operands[0]]
                except Exception:
                    xobject = None
                if xobject is not None:
                    result = self.draw(xobject, state, depth)
                    if result is not None:
                        changed = True
                        if xobjects_copy is None:
                            resources = self.copy_resources(resources)
                            xobjects_copy = resources.XObject
                        if result is False:
                            continue
                        name = self.new_name(xobjects_copy)
                        xobjects_copy[name] = result
                        out.append(pikepdf.ContentStreamInstruction([name], pikepdf.Operator("Do")))
                        continue
            out.append(instruction)

        out.extend(path)
        if xobjects_copy is not None:
            used = {str(i.operands[0]) for i in out if str(i.operator) == "Do" and i.operands}
            for name in list(xobjects_copy.keys()):
                if name not in used:
                    del xobjects_copy[name]
        return out, resources, changed

    def copy_resources(self, resources: Any) -> Any:
        import pikepdf

        copy = (
            pikepdf.Dictionary({k: v for k, v in resources.items()}) if resources is not None else pikepdf.Dictionary()
        )
        xobjects = copy.get("/XObject")
        copy.XObject = (
            pikepdf.Dictionary({k: v for k, v in xobjects.items()}) if xobjects is not None else pikepdf.Dictionary()
        )
        return copy

    def show(self, instruction: Any, op: str, operands: list[Any], state: _State, tm: Matrix):
        """Moves tm past a text-showing operator; returns (replacement instructions or None, new tm)."""
        import pikepdf

        if op == "TJ":
            items = list(operands[0]) if operands else []
        elif op == '"':
            items = [operands[2]] if len(operands) > 2 else []
        else:
            items = [operands[0]] if operands else []
        font, size, th = state.font, state.size, state.th
        if font is None:
            return None, tm
        if font.problem:
            self._check_unknown(font, items, state, tm)
            return None, tm
        trm_base = (size * th, 0.0, 0.0, size, 0.0, state.rise)
        pieces: list[tuple[str, Any]] = []
        removed_any = False
        for item in items:
            if isinstance(item, pikepdf.String):
                data = bytes(item)
                if font.two_byte and len(data) % 2:
                    self.stop(f"a string in font {font.name} has an odd number of bytes.")
                for code in font.codes(data):
                    w0 = font.width(code)
                    trm = mul(trm_base, mul(tm, state.ctm))
                    glyph = envelope(trm, 0.0, font.descent, max(w0, 0.0), font.ascent)
                    space = state.tw if (not font.two_byte and code == b" ") else 0.0
                    tx = (w0 * size + state.tc + space) * th
                    hit = self.hits(glyph)
                    if hit and state.tr >= 4:
                        self.stop("text under a mark is used as a clipping path.")
                    pieces.append(("cut" if hit else "keep", (code, tx)))
                    removed_any = removed_any or hit
                    tm = mul((1.0, 0.0, 0.0, 1.0, tx, 0.0), tm)
            else:
                n = _num(item)
                pieces.append(("num", n))
                tm = mul((1.0, 0.0, 0.0, 1.0, -n / 1000.0 * size * th, 0.0), tm)
        if not removed_any:
            return None, tm
        if size * th == 0:
            self.stop("text under a mark has a zero font size.")
        array: list[Any] = []
        kept = b""
        gap = 0.0
        for kind, value in pieces:
            if kind == "keep":
                if gap:
                    array.append(gap)
                    gap = 0.0
                kept += value[0]
                continue
            if kept:
                array.append(pikepdf.String(kept))
                kept = b""
            if kind == "cut":
                gap += -value[1] * 1000.0 / (size * th)
            else:
                gap += value
        if kept:
            array.append(pikepdf.String(kept))
        if gap:
            array.append(gap)
        replacement = []
        if op == '"':
            replacement += [
                pikepdf.ContentStreamInstruction([operands[0]], pikepdf.Operator("Tw")),
                pikepdf.ContentStreamInstruction([operands[1]], pikepdf.Operator("Tc")),
            ]
        if op in ("'", '"'):
            replacement.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("T*")))
        replacement.append(pikepdf.ContentStreamInstruction([pikepdf.Array(array)], pikepdf.Operator("TJ")))
        return replacement, tm

    def _check_unknown(self, font: _Font, items: list[Any], state: _State, tm: Matrix) -> None:
        """A font whose glyph widths are unknown is fine only while its text starts well clear of every mark."""
        import pikepdf

        glyphs = sum(len(bytes(i)) for i in items if isinstance(i, pikepdf.String))
        m = mul(tm, state.ctm)
        scale = max(abs(m[0]) + abs(m[2]), abs(m[1]) + abs(m[3]), 1e-6)
        reach = abs(state.size) * max(abs(state.th), 1.0) * scale * (glyphs + 2)
        x, y = transform(m, 0.0, 0.0)
        area = (x - reach, y - reach, x + reach, y + reach)
        if self.hits(area):
            self.stop(f"text near a mark uses a font Riemann can't measure: {font.problem}")

    def draw(self, xobject: Any, state: _State, depth: int):
        """None to leave a Do alone, False to drop it, or a replacement XObject."""
        subtype = str(xobject.get("/Subtype", ""))
        if subtype == "/Image":
            box = envelope(state.ctm, 0, 0, 1, 1)
            if not self.hits(box):
                return None
            if self.covered(box):
                return False
            return self.black_out(xobject, state.ctm)
        if subtype == "/Form":
            import pikepdf

            ctm = mul(_matrix(xobject.get("/Matrix")), state.ctm)
            bbox = xobject.get("/BBox")
            if bbox is not None:
                x1, y1, x2, y2 = (_num(v) for v in bbox)
                area = envelope(ctm, min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
                if not self.hits(area):
                    return None
            inner = state.copy()
            inner.ctm = ctm
            resources = xobject.get("/Resources")
            ops, new_resources, changed = self.run(pikepdf.parse_content_stream(xobject), resources, inner, depth + 1)
            if not changed:
                return None
            copy = pikepdf.Stream(self.pdf, pikepdf.unparse_content_stream(ops))
            for key, value in xobject.items():
                if key not in ("/Length", "/Filter", "/DecodeParms", "/Resources"):
                    copy[key] = value
            if new_resources is not None:
                copy.Resources = new_resources
            return self.pdf.make_indirect(copy)
        return None

    def black_out(self, image: Any, ctm: Matrix):
        """A copy of image with every pixel under a mark black (and opaque, through its soft mask)."""
        import pikepdf

        if image.get("/ImageMask", False) or "/Mask" in image:
            self.stop("a masked image is only partly covered by a mark.")
        try:
            pixels = pikepdf.PdfImage(image).as_pil_image()
        except Exception:
            self.stop("an image under a mark is in a format Riemann can't change.")
        copy = self._painted(pixels, ctm)
        smask = image.get("/SMask")
        if smask is not None:
            try:
                alpha = pikepdf.PdfImage(smask).as_pil_image().convert("L")
            except Exception:
                self.stop("an image under a mark has a soft mask Riemann can't change.")
            copy.SMask = self._painted(alpha, ctm, opaque=True)
        for key in ("/Interpolate", "/Intent"):
            if key in image:
                copy[key] = image[key]
        return copy

    def _painted(self, pixels: Any, ctm: Matrix, opaque: bool = False):
        import numpy as np
        import pikepdf

        if pixels.mode not in ("L", "RGB", "CMYK"):
            pixels = pixels.convert("RGB" if pixels.mode not in ("1", "LA", "I", "I;16") else "L")
        width, height = pixels.size
        array = np.array(pixels)
        us = (np.arange(width) + 0.5) / width
        vs = 1.0 - (np.arange(height) + 0.5) / height
        u, v = np.meshgrid(us, vs)
        a, b, c, d, e, f = ctm
        x, y = a * u + c * v + e, b * u + d * v + f
        pad_x = (abs(a) + abs(c)) / width / 2
        pad_y = (abs(b) + abs(d)) / height / 2
        mask = np.zeros((height, width), dtype=bool)
        for x1, y1, x2, y2 in self.boxes:
            mask |= (x + pad_x > x1) & (x - pad_x < x2) & (y + pad_y > y1) & (y - pad_y < y2)
        if pixels.mode == "CMYK":
            array[mask] = (0, 0, 0, 255)
        elif pixels.mode == "L":
            array[mask] = 255 if opaque else 0
        else:
            array[mask] = 0
        spaces = {"L": "/DeviceGray", "RGB": "/DeviceRGB", "CMYK": "/DeviceCMYK"}
        stream = pikepdf.Stream(self.pdf, zlib.compress(np.ascontiguousarray(array).tobytes()))
        stream.Type = pikepdf.Name.XObject
        stream.Subtype = pikepdf.Name.Image
        stream.Width, stream.Height, stream.BitsPerComponent = width, height, 8
        stream.ColorSpace = pikepdf.Name(spaces[pixels.mode])
        stream.Filter = pikepdf.Name.FlateDecode
        return self.pdf.make_indirect(stream)


def _remove_annotations(pdf: Any, page: Any, boxes: list[Box], keep: Iterable[str]) -> None:
    import pikepdf

    annots = page.obj.get("/Annots")
    if annots is None:
        return
    keep_names = {NAME_PREFIX + str(k) for k in keep}
    kept, gone = [], set()
    for annot in annots:
        name = str(annot.get("/NM", ""))
        subtype = str(annot.get("/Subtype", ""))
        rect = annot.get("/Rect")
        box = None
        if rect is not None:
            x1, y1, x2, y2 = (_num(v) for v in rect)
            box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        own_mark = name.startswith(NAME_PREFIX) and subtype == "/Redact"
        touches = box is not None and any(overlaps(box, b) for b in boxes)
        if name in keep_names or not (own_mark or touches):
            kept.append(annot)
        elif annot.is_indirect:
            gone.add(annot.objgen)
    kept = [a for a in kept if not (a.get("/Parent") is not None and a.Parent.is_indirect and a.Parent.objgen in gone)]
    page.obj.Annots = pikepdf.Array(kept)
    acro = pdf.Root.get("/AcroForm")
    if gone and acro is not None and "/Fields" in acro:
        acro.Fields = pikepdf.Array(_prune_fields(acro.Fields, gone))


def _prune_fields(fields: Any, gone: set) -> list[Any]:
    import pikepdf

    out = []
    for field in fields:
        if field.is_indirect and field.objgen in gone:
            continue
        kids = field.get("/Kids")
        if kids is not None:
            remaining = _prune_fields(kids, gone)
            if not remaining:
                continue
            field.Kids = pikepdf.Array(remaining)
        out.append(field)
    return out


def _display_size(space: Any) -> tuple[float, float]:
    return (space.h, space.w) if space.rotate % 180 == 90 else (space.w, space.h)


def _display_boxes(space: Any, rects: list[list[float]]) -> list[Box]:
    width, height = _display_size(space)
    out = []
    for u1, v1, u2, v2 in rects:
        out.append((min(u1, u2) * width, (1 - max(v1, v2)) * height, max(u1, u2) * width, (1 - min(v1, v2)) * height))
    return out


def _chars(path: str, password: str | None, pages: Iterable[int]) -> dict[int, list[tuple[str, Box]]]:
    import riemann_core

    doc = riemann_core.PdfEngine().load_document(path, password)
    try:
        out = {}
        for page in pages:
            boxes = []
            for text, (x1, top, x2, bottom) in doc.get_char_boxes(page):
                if text.strip():
                    boxes.append((text, (min(x1, x2), min(top, bottom), max(x1, x2), max(top, bottom))))
            out[page] = boxes
        return out
    finally:
        del doc


def _mostly_inside(char: Box, boxes: list[Box]) -> bool:
    area = max((char[2] - char[0]) * (char[3] - char[1]), 1e-9)
    for b in boxes:
        w = min(char[2], b[2]) - max(char[0], b[0])
        h = min(char[3], b[3]) - max(char[1], b[1])
        if w > 0 and h > 0 and w * h / area > 0.5:
            return True
    return False


def _clear_of(char: Box, boxes: list[Box]) -> bool:
    pad = max(2.0, (char[3] - char[1]) * 0.25)
    grown = (char[0] - pad, char[1] - pad, char[2] + pad, char[3] + pad)
    return not any(overlaps(grown, b) for b in boxes)


def verify_redaction(
    before: dict[int, list[tuple[str, Box]]], after: dict[int, list[tuple[str, Box]]], boxes: dict[int, list[Box]]
) -> None:
    """Raises RedactionError unless the marks are empty of text and nothing else moved or went missing."""
    for page, marks in boxes.items():
        for text, box in after.get(page, []):
            if _mostly_inside(box, marks):
                raise RedactionError(page, f"text ({text!r}) was still found under a mark after removing it.")
        remaining: dict[str, list[Box]] = {}
        for text, box in after.get(page, []):
            remaining.setdefault(text, []).append(box)
        for text, box in before.get(page, []):
            if not _clear_of(box, marks):
                continue
            candidates = remaining.get(text, [])
            match = next(
                (
                    i
                    for i, c in enumerate(candidates)
                    if all(abs(c[k] - box[k]) <= POSITION_TOLERANCE for k in range(4))
                ),
                None,
            )
            if match is None:
                raise RedactionError(page, f"text away from the marks ({text!r}) moved or went missing.")
            candidates.pop(match)


def apply_redactions(
    path: str,
    marks: dict[int, list[list[float]]],
    keep: Iterable[str] = (),
    password: str | None = None,
) -> dict[int, int | None]:
    """
    Removes everything under marks ({page index: [relative rects]}) from the PDF
    at path, which is replaced only after the result checks out. keep lists
    Riemann annotation ids to leave in place. Returns the identity page mapping.
    """
    import pikepdf

    pages = sorted(p for p, rects in marks.items() if rects)
    with _open(path, password) as pdf:
        count = len(pdf.pages)
        encrypted = pdf.is_encrypted
        user_boxes: dict[int, list[Box]] = {}
        display_boxes: dict[int, list[Box]] = {}
        for page_no in pages:
            if not 0 <= page_no < count:
                raise PageEditError("Page out of range.")
            page = pdf.pages[page_no]
            space = _page_space(page)
            boxes = [space.rect(*rect) for rect in marks[page_no]]
            user_boxes[page_no] = boxes
            display_boxes[page_no] = _display_boxes(space, marks[page_no])
            redactor = _Redactor(pdf, page_no, boxes)
            resources = page.obj.get("/Resources")
            if resources is None:
                resources = _inherited(page.obj, "/Resources")
            ops, new_resources, _ = redactor.run(pikepdf.parse_content_stream(page), resources, _State(), 0)
            black = "\n".join(f"{x1:.4f} {y1:.4f} {x2 - x1:.4f} {y2 - y1:.4f} re f" for x1, y1, x2, y2 in boxes)
            content = b"q\n" + pikepdf.unparse_content_stream(ops) + b"\nQ\nq 0 g\n" + black.encode() + b"\nQ\n"
            page.obj.Contents = pdf.make_indirect(
                pikepdf.Stream(pdf, zlib.compress(content), Filter=pikepdf.Name.FlateDecode)
            )
            if new_resources is not None:
                page.obj.Resources = new_resources
            if "/Thumb" in page.obj:
                del page.obj["/Thumb"]
            _remove_annotations(pdf, page, boxes, keep)

        before = _chars(path, password, pages)
        folder = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".riemann-", suffix=".pdf", dir=folder)
        os.close(fd)
        try:
            pdf.save(tmp, encryption=bool(encrypted))
        except BaseException:
            os.remove(tmp)
            raise
    try:
        verify_redaction(before, _chars(tmp, password, pages), display_boxes)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return {i: i for i in range(count)}
