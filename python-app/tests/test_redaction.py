"""
Redaction: marks made in the reader, and (further down) applying them.

The marks tests drive the reader's own handlers; the applying tests check the
saved file itself, decompressed, since a redaction that only hides text is
worse than none.
"""

import os
import shutil
import sys
import time
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from text_fixtures import all_stream_bytes, make_text_pdf, rel_box

from riemann.core import annotation_store, pdf_annotations, redaction
from riemann.ui.reader.tab import ReaderTab


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


def open_pdf(qapp, reader, path):
    reader.load_document(path)
    pump(qapp, lambda: reader.current_doc is not None and 0 in reader.page_widgets)
    pump(qapp, lambda: reader.page_widgets[0].width() > 0)
    return reader.page_widgets[0]


def mouse(kind, pos):
    point = QPointF(pos)
    return QMouseEvent(
        kind, point, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )


def test_dragging_with_the_redact_tool_marks_a_box(qapp, reader, tmp_path):
    page = open_pdf(qapp, reader, make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")]))
    reader.toggle_annotation_mode(True)
    reader.set_tool("redact")
    w, h = page.width(), page.height()
    start, end = QPoint(int(w * 0.6), int(h * 0.4)), QPoint(int(w * 0.2), int(h * 0.1))
    reader.eventFilter(page, mouse(QEvent.Type.MouseButtonPress, start))
    reader.eventFilter(page, mouse(QEvent.Type.MouseMove, end))
    reader.eventFilter(page, mouse(QEvent.Type.MouseButtonRelease, end))

    marks = reader.annotations["0"]
    assert [m["type"] for m in marks] == ["redact"]
    l, t, r, b = marks[0]["rects"][0]
    assert l < r and t < b
    assert l == pytest.approx(0.2, abs=0.01) and b == pytest.approx(0.4, abs=0.01)


def test_a_click_without_a_drag_marks_nothing(qapp, reader, tmp_path):
    page = open_pdf(qapp, reader, make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")]))
    reader.toggle_annotation_mode(True)
    reader.set_tool("redact")
    reader.eventFilter(page, mouse(QEvent.Type.MouseButtonPress, QPoint(50, 50)))
    reader.eventFilter(page, mouse(QEvent.Type.MouseButtonRelease, QPoint(50, 50)))
    assert not reader.annotations.get("0")


def test_selected_text_becomes_redaction_marks(qapp, reader, tmp_path):
    page = open_pdf(qapp, reader, make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")]))
    w = page.width()
    page.set_text_selection([QRect(int(w * 0.1), 20, int(w * 0.2), 30)])
    reader.current_selected_text = "SECRET"
    with patch.object(reader, "show_toast"):
        reader.mark_selection_for_redaction()
    marks = reader.annotations["0"]
    assert marks[0]["type"] == "redact" and len(marks[0]["rects"]) == 1
    assert not page.selected_text_rects


def test_the_eraser_removes_a_redaction_mark(qapp, reader, tmp_path):
    page = open_pdf(qapp, reader, make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")]))
    reader._add_redaction_mark(0, [[0.1, 0.1, 0.4, 0.2]])
    reader._handle_eraser_click(page, QPoint(int(page.width() * 0.25), int(page.height() * 0.15)), 0)
    assert not reader.annotations["0"]


CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


def chars(path, page=0, password=None):
    import riemann_core

    doc = riemann_core.PdfEngine().load_document(path, password)
    out = [(t, b) for t, b in doc.get_char_boxes(page) if t.strip()]
    del doc
    return out


def word_box(path, word, page=0):
    """The PDF user-space box (unrotated page) around the first occurrence of word, from PDFium."""
    found = chars(path, page)
    text = "".join(t for t, _ in found)
    start = text.index(word)
    boxes = [b for _, b in found[start : start + len(word)]]
    return min(b[0] for b in boxes), min(b[3] for b in boxes), max(b[2] for b in boxes), max(b[1] for b in boxes)


def shrink(box, by=1.0):
    return box[0] + by, box[1] + by, box[2] - by, box[3] - by


def test_redacted_text_is_gone_from_the_file_and_the_rest_stays_put(tmp_path):
    path = make_text_pdf(
        tmp_path / "a.pdf", [(72, 700, 24, "KEEP SECRET KEEP"), (72, 650, 12, "next line")], compress=False
    )
    before = chars(path)
    box = shrink(word_box(path, "SECRET"))
    redaction.apply_redactions(path, {0: [rel_box(*box)]})

    assert b"SECRET" not in all_stream_bytes(path)
    after = chars(path)
    assert "".join(t for t, _ in after) == "KEEPKEEPnextline"
    kept = [(t, b) for t, b in before if t in "KEEPnextline" and not (box[0] - 1 < b[0] < box[2] + 1)]
    for (t, b), (t2, b2) in zip(kept, after, strict=False):
        assert t == t2 and all(abs(x - y) <= 0.01 for x, y in zip(b, b2, strict=False))


def test_redaction_paints_a_black_box(tmp_path):
    import riemann_core

    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")])
    redaction.apply_redactions(path, {0: [rel_box(100, 300, 200, 400)]})
    doc = riemann_core.PdfEngine().load_document(path, None)
    r = doc.render_page(0, 1.0, 0)
    data = bytes(r.data)

    def pixel(x, y):
        return data[(y * r.width + x) * 4 : (y * r.width + x) * 4 + 3]

    assert pixel(150, 792 - 350) == b"\0\0\0"
    assert pixel(400, 792 - 350) == b"\xff\xff\xff"


def test_image_pixels_under_a_mark_are_blacked_out(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [], image=(100, 100, 200, 200))
    redaction.apply_redactions(path, {0: [rel_box(100, 100, 200, 300)]})
    with pikepdf.open(path) as pdf:
        images = list(pdf.pages[0].images.values())
        assert len(images) == 1
        pixels = pikepdf.PdfImage(images[0]).as_pil_image()
    assert pixels.getpixel((0, 0)) == 0 and pixels.getpixel((3, 7)) == 0
    assert pixels.getpixel((4, 0)) == 180 and pixels.getpixel((7, 7)) == 180


def test_an_image_inside_a_mark_is_dropped_and_paths_inside_too(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [], image=(100, 100, 50, 50), shape=(300, 300, 20, 20))
    redaction.apply_redactions(path, {0: [rel_box(90, 90, 160, 160), rel_box(290, 290, 330, 330)]})
    with pikepdf.open(path) as pdf:
        content = pdf.pages[0].Contents.read_bytes()
    assert b"/Im1 Do" not in content and b"Do" not in content
    assert b"300 300 20 20 re" not in content


def test_a_path_only_partly_under_a_mark_stays(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [], shape=(300, 300, 100, 20))
    redaction.apply_redactions(path, {0: [rel_box(290, 290, 330, 330)]})
    with pikepdf.open(path) as pdf:
        assert b"300 300 100 20 re" in pdf.pages[0].Contents.read_bytes()


def test_redaction_on_a_rotated_page(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "KEEP SECRET KEEP")], rotate=90)
    with pikepdf.open(path) as pdf:
        space = pdf_annotations._page_space(pdf.pages[0])
    x1, y1, x2, y2 = shrink(word_box_user(path, "SECRET"))
    u1, v1 = space.from_pdf(x1, y1)
    u2, v2 = space.from_pdf(x2, y2)
    redaction.apply_redactions(path, {0: [[min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2)]]})
    assert b"SECRET" not in all_stream_bytes(path)
    assert "".join(t for t, _ in chars(path)) == "KEEPKEEP"


def word_box_user(path, word):
    """The user-space box of word from the content stream's own coordinates (Helvetica 24 at x=72, y=700)."""
    from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS

    widths = CORE_FONT_METRICS["Helvetica"].character_widths
    text = "KEEP SECRET KEEP"
    start = text.index(word)
    x = 72 + sum(widths[c] for c in text[:start]) * 24 / 1000
    w = sum(widths[c] for c in word) * 24 / 1000
    return x, 700 - 5, x + w, 700 + 17


def make_form_pdf(path, pages=2):
    """Pages that all draw one shared Form XObject holding the text."""
    pdf = pikepdf.new()
    font = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    form = pikepdf.Stream(pdf, b"BT /F1 24 Tf 0 0 Td (KEEP SECRET KEEP) Tj ET")
    form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    form.BBox = pikepdf.Array([0, -10, 300, 30])
    form.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    form = pdf.make_indirect(form)
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm1=form))
        page.obj.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b"q 1 0 0 1 72 700 cm /Fm1 Do Q"))
    pdf.save(path)
    return str(path)


