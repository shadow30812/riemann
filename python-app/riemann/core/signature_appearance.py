"""
Status-aware appearances for signature fields.

Acrobat's older signing tools store a layered appearance (n0-n4). Layer n1
is a "validity unknown" icon (a yellow question mark) and n4 often holds
text like "Signature Not Verified". Acrobat never shows those layers as
stored: after validating it draws its own result in their place. Viewers
that simply render the stored appearance, PDFium included, show the "?"
forever.

This module builds, for each such field, a one-page PDF holding a copy of
the field's appearance with n1 replaced by the validation result and n4
blanked, over the page content behind the field. The reader renders it with PDFium and paints it over the field, so
the signer text and fonts stay exactly as stored. The signed file itself is
never modified.
"""

import io

import pikepdf

from .pdf_annotations import _page_space

VALID = "VALID"
INVALID = "INVALID"

_TICK = "0.42 0.76 0.44"
_CROSS = "0.90 0.42 0.40"


def _layer_parents(xobject, depth: int = 0) -> list[pikepdf.Dictionary]:
    """XObject resource dictionaries, nested inside xobject, that name n1 or n4."""
    found = []
    resources = xobject.get("/Resources")
    if depth > 3 or not isinstance(resources, pikepdf.Dictionary):
        return found
    xobjects = resources.get("/XObject")
    if not isinstance(xobjects, pikepdf.Dictionary):
        return found
    if "/n1" in xobjects or "/n4" in xobjects:
        found.append(xobjects)
    for name in list(xobjects.keys()):
        child = xobjects[name]
        if isinstance(child, pikepdf.Stream):
            found.extend(_layer_parents(child, depth + 1))
    return found


def _bbox(stream) -> tuple[float, float, float, float]:
    try:
        x1, y1, x2, y2 = (float(v) for v in stream.get("/BBox"))
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    except Exception:
        return 0.0, 0.0, 100.0, 100.0


def _icon(status: str, box: tuple[float, float, float, float]) -> bytes:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    side = min(w, h) * 0.8
    ox, oy = x0 + (w - side) / 2, y0 + (h - side) / 2

    def pt(u, v):
        return f"{ox + u * side:.2f} {oy + v * side:.2f}"

    width = side * 0.08
    if status == VALID:
        path = f"{pt(0.12, 0.52)} m {pt(0.40, 0.22)} l {pt(0.90, 0.80)} l S"
        color = _TICK
    else:
        path = f"{pt(0.18, 0.18)} m {pt(0.82, 0.82)} l S {pt(0.18, 0.82)} m {pt(0.82, 0.18)} l S"
        color = _CROSS
    return f"q {color} RG {width:.2f} w 1 J 1 j {path} Q".encode()


def _form_to_rect(appearance, rect: tuple[float, float, float, float]) -> str:
    """The cm that places a form XObject in a rectangle (PDF 32000 12.5.5)."""
    bx0, by0, bx1, by1 = _bbox(appearance)
    try:
        a, b, c, d, e, f = (float(v) for v in appearance.get("/Matrix"))
    except Exception:
        a, b, c, d, e, f = 1, 0, 0, 1, 0, 0
    xs, ys = [], []
    for x, y in ((bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1)):
        xs.append(a * x + c * y + e)
        ys.append(b * x + d * y + f)
    tw, th = (max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0
    rx0, ry0, rx1, ry1 = rect
    sx, sy = (rx1 - rx0) / tw, (ry1 - ry0) / th
    return f"{sx:.6f} 0 0 {sy:.6f} {rx0 - min(xs) * sx:.4f} {ry0 - min(ys) * sy:.4f} cm"


def _status_pdf(src: pikepdf.Pdf, index: int, widget, rect, status: str) -> bytes | None:
    """
    The page cropped to the field, without its annotations, with the
    status appearance drawn on top. Keeping the page content underneath
    means lines or backgrounds behind the field survive the overlay.
    """
    out = pikepdf.new()
    appearance = out.copy_foreign(widget.AP.N)
    parents = _layer_parents(appearance)
    if not parents:
        return None
    for xobjects in parents:
        if "/n1" in xobjects:
            n1 = xobjects["/n1"]
            n1.write(_icon(status, _bbox(n1)))
        if "/n4" in xobjects:
            xobjects["/n4"].write(b"")

    out.pages.append(src.pages[index])
    page = out.pages[-1]
    for key in ("/Annots", "/TrimBox", "/BleedBox", "/ArtBox"):
        if key in page.obj:
            del page.obj[key]
    page.obj.MediaBox = pikepdf.Array(list(rect))
    page.obj.CropBox = pikepdf.Array(list(rect))
    resources = page.obj.get("/Resources")
    if not isinstance(resources, pikepdf.Dictionary):
        resources = page.obj.Resources = pikepdf.Dictionary()
    if not isinstance(resources.get("/XObject"), pikepdf.Dictionary):
        resources.XObject = pikepdf.Dictionary()
    resources.XObject.RiemannSigStatus = appearance
    page.contents_add(out.make_stream(b"q\n"), prepend=True)
    placement = f"\nQ q {_form_to_rect(appearance, rect)} /RiemannSigStatus Do Q\n"
    page.contents_add(out.make_stream(placement.encode()))
    buffer = io.BytesIO()
    out.save(buffer)
    return buffer.getvalue()


def _field_name(widget) -> str:
    node, parts = widget, []
    for _ in range(16):
        if node is None:
            break
        if "/T" in node:
            parts.append(str(node.T))
        node = node.get("/Parent")
    return ".".join(reversed(parts))


def _is_signature(widget) -> bool:
    node = widget
    for _ in range(16):
        if node is None:
            return False
        if node.get("/FT") == pikepdf.Name.Sig:
            return True
        node = node.get("/Parent")
    return False


def status_appearances(path: str, statuses: dict[str, str]) -> list[dict]:
    """
    Returns {"page", "field", "rect", "pdf"} for each visible signature
    field whose stored appearance has validity layers and whose status in
    statuses is VALID or INVALID. rect is (left, top, right, bottom) relative
    to the page as displayed (its /Rotate applied); pdf is the replacement
    appearance as a one-page PDF of exactly that area.
    """
    wanted = {name: s for name, s in statuses.items() if s in (VALID, INVALID)}
    if not wanted:
        return []
    results = []
    with pikepdf.open(path) as pdf:
        for index, page in enumerate(pdf.pages):
            for widget in page.obj.get("/Annots", []):
                try:
                    if widget.get("/Subtype") != pikepdf.Name.Widget or not _is_signature(widget):
                        continue
                    name = _field_name(widget)
                    status = wanted.get(name) or wanted.get(name.rsplit(".", 1)[-1])
                    if status is None or "/AP" not in widget or "/N" not in widget.AP:
                        continue
                    x1, y1, x2, y2 = (float(v) for v in widget.Rect)
                    rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
                    if rect[2] - rect[0] < 1 or rect[3] - rect[1] < 1:
                        continue
                    space = _page_space(page)
                    data = _status_pdf(pdf, index, widget, rect, status)
                    if data is None:
                        continue
                    u1, v1 = space.from_pdf(rect[0], rect[1])
                    u2, v2 = space.from_pdf(rect[2], rect[3])
                    results.append(
                        {
                            "page": index,
                            "field": name,
                            "rect": (min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2)),
                            "pdf": data,
                        }
                    )
                except Exception as e:
                    print(f"Skipping signature appearance on page {index + 1}: {e}")
    return results
