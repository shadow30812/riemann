"""Tests for riemann/core/page_edit.py."""

import pikepdf
import pytest

from riemann.core import page_edit


def make(path, widths=(100, 200, 300, 400)):
    pdf = pikepdf.new()
    for w in widths:
        pdf.add_blank_page(page_size=(w, 500))
    pdf.save(path)
    return str(path)


def widths(path):
    with pikepdf.open(path) as pdf:
        return [int(float(p.mediabox[2])) for p in pdf.pages]


def test_delete(tmp_path):
    p = make(tmp_path / "a.pdf")
    mapping = page_edit.delete_pages(p, [1, 3])
    assert widths(p) == [100, 300]
    assert mapping == {0: 0, 1: None, 2: 1, 3: None}
    with pytest.raises(page_edit.PageEditError):
        page_edit.delete_pages(p, [0, 1])


def test_rotate_one_page(tmp_path):
    p = make(tmp_path / "a.pdf")
    page_edit.rotate_pages(p, [2], 90)
    with pikepdf.open(p) as pdf:
        assert [int(pg.obj.get("/Rotate", 0)) for pg in pdf.pages] == [0, 0, 90, 0]


def test_move_and_reorder(tmp_path):
    p = make(tmp_path / "a.pdf")
    mapping = page_edit.move_page(p, 0, 3)
    assert widths(p) == [200, 300, 400, 100]
    assert mapping == {1: 0, 2: 1, 3: 2, 0: 3}
    page_edit.reorder_pages(p, [3, 0, 1, 2])
    assert widths(p) == [100, 200, 300, 400]
    with pytest.raises(page_edit.PageEditError):
        page_edit.reorder_pages(p, [0, 0, 1, 2])


def test_extract_and_insert(tmp_path):
    p = make(tmp_path / "a.pdf")
    out = str(tmp_path / "part.pdf")
    page_edit.extract_pages(p, [1, 2], out)
    assert widths(out) == [200, 300]
    assert widths(p) == [100, 200, 300, 400]
    mapping = page_edit.insert_pdf(p, out, 1)
    assert widths(p) == [100, 200, 300, 200, 300, 400]
    assert mapping == {0: 0, 1: 3, 2: 4, 3: 5}


def test_signed_files_are_refused(tmp_path):
    p = make(tmp_path / "a.pdf")
    with pikepdf.open(p, allow_overwriting_input=True) as pdf:
        pdf.Root.AcroForm = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array())
        pdf.save(p)
    with pytest.raises(page_edit.PageEditError):
        page_edit.delete_pages(p, [0])
    assert widths(p) == [100, 200, 300, 400]


def test_remap_pages():
    pages = {"0": ["a"], "1": ["b"], "3": ["d"]}
    assert page_edit.remap_pages(pages, {0: 0, 1: None, 2: 1, 3: 2}) == {"0": ["a"], "2": ["d"]}
