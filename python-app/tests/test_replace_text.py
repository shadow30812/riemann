"""Replace text: the overlay's look, how it is stored in the PDF, and removing the original with it."""

import sys
import time
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect
from PySide6.QtWidgets import QApplication

from riemann.core import annotation_store, pdf_annotations
from riemann.ui.reader import replace_text
from riemann.ui.reader.tab import ReaderTab
from text_fixtures import all_stream_bytes, make_text_pdf


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def reader(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(annotation_store, "_shared", annotation_store.AnnotationStore(str(tmp_path / "store")))
    settings = MagicMock()
    settings.return_value.value.side_effect = lambda key, default=None, type=None: default
    with patch("riemann.ui.reader.tab.app_settings", settings), patch(
        "riemann.ui.reader.mixins.appearance.app_settings", settings
    ):
        tab = ReaderTab()
        tab.resize(900, 700)
        tab.show()
        yield tab
        tab.cleanup()
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pump(qapp, until, seconds=15):
    end = time.time() + seconds
    while time.time() < end and not until():
        qapp.processEvents()
        time.sleep(0.01)
    assert until()


@pytest.mark.parametrize(
    "name, serif, fixed, bold, italic, expected",
    [
        ("Helvetica", False, False, False, False, "Helvetica"),
        ("ABCDEF+NimbusRomNo9L-Regu", False, False, False, False, "Times-Roman"),
        ("Times-Bold", False, False, False, False, "Times-Bold"),
        ("SomeFont", True, False, False, True, "Times-Italic"),
        ("DejaVuSansMono-Bold", False, False, False, False, "Courier-Bold"),
        ("Unknown", False, True, False, False, "Courier"),
        ("PTSans-Italic", False, False, False, False, "Helvetica-Oblique"),
    ],
)
def test_the_overlay_font_follows_the_original(name, serif, fixed, bold, italic, expected):
    assert replace_text.standard_font(name, serif, fixed, bold, italic) == expected


def test_the_background_is_the_commonest_colour_around_the_selection():
    width, height = 20, 20
    tint = bytes([200, 230, 250, 255])
    pixels = bytearray(tint * (width * height))
    for x in range(5, 15):
        i = (4 * width + x) * 4
        pixels[i : i + 4] = bytes([0, 0, 0, 255])
    assert replace_text.ring_colour(bytes(pixels), width, height, (6, 6, 14, 14)) == "#fae6c8"


def test_an_overlay_is_sized_from_the_selection_and_widened_for_longer_text():
    styles = [("A", (100.0, 720.0, 110.0, 692.0), 24.0, "Times-Roman", False, False, False, False, "#112233")]
    rect = [100 / 612, 1 - 720 / 792, 110 / 612, 1 - 692 / 792]
    overlay = replace_text.overlay_from_selection([rect], styles, (612.0, 792.0), "A much longer line", "#fafafa")
    assert overlay["font"] == "Times-Roman" and overlay["size"] == 24.0 and overlay["color"] == "#112233"
    l, t, r, b = overlay["rects"][0]
    assert l == pytest.approx(rect[0]) and t == pytest.approx(rect[1])
    assert (r - l) * 612 >= replace_text.text_width("A much longer line", "Times-Roman", 24)


def overlay_anno(rect=(0.1, 0.1, 0.4, 0.15)):
    return {
        "id": "rep", "type": "replace", "rects": [list(rect)], "text": "New words", "font": "Times-Bold",
        "size": 14.0, "baseline": 11.0, "leading": 16.8, "color": "#000000", "background": "#fdf6e3",
    }


def test_the_overlay_round_trips_and_paints_its_background_first(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 14, "Old words")])
    pdf_annotations.write_annotations(path, {"0": [overlay_anno()]})
    assert pdf_annotations.read_annotations(path)["0"][0]["text"] == "New words"
    with pikepdf.open(path) as pdf:
        annot = pdf.pages[0].Annots[0]
        assert annot.Subtype == "/FreeText"
        stream = annot.AP.N.read_bytes().decode("latin-1")
        assert str(annot.AP.N.Resources.Font.RmF.BaseFont) == "/Times-Bold"
    fill = stream.index("re f")
    assert fill < stream.index("BT") < stream.index("(New words) Tj")
    assert stream.startswith("0.992 0.965 0.890 rg")


