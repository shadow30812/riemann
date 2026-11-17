"""Tests for riemann/core/page_edit.py."""

import os

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


def sign(path):
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.AcroForm = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array())
        pdf.save(path)


def lock(path, password="secret"):
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(user=password, owner=password))


def test_signed_files_can_be_edited_and_are_reported_as_signed(tmp_path):
    p = make(tmp_path / "a.pdf")
    sign(p)
    assert page_edit.document_state(p) == (False, True)
    page_edit.delete_pages(p, [0])
    assert widths(p) == [200, 300, 400]


def test_edits_to_an_encrypted_file_keep_its_password(tmp_path):
    p = make(tmp_path / "a.pdf")
    lock(p)
    assert page_edit.document_state(p, "secret") == (True, False)
    with pytest.raises(page_edit.PageEditError):
        page_edit.rotate_pages(p, [0], 90)
    page_edit.move_page(p, 0, 3, password="secret")
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(p)
    with pikepdf.open(p, password="secret") as pdf:
        assert [int(float(pg.mediabox[2])) for pg in pdf.pages] == [200, 300, 400, 100]


def test_remove_password_rewrites_the_file_without_encryption(tmp_path):
    p = make(tmp_path / "a.pdf")
    lock(p)
    assert page_edit.remove_password(p, "secret") == {0: 0, 1: 1, 2: 2, 3: 3}
    with pikepdf.open(p) as pdf:
        assert not pdf.is_encrypted
    assert widths(p) == [100, 200, 300, 400]


def test_an_unlocked_copy_leaves_the_original_locked(tmp_path):
    p = make(tmp_path / "a.pdf")
    lock(p)
    out = str(tmp_path / "a_unlocked.pdf")
    page_edit.save_unlocked_copy(p, out, "secret")
    with pikepdf.open(out) as pdf:
        assert not pdf.is_encrypted
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(p)
    with pytest.raises(page_edit.PageEditError):
        page_edit.save_unlocked_copy(p, p, "secret")


def test_remap_pages():
    pages = {"0": ["a"], "1": ["b"], "3": ["d"]}
    assert page_edit.remap_pages(pages, {0: 0, 1: None, 2: 1, 3: 2}) == {"0": ["a"], "2": ["d"]}


@pytest.mark.parametrize("edit", [
    lambda p: page_edit.rotate_pages(p, [0], 90),
    lambda p: page_edit.delete_pages(p, [1]),
    lambda p: page_edit.move_page(p, 0, 2),
])
def test_edits_keep_the_file_permissions(tmp_path, edit):
    import os
    import stat

    p = make(tmp_path / "shared.pdf")
    os.chmod(p, 0o664)
    edit(p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o664


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="counts open files through /proc")
def test_refused_edit_closes_the_file(tmp_path):
    p = make(tmp_path / "a.pdf")
    before = len(os.listdir("/proc/self/fd"))
    for edit in (lambda: page_edit.rotate_pages(p, [9], 90), lambda: page_edit.delete_pages(p, [0, 1, 2, 3])):
        with pytest.raises(page_edit.PageEditError):
            edit()
    assert len(os.listdir("/proc/self/fd")) == before
