"""
Notes:

test_rotated_page_mapping: Top-left of the displayed (rotated) page is the
bottom-left in user space.
"""

import os

import pikepdf
import pypdf
import pytest

from riemann.core import pdf_annotations as pa


def _make_pdf(path, pages=2, xref_streams=False):
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
    if xref_streams:
        pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    else:
        pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.disable)


ANNOS = {
    "0": [
        {"type": "note", "rel_pos": [0.3, 0.4], "text": "Sticky ü note", "color": "#ff0000"},
        {"type": "text", "rel_pos": [0.1, 0.9], "text": "Free text", "color": "#0000ff"},
        {
            "type": "markup",
            "subtype": "highlight",
            "rects": [[0.1, 0.2, 0.8, 0.25]],
            "text": "highlighted",
            "color": "#ffff00",
        },
        {
            "type": "drawing",
            "subtype": "pen",
            "points": [[0.1, 0.1], [0.2, 0.2]],
            "color": "#0000ff",
            "thickness": 2,
        },
        {
            "type": "drawing",
            "subtype": "rect",
            "points": [[0.6, 0.6], [0.8, 0.7]],
            "color": "#ff0000",
            "thickness": 3,
        },
        {"type": "stamp", "subtype": "stamp_tick", "rel_pos": [0.5, 0.5], "color": "#00aa00"},
    ]
}


def _subtypes(path, page=0):
    reader = pypdf.PdfReader(path, strict=True)
    return [str(a.get_object()["/Subtype"]) for a in reader.pages[page].get("/Annots", [])]


@pytest.mark.parametrize("xref_streams", [False, True])
def test_write_appends_standard_annotations(tmp_path, xref_streams):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path, xref_streams=xref_streams)
    with open(path, "rb") as f:
        original = f.read()

    assert pa.write_annotations(path, {k: [dict(a) for a in v] for k, v in ANNOS.items()})

    with open(path, "rb") as f:
        updated = f.read()
    assert updated.startswith(original), "the original revision must stay untouched"
    assert _subtypes(path) == ["/Text", "/FreeText", "/Highlight", "/Ink", "/Square", "/Ink"]


