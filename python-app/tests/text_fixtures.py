"""Small PDFs with known text (and optionally an image or a shape) for the redaction and replace-text tests."""

import zlib

import pikepdf
from pikepdf import Dictionary, Name

PAGE = (612, 792)


def make_text_pdf(path, lines, rotate=0, image=None, shape=None, font="Helvetica", compress=True):
    """
    lines: [(x, y, size, text)] drawn in font. image: (x, y, w, h) for a grey
    8x8 image. shape: (x, y, w, h) for a filled rectangle path.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=PAGE)
    page = pdf.pages[0]
    if rotate:
        page.obj.Rotate = rotate
    resources = Dictionary(
        Font=Dictionary(F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/" + font)))
    )
    ops = []
    for x, y, size, text in lines:
        ops.append(f"BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET")
    if shape:
        x, y, w, h = shape
        ops.append(f"0.2 0.4 0.8 rg {x} {y} {w} {h} re f")
    if image:
        x, y, w, h = image
        pixels = bytes([180] * 64)
        img = pikepdf.Stream(pdf, pixels)
        img.Type, img.Subtype = Name.XObject, Name.Image
        img.Width, img.Height, img.BitsPerComponent = 8, 8, 8
        img.ColorSpace = Name.DeviceGray
        resources.XObject = Dictionary(Im1=img)
        ops.append(f"q {w} 0 0 {h} {x} {y} cm /Im1 Do Q")
    page.obj.Resources = resources
    content = "\n".join(ops).encode()
    if compress:
        stream = pikepdf.Stream(pdf, zlib.compress(content))
        stream.Filter = Name.FlateDecode
    else:
        stream = pikepdf.Stream(pdf, content)
    page.obj.Contents = pdf.make_indirect(stream)
    pdf.save(path)
    return str(path)


def all_stream_bytes(path, password=None):
    """Every stream in the file, decompressed, joined: what a determined reader of the file could find."""
    out = []
    with pikepdf.open(path, password=password or "") as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream):
                try:
                    out.append(obj.read_bytes())
                except Exception:
                    out.append(obj.read_raw_bytes())
    with open(path, "rb") as f:
        out.append(f.read())
    return b"\n".join(out)


def rel_box(x1, y1, x2, y2, page=PAGE):
    """A PDF user-space box (bottom-left origin) as Riemann's relative top-left rect on an unrotated page."""
    w, h = page
    return [x1 / w, 1 - y2 / h, x2 / w, 1 - y1 / h]

