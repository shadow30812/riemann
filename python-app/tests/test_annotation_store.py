"""Tests for riemann/core/annotation_store.py: where Riemann's annotation copies live."""

import json
import os
import shutil

import pikepdf
import pytest

from riemann.core import annotation_store
from riemann.core.annotation_store import AnnotationStore, pdf_identifier


def make_pdf(path, ident=None):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    if ident is None:
        pdf.save(path, static_id=False)
    else:
        pdf.trailer.ID = pikepdf.Array([pikepdf.String(bytes.fromhex(ident)), pikepdf.String(bytes.fromhex(ident))])
        pdf.save(path, fix_metadata_version=False, static_id=False, deterministic_id=False)
    return str(path)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(annotation_store.Path, "home", lambda: tmp_path / "home")
    return AnnotationStore(str(tmp_path / "data" / "annotations"))


def write(path, pages):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"pages": pages}, f)


def read(path):
    with open(path) as f:
        return json.load(f)["pages"]


def test_identifier_is_read_from_the_trailer(tmp_path):
    p = make_pdf(tmp_path / "a.pdf")
    ident = pdf_identifier(p)
    assert ident and len(ident) == 32
    with pikepdf.open(p) as pdf:
        assert bytes(pdf.trailer.ID[0]).hex() == ident


def test_moving_a_file_keeps_its_annotations(store, tmp_path):
    a = make_pdf(tmp_path / "a.pdf")
    write(store.json_path(a), {"0": [{"id": "x"}]})
    moved = str(tmp_path / "sub" / "renamed.pdf")
    os.makedirs(os.path.dirname(moved))
    shutil.move(a, moved)
    assert read(store.json_path(moved)) == {"0": [{"id": "x"}]}


def test_a_copy_starts_with_the_same_annotations_then_diverges(store, tmp_path):
    a = make_pdf(tmp_path / "a.pdf")
    write(store.json_path(a), {"0": [{"id": "x"}]})
    b = str(tmp_path / "b.pdf")
    shutil.copyfile(a, b)
    copy_json = store.json_path(b)
    assert copy_json != store.json_path(a)
    assert read(copy_json) == {"0": [{"id": "x"}]}
    write(copy_json, {"0": []})
    assert read(store.json_path(a)) == {"0": [{"id": "x"}]}


def test_a_file_replaced_by_another_document_starts_fresh(store, tmp_path):
    a = make_pdf(tmp_path / "a.pdf", ident="11" * 16)
    write(store.json_path(a), {"0": [{"id": "x"}]})
    os.remove(a)
    make_pdf(tmp_path / "a.pdf", ident="22" * 16)
    assert pdf_identifier(a) == "22" * 16
    assert not os.path.exists(store.json_path(a))


def test_old_copies_are_migrated(store, tmp_path):
    a = make_pdf(tmp_path / "a.pdf")
    legacy = annotation_store.legacy_json_path(os.path.abspath(a))
    write(legacy, {"2": [{"id": "old"}]})
    assert read(store.json_path(a)) == {"2": [{"id": "old"}]}
    assert not os.path.exists(legacy)


def test_documents_without_an_identifier_are_keyed_by_path(store, tmp_path):
    epub = tmp_path / "book.md"
    epub.write_text("# hi")
    path = store.json_path(str(epub))
    assert os.path.basename(path).startswith("path-")
    assert store.json_path(str(epub)) == path


def test_moved_updates_the_index(store, tmp_path):
    md = tmp_path / "notes.md"
    md.write_text("x")
    old_json = store.json_path(str(md))
    write(old_json, {"0": [{"id": "n"}]})
    new = tmp_path / "renamed.md"
    os.rename(md, new)
    store.moved(str(md), str(new))
    assert store.json_path(str(new)) == old_json
