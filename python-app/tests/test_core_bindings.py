"""
Tests of the riemann_core extension itself (PDFium bindings), using small
PDFs generated with pikepdf.
"""

import pikepdf
import pytest

riemann_core = pytest.importorskip("riemann_core")


@pytest.fixture(scope="module")
def engine():
    return riemann_core.PdfEngine()


@pytest.fixture
def encrypted_pdf(tmp_path):
    path = str(tmp_path / "locked.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.save(path, encryption=pikepdf.Encryption(owner="owner", user="secret"))
    return path


def test_encrypted_document_opens_with_its_password(engine, encrypted_pdf):
    doc = engine.load_document(encrypted_pdf, "secret")
    assert doc.page_count == 1
    assert doc.get_page_size(0) == pytest.approx((200, 300))
    result = doc.render_page(0, 1.0, 0)
    assert (result.width, result.height) == (200, 300)
    assert len(result.data) == 200 * 300 * 4


def test_wrong_password_raises(engine, encrypted_pdf):
    with pytest.raises(OSError):
        engine.load_document(encrypted_pdf, "wrong")


def test_password_with_nul_is_rejected_not_a_panic(engine, encrypted_pdf):
    with pytest.raises(ValueError):
        engine.load_document(encrypted_pdf, "sec\0ret")


def test_many_password_loads_keep_working(engine, encrypted_pdf):
    for _ in range(50):
        assert engine.load_document(encrypted_pdf, "secret").page_count == 1


def test_render_is_capped_for_huge_pages(engine, tmp_path, qapp):
    from riemann.ui.reader.render_worker import MAX_PAGE_PIXELS, page_image

    path = str(tmp_path / "a0.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(2384, 3370))
    pdf.save(path)
    doc = engine.load_document(path)

    res = doc.render_page(0, 10.0, 0)
    assert res.width * res.height <= MAX_PAGE_PIXELS
    assert res.scale < 10.0
    assert res.width == int(2384 * res.scale)
    img = page_image(res, 10.0)
    assert img.devicePixelRatio() == pytest.approx(res.scale / 10.0)
    assert img.width() / img.devicePixelRatio() == pytest.approx(2384 * 10.0, rel=1e-3)


def test_normal_pages_render_at_the_requested_scale(engine, encrypted_pdf, qapp):
    from riemann.ui.reader.render_worker import page_image

    res = engine.load_document(encrypted_pdf, "secret").render_page(0, 4.0, 0)
    assert res.scale == 4.0
    assert page_image(res, 4.0).devicePixelRatio() == 1.0


def test_pages_past_65535_can_be_reached(engine, tmp_path):
    path = str(tmp_path / "long.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 150))
    page = pdf.pages[0].obj
    kids = pdf.Root.Pages.Kids
    for _ in range(70_000 - 1):
        kids.append(page)
    pdf.Root.Pages.Count = 70_000
    pdf.save(path)
    doc = engine.load_document(path, None)
    assert doc.page_count == 70_000
    assert doc.get_page_size(69_999) == (100.0, 150.0)
    assert doc.render_page(66_000, 0.5, 0).width == 50
    with pytest.raises(ValueError):
        doc.get_page_size(70_000)
    with pytest.raises(OverflowError):
        doc.get_page_size(-1)