@pytest.mark.parametrize("rotate, matrix", [(0, None), (90, [0, 1, -1, 0, 0, 0]), (270, [0, -1, 1, 0, 0, 0])])
def test_on_a_rotated_page_the_overlay_turns_with_the_page(tmp_path, rotate, matrix):
    import riemann_core

    path = make_text_pdf(tmp_path / "a.pdf", [], rotate=rotate)
    rect = (0.1, 0.1, 0.5, 0.2)
    pdf_annotations.write_annotations(path, {"0": [overlay_anno(rect)]})
    with pikepdf.open(path) as pdf:
        ap = pdf.pages[0].Annots[0].AP.N
        assert (list(map(float, ap.Matrix)) if "/Matrix" in ap else None) == matrix
        bbox = [float(v) for v in ap.BBox]
    w, h = (792, 612) if rotate % 180 else (612, 792)
    assert bbox[2] == pytest.approx(0.4 * w, abs=0.01) and bbox[3] == pytest.approx(0.1 * h, abs=0.01)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        del pdf.pages[0].Annots[0]["/NM"]
        pdf.save(path)
    doc = riemann_core.PdfEngine().load_document(path, None)
    r = doc.render_page(0, 1.0, 0)
    data = bytes(r.data)
    x, y = int(0.12 * r.width), int(0.15 * r.height)
    assert data[(y * r.width + x) * 4 : (y * r.width + x) * 4 + 3] == bytes([0xE3, 0xF6, 0xFD])


def select(qapp, reader, path, word_rect):
    reader.load_document(path)
    pump(qapp, lambda: reader.current_doc is not None and 0 in reader.page_widgets)
    page = reader.page_widgets[0]
    pump(qapp, lambda: page.width() > 0)
    w, h = page.width(), page.height()
    l, t, r, b = word_rect
    page.set_text_selection([QRect(int(l * w), int(t * h), int((r - l) * w), int((b - t) * h))])
    reader.current_selected_text = "SECRET"
    return page


def secret_rect(path):
    import riemann_core

    doc = riemann_core.PdfEngine().load_document(path, None)
    boxes = [b for t, b in doc.get_char_boxes(0) if t.strip()][4:10]
    del doc
    return (boxes[0][0] + 1) / 612, 1 - max(b[1] for b in boxes) / 792, (boxes[-1][2] - 1) / 612, 1 - min(b[3] for b in boxes) / 792


def dialog(text, remove):
    fake = MagicMock()
    fake.exec.return_value = True
    fake.text.return_value = text
    fake.remove_original.return_value = remove
    return patch("riemann.ui.reader.mixins.annotations.ReplaceTextDialog", return_value=fake)


def test_replace_text_adds_an_overlay_matching_the_original(qapp, reader, tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "KEEP SECRET KEEP")], font="Times-Roman")
    select(qapp, reader, path, secret_rect(path))
    with dialog("PUBLIC", False):
        reader.replace_selected_text()
    annos = reader.annotations["0"]
    assert [a["type"] for a in annos] == ["replace"]
    assert annos[0]["text"] == "PUBLIC" and annos[0]["font"] == "Times-Roman"
    assert annos[0]["size"] == pytest.approx(24.0)
    assert annos[0]["background"] == "#ffffff"


def test_removing_the_original_too_leaves_only_the_new_text(qapp, reader, tmp_path, monkeypatch):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "KEEP SECRET KEEP")], compress=False)
    select(qapp, reader, path, secret_rect(path))
    with dialog("PUBLIC", True):
        reader.replace_selected_text()
    assert [a["type"] for a in reader.annotations["0"]] == ["replace", "redact"]

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec", lambda box: 0)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.clickedButton",
        lambda box: next(b for b in box.buttons() if b.text() == "Apply"),
    )
    with patch.object(reader, "show_toast"):
        reader.apply_redactions()
        pump(qapp, lambda: reader.current_doc is not None)
        assert reader.save_page_edits()
    pump(qapp, lambda: reader.current_doc is not None)
    reader.flush_annotations_to_pdf()
    reader._store_annotations_in_pdf(blocking=True)

    assert b"SECRET" not in all_stream_bytes(path)
    left = pdf_annotations.read_annotations(path)["0"]
    assert [a["type"] for a in left] == ["replace"] and left[0]["text"] == "PUBLIC"
