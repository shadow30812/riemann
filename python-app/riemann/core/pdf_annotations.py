"""
Native PDF storage for Riemann's annotations.

Riemann keeps its annotations as plain dicts (relative 0..1 page coordinates,
top-left origin). This module converts them to standard PDF annotations
(ISO 32000-1 §12.5) and back, so notes, highlights and drawings made in
Riemann open in Acrobat, Preview, Okular, browsers and so on, and comments
those apps add to Riemann's annotations come back.

Files are changed with an incremental update: the original bytes are left
untouched and only the new and changed objects, plus a cross-reference
section, are appended. That is how other editors save annotations, it keeps
existing digital signatures intact, and it is safe while PDFium still has the
file open.

Each annotation Riemann writes is named "riemann:<id>" (/NM) and carries the
original dict as JSON under /RiemannData, so it round-trips losslessly.

A redaction mark ("redact", with "rects" like markup) is a standard /Redact
annotation (§12.5.6.23): other apps list it as a pending redaction, and
/IC asks for a black box once applied. Marking removes nothing; applying
does (core/redaction.py).

A replace-text overlay ("replace") is a FreeText annotation whose appearance
fills its box with the page's background colour and then draws the new text
in a standard font (ui/reader/replace_text.py chooses the look). On a rotated
page the appearance is turned with the page, so the text reads upright.

New objects are told apart from existing ones by an unreferenced probe object
made before anything else: qpdf numbers new objects from the highest existing
one, not from the trailer's /Size, which may be larger.

Notes:

write_annotations: Append only if nobody changed the file while it was being
parsed.
"""

import io
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

NAME_PREFIX = "riemann:"
DATA_KEY = "/RiemannData"
RECT_KEY = "/RiemannRect"

Box = Tuple[float, float, float, float]
STANDARD_FONTS = {
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
}


def new_annotation_id() -> str:
    return uuid.uuid4().hex


def _pdf_date() -> str:
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    return now.strftime("D:%Y%m%d%H%M%S") + (f"{offset[:3]}'{offset[3:]}'" if offset else "")