def test_round_trip_and_replacement(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    annos = {k: [dict(a) for a in v] for k, v in ANNOS.items()}
    pa.write_annotations(path, annos)

    back = pa.read_annotations(path)
    assert [a["type"] for a in back["0"]] == [a["type"] for a in ANNOS["0"]]
    assert back["0"][0]["text"] == "Sticky ü note"
    assert back["0"][0]["id"] == annos["0"][0]["id"]

    pa.write_annotations(path, {"1": [dict(ANNOS["0"][0])]})
    assert _subtypes(path, 0) == []
    assert _subtypes(path, 1) == ["/Text"]
    assert list(pa.read_annotations(path)) == ["1"]


def test_other_apps_comments_are_kept_and_their_edits_read_back(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Annots = pdf.make_indirect(
            pikepdf.Array(
                [
                    pdf.make_indirect(
                        pikepdf.Dictionary(
                            Type=pikepdf.Name.Annot,
                            Subtype=pikepdf.Name.Text,
                            Rect=[10, 10, 30, 30],
                            Contents=pikepdf.String("From Acrobat"),
                        )
                    )
                ]
            )
        )
        pdf.save(path)

    pa.write_annotations(path, {"0": [dict(ANNOS["0"][0])]})
    assert _subtypes(path) == ["/Text", "/Text"]

    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for annot in pdf.pages[0].Annots:
            if str(annot.get("/NM", "")).startswith(pa.NAME_PREFIX):
                annot.Contents = pikepdf.String("Edited elsewhere")
        pdf.save(path)

    back = pa.read_annotations(path)
    assert [a["text"] for a in back["0"]] == ["Edited elsewhere"]


def test_read_returns_none_without_riemann_annotations(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    assert pa.read_annotations(path) is None


def test_encrypted_and_read_only_files_are_not_written(tmp_path):
    path = str(tmp_path / "locked.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(path, encryption=pikepdf.Encryption(owner="o", user=""))
    assert not pa.can_store_in(path)

    plain = str(tmp_path / "plain.pdf")
    _make_pdf(plain)
    assert pa.can_store_in(plain)
    os.chmod(plain, 0o444)
    try:
        if not os.access(plain, os.W_OK):
            assert not pa.can_store_in(plain)
    finally:
        os.chmod(plain, 0o644)


def test_save_copy_leaves_source_alone(tmp_path):
    path = str(tmp_path / "doc.pdf")
    dest = str(tmp_path / "copy.pdf")
    _make_pdf(path)
    before = open(path, "rb").read()
    assert pa.write_annotations(path, {"0": [dict(ANNOS["0"][0])]}, dest=dest)
    assert open(path, "rb").read() == before
    assert _subtypes(dest) == ["/Text"]


def test_rotated_page_mapping():
    space = pa._PageSpace((0, 0, 600, 800), 90)
    assert space.to_pdf(0, 0) == (0, 0)
    assert space.to_pdf(1, 1) == (600, 800)
    upright = pa._PageSpace((0, 0, 600, 800), 0)
    assert upright.to_pdf(0, 0) == (0, 800)


def test_page_space_uses_inherited_attributes(tmp_path):
    path = str(tmp_path / "inherit.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(600, 800))
    page = pdf.pages[0]
    del page.obj["/MediaBox"]
    pdf.Root.Pages.MediaBox = [0, 0, 600, 800]
    pdf.Root.Pages.Rotate = 90
    pdf.save(path)
    with pikepdf.open(path) as pdf:
        space = pa._page_space(pdf.pages[0])
    assert space.rotate == 90
    assert space.to_pdf(1, 1) == (600, 800)


def test_trailer_size_above_object_count(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    with open(path, "rb") as f:
        data = f.read()
    head, sep, tail = data.rpartition(b"/Size ")
    digits = len(tail) - len(tail.lstrip(b"0123456789"))
    with open(path, "wb") as f:
        f.write(head + sep + b"200" + tail[digits:])
    with pikepdf.open(path) as pdf:
        assert int(pdf.trailer.Size) == 200

    assert pa.write_annotations(path, {k: [dict(a) for a in v] for k, v in ANNOS.items()})
    found = pa.read_annotations(path)
    assert found is not None and len(found["0"]) == len(ANNOS["0"])
    assert len(_subtypes(path)) == len(ANNOS["0"])


def _copy(annos):
    import json

    return json.loads(json.dumps(annos))


def test_read_back_matches_written_form(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    annos = _copy(ANNOS)
    assert pa.write_annotations(path, annos)
    assert pa.read_annotations(path) == pa.written_form(annos)


def _moved_externally(path, dx, dy, subtype=None):
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for annot in pdf.pages[0].Annots:
            if subtype is None or str(annot.Subtype) == subtype:
                r = [float(v) for v in annot.Rect]
                annot.Rect = pikepdf.Array([r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy])
        pdf.save(path)


def test_annotations_moved_in_another_app_follow(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    note = {"id": "n1", "type": "note", "rel_pos": [0.3, 0.4], "text": "x", "color": "#ff0000"}
    assert pa.write_annotations(path, {"0": [note]})
    _moved_externally(path, 61.2, -79.2)
    back = pa.read_annotations(path)["0"][0]
    assert back["rel_pos"] == pytest.approx([0.4, 0.5])


def test_resized_rectangles_take_the_new_corners(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    rect = {"id": "r1", "type": "drawing", "subtype": "rect", "points": [[0.1, 0.1], [0.2, 0.2]], "color": "#ff0000"}
    assert pa.write_annotations(path, {"0": [rect]})
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Annots[0].Rect = pikepdf.Array([61.2, 396, 306, 712.8])
        pdf.save(path)
    pts = pa.read_annotations(path)["0"][0]["points"]
    assert pts[0] == pytest.approx([0.1, 0.1]) and pts[1] == pytest.approx([0.5, 0.5])


def _note(i, text, color="#ff0000"):
    return {"id": i, "type": "note", "rel_pos": [0.1, 0.1], "text": text, "color": color}


def test_merge_keeps_edits_from_both_sides():
    base = pa.written_form({"0": [_note("a", "A"), _note("b", "B"), _note("c", "C"), _note("d", "D")]})
    local = {"0": [_note("a", "A local"), _note("b", "B"), _note("d", "D"), _note("new", "mine")]}
    stored = {"0": [pa.as_read_back(_note("a", "A")), pa.as_read_back(_note("b", "B theirs")), pa.as_read_back(_note("c", "C")), pa.as_read_back(_note("x", "theirs"))]}
    merged = pa.merge_annotations(local, stored, base)
    texts = {a["id"]: a["text"] for a in merged["0"]}
    assert texts == {"a": "A local", "b": "B theirs", "new": "mine", "x": "theirs"}


def test_merge_without_a_base_keeps_everything_and_prefers_the_file():
    local = {"0": [_note("a", "mine"), _note("b", "only mine")]}
    stored = {"1": [_note("a", "theirs")], "0": [_note("c", "only theirs")]}
    merged = pa.merge_annotations(local, stored, None)
    assert {a["id"]: a["text"] for p in merged.values() for a in p} == {"a": "theirs", "b": "only mine", "c": "only theirs"}
    assert [a["id"] for a in merged["1"]] == ["a"]


def test_compact_drops_superseded_saves(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    big = "x" * 20000
    for i in range(12):
        assert pa.write_annotations(path, {"0": [_note("n", f"{big}{i}")]})
    assert pa.incremental_updates(path) >= 12
    before = os.path.getsize(path)
    assert pa.compact(path)
    assert os.path.getsize(path) < before / 4
    assert pa.incremental_updates(path) == 0
    assert pa.read_annotations(path)["0"][0]["text"] == f"{big}11"


def test_compact_leaves_signed_files_alone(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.AcroForm = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array())
        pdf.save(path)
    with open(path, "rb") as f:
        original = f.read()
    assert not pa.compact(path)
    with open(path, "rb") as f:
        assert f.read() == original


def test_saved_copies_are_compact(tmp_path):
    path = str(tmp_path / "doc.pdf")
    dest = str(tmp_path / "copy.pdf")
    _make_pdf(path)
    for i in range(5):
        assert pa.write_annotations(path, {"0": [_note("n", "y" * 20000 + str(i))]})
    assert pa.write_annotations(path, {"0": [_note("n", "final")]}, dest=dest)
    assert pa.incremental_updates(dest) == 0
    assert os.path.getsize(dest) < os.path.getsize(path) / 4
    assert pa.read_annotations(dest)["0"][0]["text"] == "final"


def test_visible_box_clips_the_crop_box_to_the_media_box():
    assert pa.visible_box((-10, -10, 700, 900), (0, 0, 612, 792)) == (0, 0, 612, 792)
    assert pa.visible_box((50, 50, 300, 300), (0, 0, 612, 792)) == (50, 50, 300, 300)
    assert pa.visible_box((700, 800, 900, 1000), (0, 0, 612, 792)) == (0, 0, 612, 792)
    assert pa.visible_box(None, None) == (0.0, 0.0, 612.0, 792.0)
