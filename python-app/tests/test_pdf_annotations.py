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

    # Another app edits the text of Riemann's note.
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
    # Top-left of the displayed (rotated) page is the bottom-left in user space.
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