def test_text_inside_a_shared_form_is_redacted_on_its_page_only(tmp_path):
    path = make_form_pdf(tmp_path / "a.pdf")
    x1, y1, x2, y2 = word_box_user(path, "SECRET")
    redaction.apply_redactions(path, {0: [rel_box(x1 + 1, y1, x2 - 1, y2)]})
    assert "".join(t for t, _ in chars(path, 0)) == "KEEPKEEP"
    assert "".join(t for t, _ in chars(path, 1)) == "KEEPSECRETKEEP"


def test_a_font_that_cant_be_measured_stops_apply_and_names_the_page(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")])
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Resources.Font.F1.BaseFont = pikepdf.Name("/SomeUnknownFont")
        pdf.save(path)
    before = open(path, "rb").read()
    with pytest.raises(redaction.RedactionError) as error:
        redaction.apply_redactions(path, {0: [rel_box(70, 690, 200, 720)]})
    assert "Page 1" in str(error.value)
    assert open(path, "rb").read() == before
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".riemann-")]


def test_a_font_that_cant_be_measured_far_from_the_mark_is_fine(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 100, 12, "far away")])
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Resources.Font.F1.BaseFont = pikepdf.Name("/SomeUnknownFont")
        pdf.save(path)
    redaction.apply_redactions(path, {0: [rel_box(300, 600, 400, 700)]})