def _hex_to_rgb(value: Any) -> List[float]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return [max(0.0, min(1.0, float(c) / 255.0)) for c in value[:3]]
    s = str(value or "").lstrip("#")
    if len(s) == 8:
        s = s[2:]
    if len(s) == 6:
        try:
            return [int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
        except ValueError:
            pass
    return [1.0, 0.85, 0.0]


def _rgb_to_hex(rgb) -> Optional[str]:
    try:
        r, g, b = (max(0, min(255, round(float(c) * 255))) for c in list(rgb)[:3])
    except (TypeError, ValueError):
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


class _PageSpace:
    """Maps Riemann's relative, top-left based page coordinates to PDF user space."""

    def __init__(self, crop: Box, rotate: int):
        self.left, self.bottom, self.right, self.top = crop
        self.w = (self.right - self.left) or 1.0
        self.h = (self.top - self.bottom) or 1.0
        self.rotate = rotate % 360

    def to_pdf(self, u: float, v: float) -> Tuple[float, float]:
        if self.rotate == 90:
            return self.left + v * self.w, self.bottom + u * self.h
        if self.rotate == 180:
            return self.right - u * self.w, self.bottom + v * self.h
        if self.rotate == 270:
            return self.right - v * self.w, self.top - u * self.h
        return self.left + u * self.w, self.top - v * self.h

    def from_pdf(self, x: float, y: float) -> Tuple[float, float]:
        if self.rotate == 90:
            return (y - self.bottom) / self.h, (x - self.left) / self.w
        if self.rotate == 180:
            return (self.right - x) / self.w, (y - self.bottom) / self.h
        if self.rotate == 270:
            return (self.top - y) / self.h, (self.right - x) / self.w
        return (x - self.left) / self.w, (self.top - y) / self.h

    def rect(self, u1: float, v1: float, u2: float, v2: float) -> Box:
        x1, y1 = self.to_pdf(u1, v1)
        x2, y2 = self.to_pdf(u2, v2)
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _inherited(page_obj, key: str):
    """A page attribute, looked up through the page tree as PDF inheritance requires."""
    node = page_obj
    for _ in range(64):
        if node is None:
            return None
        if key in node:
            return node[key]
        node = node.get("/Parent")
    return None


def _box(value) -> Optional[Box]:
    try:
        x1, y1, x2, y2 = (float(v) for v in value)
    except Exception:
        return None
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def visible_box(crop: Optional[Box], media: Optional[Box]) -> Box:
    """The crop box clipped to the media box, falling back as the Rust core does."""
    if crop and media:
        clipped = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
        if clipped[0] < clipped[2] and clipped[1] < clipped[3]:
            return clipped
    return media or crop or (0.0, 0.0, 612.0, 792.0)


def _page_space(page) -> _PageSpace:
    crop = visible_box(_box(_inherited(page.obj, "/CropBox")), _box(_inherited(page.obj, "/MediaBox")))
    try:
        rotate = int(_inherited(page.obj, "/Rotate") or 0)
    except Exception:
        rotate = 0
    return _PageSpace(crop, rotate)


def _text_stream_literal(text: str) -> str:
    safe = text.encode("latin-1", "replace").decode("latin-1")
    return "(" + safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _build_annotation(pdf, page, space: _PageSpace, anno: Dict[str, Any], author: str):
    """Returns a pikepdf Dictionary for one Riemann annotation, or None."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    atype = anno.get("type")
    subtype = str(anno.get("subtype", ""))
    rgb = _hex_to_rgb(anno.get("color"))
    thickness = float(anno.get("thickness", 2) or 2)
    contents = str(anno.get("text", "") or "")

    d = Dictionary(
        Type=Name.Annot,
        NM=String(NAME_PREFIX + str(anno["id"])),
        T=String(author),
        M=String(_pdf_date()),
        F=4,
        C=Array(rgb),
        P=page.obj,
    )
    d[Name(DATA_KEY)] = String(json.dumps(anno, separators=(",", ":")))

    def ink(strokes: List[List[Tuple[float, float]]], width: float, opacity: float = 1.0):
        pdf_strokes = [[c for u, v in s for c in space.to_pdf(u, v)] for s in strokes if s]
        if not pdf_strokes:
            return None
        xs = [p for s in pdf_strokes for p in s[0::2]]
        ys = [p for s in pdf_strokes for p in s[1::2]]
        pad = width + 1
        d.Subtype = Name.Ink
        d.InkList = Array([Array(s) for s in pdf_strokes])
        d.Rect = Array([min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad])
        d.BS = Dictionary(W=width, S=Name.S)
        if opacity < 1.0:
            d.CA = opacity
        return d

    if atype == "note":
        u, v = anno.get("rel_pos", (0.1, 0.1))
        x, y = space.to_pdf(float(u), float(v))
        d.Subtype = Name.Text
        d.Name = Name.Comment
        d.Contents = String(contents)
        d.Rect = Array([x - 10, y - 10, x + 10, y + 10])
        return d

    if atype == "text":
        u, v = anno.get("rel_pos", (0.1, 0.1))
        x, y = space.to_pdf(float(u), float(v))
        lines = contents.splitlines() or [""]
        size = 12.0
        width = max(40.0, max(len(line) for line in lines) * size * 0.55 + 8)
        height = len(lines) * size * 1.25 + 6
        rect = [x, y - height, x + width, y]
        d.Subtype = Name.FreeText
        d.Contents = String(contents)
        d.Rect = Array(rect)
        color_op = "{:.3f} {:.3f} {:.3f} rg".format(*rgb)
        d.DA = String(f"/Helv {size:g} Tf {color_op}")
        d.BS = Dictionary(W=0)
        ops = [f"BT /Helv {size:g} Tf {color_op} {size * 1.25:g} TL 4 {height - size - 2:g} Td"]
        ops += [f"{_text_stream_literal(line)} Tj T*" for line in lines]
        ops.append("ET")
        font = Dictionary(
            Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding
        )
        ap = pikepdf.Stream(pdf, "\n".join(ops).encode("latin-1", "replace"))
        ap.Type = Name.XObject
        ap.Subtype = Name.Form
        ap.BBox = Array([0, 0, width, height])
        ap.Resources = Dictionary(Font=Dictionary(Helv=font))
        d.AP = Dictionary(N=ap)
        return d

    if atype == "replace":
        rects = anno.get("rects") or []
        if not rects or len(rects[0]) != 4:
            return None
        x1, y1, x2, y2 = space.rect(*(float(c) for c in rects[0]))
        turned = space.rotate % 180 == 90
        dw, dh = (y2 - y1, x2 - x1) if turned else (x2 - x1, y2 - y1)
        size = float(anno.get("size", 12) or 12)
        leading = float(anno.get("leading", size * 1.2) or size * 1.2)
        baseline = float(anno.get("baseline", size) or size)
        font_name = str(anno.get("font", "Helvetica"))
        if font_name not in STANDARD_FONTS:
            font_name = "Helvetica"
        background = _hex_to_rgb(anno.get("background")) if anno.get("background") else [1.0, 1.0, 1.0]
        fill = "{:.3f} {:.3f} {:.3f} rg".format(*background)
        color_op = "{:.3f} {:.3f} {:.3f} rg".format(*rgb)
        lines = contents.splitlines() or [""]
        ops = [f"{fill} 0 0 {dw:g} {dh:g} re f", f"BT /RmF {size:g} Tf {color_op} {leading:g} TL 0 {dh - baseline:g} Td"]
        ops += [f"{_text_stream_literal(line)} Tj T*" for line in lines]
        ops.append("ET")
        d.Subtype = Name.FreeText
        d.Contents = String(contents)
        d.Rect = Array([x1, y1, x2, y2])
        d.DA = String(f"/RmF {size:g} Tf {color_op}")
        d.BS = Dictionary(W=0)
        d.IC = Array(background)
        font = Dictionary(
            Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/" + font_name), Encoding=Name.WinAnsiEncoding
        )
        ap = pikepdf.Stream(pdf, "\n".join(ops).encode("latin-1", "replace"))
        ap.Type = Name.XObject
        ap.Subtype = Name.Form
        ap.BBox = Array([0, 0, dw, dh])
        turn = {90: [0, 1, -1, 0, 0, 0], 180: [-1, 0, 0, -1, 0, 0], 270: [0, -1, 1, 0, 0, 0]}.get(space.rotate)
        if turn:
            ap.Matrix = Array(turn)
        ap.Resources = Dictionary(Font=Dictionary(RmF=font))
        d.AP = Dictionary(N=ap)
        return d

    if atype == "stamp":
        u, v = (float(c) for c in anno.get("rel_pos", (0.1, 0.1)))
        s = 0.012
        if subtype == "stamp_cross":
            strokes = [[(u - s, v - s), (u + s, v + s)], [(u - s, v + s), (u + s, v - s)]]
        else:
            strokes = [[(u - s, v), (u - s / 3, v + s), (u + s, v - s)]]
        return ink(strokes, 3.0)

    if atype == "drawing":
        pts = [(float(p[0]), float(p[1])) for p in anno.get("points", [])]
        if subtype in ("rect", "oval") and len(pts) >= 2:
            x1, y1, x2, y2 = space.rect(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
            d.Subtype = Name.Square if subtype == "rect" else Name.Circle
            d.Rect = Array([x1, y1, x2, y2])
            d.BS = Dictionary(W=thickness, S=Name.S)
            return d
        if subtype == "highlight":
            return ink([pts], thickness * 3, opacity=0.3)
        return ink([pts], thickness)

    if atype in ("markup", "redact"):
        quads, bounds = [], None
        for r in anno.get("rects", []):
            if len(r) != 4:
                continue
            x1, y1, x2, y2 = space.rect(*(float(c) for c in r))
            quads += [x1, y2, x2, y2, x1, y1, x2, y1]
            bounds = (
                (x1, y1, x2, y2)
                if bounds is None
                else (min(bounds[0], x1), min(bounds[1], y1), max(bounds[2], x2), max(bounds[3], y2))
            )
        if not quads:
            return None
        if atype == "redact":
            d.Subtype = Name.Redact
            d.IC = Array([0, 0, 0])
        else:
            d.Subtype = {"underline": Name.Underline, "strikeout": Name.StrikeOut}.get(
                subtype, Name.Highlight
            )
        d.QuadPoints = Array(quads)
        d.Rect = Array(list(bounds))
        if contents:
            d.Contents = String(contents)
        return d

    return None


def _is_riemann(annot) -> bool:
    try:
        name = annot.get("/NM")
        if name is not None and str(name).startswith(NAME_PREFIX):
            return True
        parent = annot.get("/Parent")
        return parent is not None and str(parent.get("/NM", "")).startswith(NAME_PREFIX)
    except Exception:
        return False


def serialize_objects(objects: Dict[Tuple[int, int], Any]) -> List[Tuple[int, int, bytes]]:
    """(number, generation, body) for each indirect object, ready for an incremental section."""
    import pikepdf

    serialized = []
    for (num, gen), obj in sorted(objects.items()):
        if isinstance(obj, pikepdf.Stream):
            raw = obj.read_raw_bytes()
            stream_dict = pikepdf.Dictionary({k: v for k, v in obj.stream_dict.items() if k != "/Length"})
            stream_dict.Length = len(raw)
            body = stream_dict.unparse(resolved=True) + b"\nstream\n" + raw + b"\nendstream"
        else:
            body = obj.unparse(resolved=True)
        serialized.append((num, gen, body))
    return serialized


def collect_new_objects(objects: Dict[Tuple[int, int], Any], root, first_new: int) -> None:
    """Adds root and every object reachable from it that was created after first_new."""
    import pikepdf

    containers = (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)

    def walk(obj):
        if not isinstance(obj, containers):
            return
        if obj.is_indirect:
            if obj.objgen in objects:
                return
            objects[obj.objgen] = obj
        items = obj.values() if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) else obj
        for child in items:
            if isinstance(child, containers):
                if child.is_indirect and child.objgen[0] < first_new:
                    continue
                walk(child)

    walk(root)


def incremental_save(path: str, original: bytes, pdf, objects, dest: Optional[str] = None) -> bool:
    """
    Appends the given objects of pdf (opened from original) to path as an
    incremental update, or writes original plus that update to dest. Returns
    False without writing when path changed since original was read.
    """
    trailer = {"/Size": str(int(pdf.trailer.get("/Size", 0))).encode()}
    for key in ("/Root", "/Info", "/ID"):
        if key in pdf.trailer:
            trailer[key] = pdf.trailer[key].unparse()
    section = _incremental_section(original, serialize_objects(objects), trailer)
    if dest:
        with open(dest, "wb") as f:
            f.write(original + section)
        return True
    if file_signature(path) is None or os.path.getsize(path) != len(original):
        return False
    with open(path, "ab") as f:
        f.write(section)
    return True


def _last_startxref(data: bytes) -> int:
    matches = list(re.finditer(rb"startxref\s+(\d+)", data[-4096:]))
    if not matches:
        matches = list(re.finditer(rb"startxref\s+(\d+)", data))
    if not matches:
        raise ValueError("no startxref")
    return int(matches[-1].group(1))


def _incremental_section(
    original: bytes, objects: List[Tuple[int, int, bytes]], trailer: Dict[str, bytes]
) -> bytes:
    """Builds the bytes to append: the objects, a cross-reference section and trailer."""
    prev = _last_startxref(original)
    uses_stream = not original[prev : prev + 4].startswith(b"xref")

    out = io.BytesIO()
    base = len(original)
    if not original.endswith((b"\n", b"\r")):
        out.write(b"\n")

    offsets: Dict[int, Tuple[int, int]] = {}
    for num, gen, body in objects:
        offsets[num] = (base + out.tell(), gen)
        out.write(b"%d %d obj\n" % (num, gen) + body + b"\nendobj\n")

    size = max([int(trailer["/Size"])] + [n + 1 for n in offsets])
    extra = b"".join(b"/%s %s " % (k.lstrip("/").encode(), v) for k, v in trailer.items() if k != "/Size")

    if uses_stream:
        xref_num = size
        size += 1
        xref_pos = base + out.tell()
        offsets[xref_num] = (xref_pos, 0)
        nums = sorted(offsets)
        rows = b"".join(
            b"\x01" + offsets[n][0].to_bytes(4, "big") + offsets[n][1].to_bytes(2, "big") for n in nums
        )
        index = b" ".join(b"%d 1" % n for n in nums)
        out.write(
            b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 2] /Index [%s] /Prev %d %s/Length %d >>\nstream\n"
            % (xref_num, size, index, prev, extra, len(rows))
            + rows
            + b"\nendstream\nendobj\n"
        )
    else:
        xref_pos = base + out.tell()
        out.write(b"xref\n")
        for n in sorted(offsets):
            pos, gen = offsets[n]
            out.write(b"%d 1\n%010d %05d n \n" % (n, pos, gen))
        out.write(b"trailer\n<< /Size %d /Prev %d %s>>\n" % (size, prev, extra))
    out.write(b"startxref\n%d\n%%%%EOF\n" % xref_pos)
    return out.getvalue()


def file_signature(path: str) -> Optional[List[int]]:
    """Size and modification time, used to tell whether another app changed the file."""
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def _is_signed(pdf) -> bool:
    acro = pdf.Root.get("/AcroForm")
    try:
        return acro is not None and bool(int(acro.get("/SigFlags", 0)) & 1)
    except Exception:
        return True


def incremental_updates(path: str) -> int:
    """How many times the file has been saved incrementally (end-of-file markers minus one)."""
    try:
        with open(path, "rb") as f:
            return max(0, f.read().count(b"%%EOF") - 1)
    except OSError:
        return 0


COMPACT_AFTER_UPDATES = 10


def compact(path: str) -> bool:
    """
    Rewrites the PDF in full, dropping the superseded objects that incremental
    saves leave behind. Skipped (False) for files Riemann may not change, such
    as signed ones, and when the file cannot be replaced (on Windows, while
    another program has it open). The original stays intact on any failure.
    """
    if not can_store_in(path):
        return False
    import tempfile

    import pikepdf

    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".riemann-", suffix=".pdf", dir=folder)
    os.close(fd)
    try:
        before = file_signature(path)
        with pikepdf.open(path) as pdf:
            pdf.save(tmp, object_stream_mode=pikepdf.ObjectStreamMode.generate)
        if file_signature(path) != before:
            raise OSError("file changed while compacting")
        try:
            import shutil

            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def can_store_in(path: str) -> bool:
    """Whether annotations can be written into this PDF (writable, not encrypted or signed)."""
    if not path.lower().endswith(".pdf") or not os.access(path, os.W_OK):
        return False
    try:
        import pikepdf

        with pikepdf.open(path) as pdf:
            if pdf.is_encrypted:
                return False
            acro = pdf.Root.get("/AcroForm")
            if acro is not None and int(acro.get("/SigFlags", 0)) & 1:
                return False
    except Exception:
        return False
    return True


def write_annotations(
    path: str,
    annotations: Dict[str, List[Dict[str, Any]]],
    author: str = "Riemann",
    dest: Optional[str] = None,
) -> bool:
    """
    Replaces the Riemann annotations stored in the PDF at path with the given
    ones, appending an incremental update. With dest, writes the updated
    document there instead and leaves path alone.

    Annotations without an "id" are given one in place.
    """
    import pikepdf

    with open(path, "rb") as f:
        original = f.read()

    with pikepdf.open(io.BytesIO(original)) as pdf:
        if pdf.is_encrypted:
            return False
        page_count = len(pdf.pages)
        wanted = {
            int(pid): annos for pid, annos in annotations.items() if annos and str(pid).isdigit()
        }

        first_new = pdf.make_indirect(pikepdf.Dictionary()).objgen[0]

        changed_pages = []
        for idx in range(page_count):
            page = pdf.pages[idx]
            existing = page.obj.get("/Annots")
            kept = []
            removed = False
            if existing is not None:
                for annot in existing:
                    if _is_riemann(annot):
                        removed = True
                    else:
                        kept.append(annot)
            new_annos = wanted.get(idx, [])
            if not removed and not new_annos:
                continue

            space = _page_space(page)
            for anno in new_annos:
                anno.setdefault("id", new_annotation_id())
                built = _build_annotation(pdf, page, space, anno, author)
                if built is not None:
                    built[pikepdf.Name(RECT_KEY)] = pikepdf.Array([float(v) for v in built.Rect])
                    kept.append(pdf.make_indirect(built))
            page.obj.Annots = pikepdf.Array(kept)
            changed_pages.append(page.obj)

        if not changed_pages:
            if dest:
                with open(dest, "wb") as f:
                    f.write(original)
            return True

        objects: Dict[Tuple[int, int], Any] = {}

        original_size = int(pdf.trailer.get("/Size", 0))
        for page_obj in changed_pages:
            objects[page_obj.objgen] = page_obj
            for annot in page_obj.Annots:
                if annot.is_indirect and annot.objgen[0] >= first_new:
                    collect_new_objects(objects, annot, first_new)

        serialized = serialize_objects(objects)

        trailer = {"/Size": str(original_size).encode()}
        for key in ("/Root", "/Info", "/ID"):
            if key in pdf.trailer:
                trailer[key] = pdf.trailer[key].unparse()
        signed = _is_signed(pdf)

    section = _incremental_section(original, serialized, trailer)
    if dest:
        if signed:
            with open(dest, "wb") as f:
                f.write(original + section)
        else:
            with pikepdf.open(io.BytesIO(original + section)) as updated:
                updated.save(dest, object_stream_mode=pikepdf.ObjectStreamMode.generate)
        return True

    if file_signature(path) is None or os.path.getsize(path) != len(original):
        return False
    with open(path, "ab") as f:
        f.write(section)
    return True


def read_annotations(path: str) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """
    Reads the Riemann annotations stored in a PDF, picking up edits other apps
    made to their text and colour. Returns None when the file holds none.
    """
    import pikepdf

    found: Dict[str, List[Dict[str, Any]]] = {}
    any_found = False
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        return None
    with pdf:
        for idx, page in enumerate(pdf.pages):
            annots = page.obj.get("/Annots")
            if annots is None:
                continue
            for annot in annots:
                try:
                    name = str(annot.get("/NM", ""))
                    if not name.startswith(NAME_PREFIX) or DATA_KEY not in annot:
                        continue
                    anno = json.loads(str(annot[DATA_KEY]))
                except Exception:
                    continue
                any_found = True
                anno["id"] = name[len(NAME_PREFIX) :]
                anno = _follow_move(anno, annot, page)
                if anno.get("type") in ("note", "text", "markup") and "/Contents" in annot:
                    anno["text"] = str(annot.Contents)
                color = _rgb_to_hex(annot.get("/C", []))
                if color and not isinstance(anno.get("color"), (list, tuple)):
                    anno["color"] = color
                found.setdefault(str(idx), []).append(anno)
    return found if any_found else None


def _shift(anno: Dict[str, Any], du: float, dv: float) -> None:
    if "rel_pos" in anno:
        u, v = anno["rel_pos"]
        anno["rel_pos"] = [float(u) + du, float(v) + dv]
    if "points" in anno:
        anno["points"] = [[float(p[0]) + du, float(p[1]) + dv, *p[2:]] for p in anno["points"]]
    if "rects" in anno:
        anno["rects"] = [
            [float(r[0]) + du, float(r[1]) + dv, float(r[2]) + du, float(r[3]) + dv] if len(r) == 4 else r
            for r in anno["rects"]
        ]


def _follow_move(anno: Dict[str, Any], annot, page) -> Dict[str, Any]:
    """
    Updates an annotation's position when another app moved or resized it.

    Riemann records the /Rect it wrote under /RiemannRect. A different /Rect
    now means the annotation was moved: rectangles and ovals take the new
    corners, everything else is shifted by how far the centre moved.
    """
    try:
        old = [float(v) for v in annot[RECT_KEY]]
        new = [float(v) for v in annot.Rect]
    except Exception:
        return anno
    if len(old) != 4 or len(new) != 4 or max(abs(a - b) for a, b in zip(old, new)) < 0.5:
        return anno
    space = _page_space(page)
    if anno.get("type") == "drawing" and anno.get("subtype") in ("rect", "oval"):
        x1, y1, x2, y2 = min(new[0], new[2]), min(new[1], new[3]), max(new[0], new[2]), max(new[1], new[3])
        corners = sorted([space.from_pdf(x1, y2), space.from_pdf(x2, y1)])
        anno["points"] = [list(c) for c in corners]
        return anno
    u0, v0 = space.from_pdf((old[0] + old[2]) / 2, (old[1] + old[3]) / 2)
    u1, v1 = space.from_pdf((new[0] + new[2]) / 2, (new[1] + new[3]) / 2)
    _shift(anno, u1 - u0, v1 - v0)
    return anno


def as_read_back(anno: Dict[str, Any]) -> Dict[str, Any]:
    """An annotation as read_annotations will return it after write_annotations stores it."""
    a = json.loads(json.dumps(anno))
    if a.get("type") in ("note", "text"):
        a["text"] = str(a.get("text", "") or "")
    if not isinstance(a.get("color"), (list, tuple)):
        color = _rgb_to_hex(_hex_to_rgb(a.get("color")))
        if color:
            a["color"] = color
    return a


def written_form(pages: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """The read-back form of a whole snapshot, kept as the base for merge_annotations."""
    return {str(pid): [as_read_back(a) for a in annos] for pid, annos in pages.items() if annos}


def merge_annotations(local, stored, base) -> Dict[str, List[Dict[str, Any]]]:
    """
    Three-way merge of Riemann's annotations with the set read from the PDF.

    base is what Riemann last wrote into the file (written_form). For each
    annotation ID: one edited since base wins over the unchanged one, an edit
    on both sides goes to the file's version, and a deletion on either side
    sticks. Without a base (copies from older versions), the file's version
    wins for shared IDs and both sides' other annotations are kept.
    """

    def by_id(pages):
        out = {}
        for pid, annos in (pages or {}).items():
            for a in annos or []:
                if isinstance(a, dict) and a.get("id"):
                    out[str(a["id"])] = (str(pid), a)
        return out

    L, S = by_id(local), by_id(stored)
    B = by_id(base) if base is not None else None
    order = list(L) + [i for i in S if i not in L]
    merged: Dict[str, List[Dict[str, Any]]] = {}
    for i in order:
        in_l, in_s = i in L, i in S
        known = B is not None and i in B
        if in_l and in_s:
            pick = L[i] if known and S[i] == B[i] else S[i]
        elif in_s:
            if known:
                continue
            pick = S[i]
        else:
            if known:
                continue
            pick = L[i]
        merged.setdefault(pick[0], []).append(pick[1])
    return merged
