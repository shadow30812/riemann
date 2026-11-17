"""
Tests for PDF compression and conversion (riemann/core/features.py), which
use pikepdf, Pillow and the Rust core instead of PyMuPDF. Fixtures are built
with pikepdf: one image XObject shared by three pages, so "each image is
re-encoded once" is checkable.
"""

import os
import zlib

import pikepdf
import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
riemann_core = pytest.importorskip("riemann_core")

from riemann.core.features import DocumentCompressor, DocumentConverter

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus")
ATTENTION = os.path.abspath(os.path.join(CORPUS, "attention.pdf"))


def _image_pdf(path, pages=3, alpha=False):
    gradient = (np.add.outer(np.arange(600), np.arange(800)) % 256).astype(np.uint8)
    noise = np.random.default_rng(0).integers(0, 40, gradient.shape, dtype=np.uint8)
    rgb = np.stack([gradient, gradient[::-1], gradient // 2 + noise], -1)
    pdf = pikepdf.new()
    image = pikepdf.Stream(pdf, zlib.compress(rgb.tobytes()))
    image.Type = pikepdf.Name.XObject
    image.Subtype = pikepdf.Name.Image
    image.Width, image.Height = 800, 600
    image.ColorSpace = pikepdf.Name.DeviceRGB
    image.BitsPerComponent = 8
    image.Filter = pikepdf.Name.FlateDecode
    if alpha:
        mask = pikepdf.Stream(pdf, zlib.compress(bytes([200]) * (800 * 600)))
        mask.Type = pikepdf.Name.XObject
        mask.Subtype = pikepdf.Name.Image
        mask.Width, mask.Height = 800, 600
        mask.ColorSpace = pikepdf.Name.DeviceGray
        mask.BitsPerComponent = 8
        mask.Filter = pikepdf.Name.FlateDecode
        image.SMask = mask
    image = pdf.make_indirect(image)
    for _ in range(pages):
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, 600, 800],
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image)),
            Contents=pdf.make_stream(b"q 600 0 0 800 0 0 cm /Im0 Do Q"),
        )
        pdf.pages.append(pikepdf.Page(page))
    pdf.save(path)


def _renders(path):
    doc = riemann_core.PdfEngine().load_document(path, None)
    return doc.page_count, [doc.render_page(i, 0.2, 0).width for i in range(doc.page_count)]


def test_lossy_compression_reencodes_a_shared_image_once(tmp_path, qapp):
    src, out = str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf")
    _image_pdf(src)

    assert DocumentCompressor.compress_pdf(src, out, lossless=False, quality=30) == 1

    assert os.path.getsize(out) < os.path.getsize(src) / 5
    count, widths = _renders(out)
    assert count == 3 and all(w > 0 for w in widths)
    with pikepdf.open(out) as pdf:
        images = {obj.objgen for p in pdf.pages for obj in p.images.values()}
        assert len(images) == 1
        assert pdf.pages[0].images["/Im0"].Filter == pikepdf.Name.DCTDecode


def test_lossy_compression_keeps_the_soft_mask(tmp_path, qapp):
    src, out = str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf")
    _image_pdf(src, pages=1, alpha=True)
    DocumentCompressor.compress_pdf(src, out, lossless=False, quality=30)
    with pikepdf.open(out) as pdf:
        image = pdf.pages[0].images["/Im0"]
        assert image.Filter == pikepdf.Name.DCTDecode
        assert "/SMask" in image


def test_lossless_compression_leaves_images_alone(tmp_path, qapp):
    src, out = str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf")
    _image_pdf(src)
    assert DocumentCompressor.compress_pdf(src, out, lossless=True) == 0
    with pikepdf.open(out) as pdf:
        assert pdf.pages[0].images["/Im0"].Filter == pikepdf.Name.FlateDecode
    assert _renders(out)[0] == 3


def test_images_to_pdf_makes_one_page_per_image(tmp_path, qapp):
    paths = []
    for i, (size, mode) in enumerate([((300, 200), "RGB"), ((100, 400), "RGBA"), ((50, 50), "L")]):
        p = str(tmp_path / f"{i}.png")
        Image.new(mode, size).save(p)
        paths.append(p)
    out = str(tmp_path / "out.pdf")
    DocumentConverter.images_to_pdf(paths, out)
    doc = riemann_core.PdfEngine().load_document(out, None)
    assert doc.page_count == 3
    assert [tuple(round(v) for v in doc.get_page_size(i)) for i in range(3)] == [(300, 200), (100, 400), (50, 50)]


@pytest.mark.skipif(not os.path.exists(ATTENTION), reason="benchmark corpus not downloaded")
def test_pdf_to_images_text_and_html(tmp_path, qapp):
    pngs = DocumentConverter.pdf_to_images(ATTENTION, str(tmp_path / "png"), dpi=36)
    doc = riemann_core.PdfEngine().load_document(ATTENTION, None)
    assert len(pngs) == doc.page_count
    with Image.open(pngs[0]) as first:
        assert first.size == (round(doc.get_page_size(0)[0] / 2), round(doc.get_page_size(0)[1] / 2))

    md = str(tmp_path / "out.md")
    DocumentConverter.pdf_to_text(ATTENTION, md, as_markdown=True)
    text = open(md, encoding="utf-8").read()
    assert text.startswith("# Extracted Content: attention.pdf")
    assert "Attention Is All You Need" in text
    assert text.count("\n---\n") == doc.page_count

    out = str(tmp_path / "out.html")
    DocumentConverter.pdf_to_html(ATTENTION, out)
    page = open(out, encoding="utf-8").read()
    assert page.count("<div class='page'") == doc.page_count
    assert "Attention" in page and "<script" not in page


def test_pdf_to_images_never_replaces_existing_images(tmp_path, qapp):
    out = tmp_path / "png"
    out.mkdir()
    keep = out / "attention_page_1.png"
    keep.write_bytes(b"mine")
    pngs = DocumentConverter.pdf_to_images(ATTENTION, str(out), dpi=18)
    assert keep.read_bytes() == b"mine"
    assert all(os.path.basename(p).startswith("attention (2)_page_") for p in pngs)


def test_compression_default_output_changes_only_the_file_name(tmp_path, qapp, monkeypatch):
    from riemann.core import features

    folder = tmp_path / "my.pdf.files"
    folder.mkdir()
    src = folder / "report.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    (folder / "report_compressed.pdf").write_bytes(b"older")
    seen = []
    monkeypatch.setattr(features.QFileDialog, "getSaveFileName", lambda parent, title, default, flt: (seen.append(default), ("", ""))[1])
    dialog = features.CompressDialog()
    dialog.txt_input.setText(str(src))
    dialog.browse_output()
    dialog.deleteLater()
    assert seen == [str(folder / "report_compressed (2).pdf")]