def test_annotations_touching_a_mark_go_and_kept_ids_stay(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")])
    mark = rel_box(70, 690, 200, 720)
    pdf_annotations.write_annotations(path, {"0": [
        {"id": "m", "type": "redact", "rects": [mark]},
        {"id": "near", "type": "note", "rel_pos": [0.2, 0.1], "text": "on it"},
        {"id": "far", "type": "note", "rel_pos": [0.8, 0.9], "text": "elsewhere"},
        {"id": "over", "type": "markup", "subtype": "highlight", "rects": [mark]},
    ]})
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        link = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[80, 695, 150, 715])
        pdf.pages[0].Annots.append(pdf.make_indirect(link))
        pdf.save(path)
    redaction.apply_redactions(path, {0: [mark]}, keep=["over"])
    left = pdf_annotations.read_annotations(path)["0"]
    assert sorted(a["id"] for a in left) == ["far", "over"]
    with pikepdf.open(path) as pdf:
        assert all(str(a.Subtype) != "/Link" for a in pdf.pages[0].Annots)


def test_an_encrypted_file_stays_encrypted(tmp_path):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "KEEP SECRET KEEP")])
    box = shrink(word_box(path, "SECRET"))
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(user="pw", owner="pw"))
    redaction.apply_redactions(path, {0: [rel_box(*box)]}, password="pw")
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(path)
    assert "".join(t for t, _ in chars(path, password="pw")) == "KEEPKEEP"


