"""Tests for saving form fields into PDFs (riemann/core/pdf_forms.py)."""

import pikepdf

from form_fixtures import make_form_pdf
from riemann.core.pdf_forms import read_form_values, write_form_values


def test_values_are_saved_incrementally_and_read_back(tmp_path):
    path = make_form_pdf(str(tmp_path / "form.pdf"))
    with open(path, "rb") as f:
        original = f.read()
    result = write_form_values(path, {(0, 0): "Ada Lovelace", (0, 1): True, (0, 3): True})
    assert result == "ok"
    with open(path, "rb") as f:
        data = f.read()
    assert data.startswith(original)
    assert read_form_values(path) == {"name": "Ada Lovelace", "agree": True, "size": "Large"}
    with pikepdf.open(path) as pdf:
        ap = pdf.pages[0].Annots[0].AP.N.read_bytes()
        assert b"Ada Lovelace" in ap
        assert str(pdf.pages[0].Annots[3].AS) == "/Large"


def test_values_survive_another_round_and_pdfium_reads_them(tmp_path):
    import riemann_core

    path = make_form_pdf(str(tmp_path / "form.pdf"))
    assert write_form_values(path, {(0, 0): "first"}) == "ok"
    assert write_form_values(path, {(0, 0): "second", (0, 1): False}) == "ok"
    assert read_form_values(path)["name"] == "second"
    doc = riemann_core.PdfEngine().load_document(path, None)
    widgets = doc.get_form_widgets(0)
    assert widgets[0][3] == "second"


def test_signed_forms_are_left_alone(tmp_path):
    path = make_form_pdf(str(tmp_path / "form.pdf"))
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.AcroForm.SigFlags = 3
        pdf.save(path)
    with open(path, "rb") as f:
        before = f.read()
    assert write_form_values(path, {(0, 0): "x"}) == "blocked"
    with open(path, "rb") as f:
        assert f.read() == before


def test_copies_get_the_values(tmp_path):
    path = make_form_pdf(str(tmp_path / "form.pdf"))
    dest = str(tmp_path / "copy.pdf")
    assert write_form_values(path, {(0, 0): "copy only"}, dest=dest) == "ok"
    assert read_form_values(dest)["name"] == "copy only"
    assert read_form_values(path)["name"] == ""