def test_a_failed_check_leaves_the_file_alone(tmp_path, monkeypatch):
    path = make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "KEEP SECRET KEEP")])
    before = open(path, "rb").read()
    monkeypatch.setattr(redaction, "_mostly_inside", lambda char, boxes: True)
    with pytest.raises(redaction.RedactionError):
        redaction.apply_redactions(path, {0: [rel_box(*shrink(word_box(path, "SECRET")))]})
    assert open(path, "rb").read() == before
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".riemann-")]


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_a_word_in_a_real_paper_is_removed_and_its_neighbours_kept(tmp_path):
    path = str(tmp_path / "paper.pdf")
    shutil.copyfile(CORPUS, path)
    before = "".join(t for t, _ in chars(path, 3))
    word = "multiplication"
    with pikepdf.open(path) as pdf:
        space = pdf_annotations._page_space(pdf.pages[3])
    x1, y1, x2, y2 = shrink(word_box(path, word, 3), 0.5)
    rect = [x1 / space.w, 1 - y2 / space.h, x2 / space.w, 1 - y1 / space.h]
    redaction.apply_redactions(path, {3: [rect]})
    after = "".join(t for t, _ in chars(path, 3))
    assert after == before.replace(word, "", 1)


def click(monkeypatch, label):
    asked = []

    def exec_(box):
        asked.append(box.text())
        return 0

    def clicked(box):
        return next((b for b in box.buttons() if b.text() == label), None)

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec", exec_)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.clickedButton", clicked)
    return asked


def marked_reader(qapp, reader, tmp_path):
    path = make_text_pdf(tmp_path / "doc.pdf", [(72, 700, 24, "KEEP SECRET KEEP")], compress=False)
    box = shrink(word_box(path, "SECRET"))
    open_pdf(qapp, reader, path)
    reader._add_redaction_mark(0, [rel_box(*box)])
    reader._add_anno_data(0, {"type": "note", "rel_pos": [(box[0] + 5) / 612, 1 - (box[1] + 5) / 792], "text": "on it"})
    reader._add_anno_data(0, {"type": "note", "rel_pos": [0.9, 0.9], "text": "elsewhere"})
    return path


def test_apply_removes_the_text_in_the_scratch_copy_until_save(qapp, reader, tmp_path, monkeypatch):
    path = marked_reader(qapp, reader, tmp_path)
    asked = click(monkeypatch, "Apply")
    with patch.object(reader, "show_toast"):
        reader.apply_redactions()
    pump(qapp, lambda: reader.current_doc is not None)

    assert "1 redaction mark on page 1" in asked[0]
    assert reader.has_unsaved_page_edits()
    assert b"SECRET" in all_stream_bytes(path)
    assert b"SECRET" not in all_stream_bytes(reader.pdf_path())
    assert [a["text"] for a in reader.annotations["0"]] == ["elsewhere"]

    with patch.object(reader, "show_toast"):
        assert reader.save_page_edits()
    pump(qapp, lambda: reader.current_doc is not None)
    reader.flush_annotations_to_pdf()
    assert b"SECRET" not in all_stream_bytes(path)
    assert "".join(t for t, _ in chars(path)) == "KEEPKEEP"


def test_discard_after_apply_brings_the_original_back(qapp, reader, tmp_path, monkeypatch):
    path = marked_reader(qapp, reader, tmp_path)
    click(monkeypatch, "Apply")
    with patch.object(reader, "show_toast"):
        reader.apply_redactions()
    pump(qapp, lambda: reader.current_doc is not None)
    reader.discard_page_edits()
    assert not reader.has_unsaved_page_edits()
    assert b"SECRET" in all_stream_bytes(path)
    assert [a["type"] for a in reader.annotations["0"]] == ["redact", "note", "note"]


def test_cancelling_apply_changes_nothing(qapp, reader, tmp_path, monkeypatch):
    path = marked_reader(qapp, reader, tmp_path)
    click(monkeypatch, "Cancel")
    reader.apply_redactions()
    assert not reader.has_unsaved_page_edits()
    assert len(reader.annotations["0"]) == 3
    assert b"SECRET" in all_stream_bytes(path)


def test_apply_with_nothing_marked_says_so(qapp, reader, tmp_path):
    open_pdf(qapp, reader, make_text_pdf(tmp_path / "a.pdf", [(72, 700, 24, "SECRET")]))
    with patch.object(reader, "show_toast") as toast:
        reader.apply_redactions()
    assert "Nothing is marked" in toast.call_args.args[0]
    assert not reader.has_unsaved_page_edits()
