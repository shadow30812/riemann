"""
Notes:

test_invalid_field_gets_a_cross: Turned clockwise, the page's low-y edge is
shown on the left and x runs down.
"""

import io

import pikepdf

from riemann.core import signature_appearance as sa


def _layer(pdf, content):
    return pdf.make_stream(
        content, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 100, 100]
    )


def _signed_like(path, rotate=0, layered=True):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(600, 800))
    page = pdf.pages[0]
    if rotate:
        page.obj.Rotate = rotate
    page.obj.Contents = pdf.make_stream(b"0 0 1 rg 0 0 600 10 re f")
    layers = {
        "/n0": _layer(pdf, b"% DSBlank"),
        "/n1": _layer(pdf, b"1 1 0 rg 10 10 80 80 re f"),
        "/n2": _layer(pdf, b"0 g 5 5 10 10 re f"),
    }
    if layered:
        layers["/n4"] = _layer(pdf, b"1 0 0 rg 0 90 100 10 re f")
    frm = _layer(pdf, b"/n0 Do /n1 Do /n2 Do" + (b" /n4 Do" if layered else b""))
    frm.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(layers if layered else {"/n2": layers["/n2"]}))
    normal = _layer(pdf, b"/FRM Do")
    normal.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(FRM=frm))
    field = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Widget,
            FT=pikepdf.Name.Sig,
            T=pikepdf.String("Signature1"),
            Rect=[500, 10, 600, 110],
            AP=pikepdf.Dictionary(N=normal),
        )
    )
    page.obj.Annots = pdf.make_indirect(pikepdf.Array([field]))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=[field], SigFlags=3)
    pdf.save(path)


def _layers_of(data):
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        frm = page.Resources.XObject.RiemannSigStatus.Resources.XObject.FRM
        xobjects = frm.Resources.XObject
        return (
            [float(v) for v in page.MediaBox],
            "/Annots" in page.obj,
            xobjects.n1.read_bytes(),
            xobjects.n4.read_bytes(),
            xobjects.n2.read_bytes(),
        )


def test_valid_field_gets_a_tick_and_loses_the_unknown_layers(tmp_path):
    path = str(tmp_path / "signed.pdf")
    _signed_like(path)
    before = open(path, "rb").read()
    [result] = sa.status_appearances(path, {"Signature1": sa.VALID})
    assert open(path, "rb").read() == before

    media, has_annots, n1, n4, n2 = _layers_of(result["pdf"])
    assert media == [500, 10, 600, 110]
    assert not has_annots
    assert sa._TICK.encode() in n1
    assert n4 == b""
    assert n2 == b"0 g 5 5 10 10 re f"
    left, top, right, bottom = result["rect"]
    assert abs(left - 500 / 600) < 1e-9 and abs(right - 1) < 1e-9
    assert abs(top - 690 / 800) < 1e-9 and abs(bottom - 790 / 800) < 1e-9


def test_invalid_field_gets_a_cross(tmp_path):
    path = str(tmp_path / "signed.pdf")
    _signed_like(path, rotate=90)
    [result] = sa.status_appearances(path, {"Signature1": sa.INVALID})
    assert sa._CROSS.encode() in _layers_of(result["pdf"])[2]
    left, top, right, bottom = result["rect"]
    assert abs(left - 10 / 800) < 1e-9 and abs(right - 110 / 800) < 1e-9
    assert abs(top - 500 / 600) < 1e-9 and abs(bottom - 1) < 1e-9


def test_unknown_status_and_plain_appearances_are_left_alone(tmp_path):
    path = str(tmp_path / "signed.pdf")
    _signed_like(path)
    assert sa.status_appearances(path, {"Signature1": "UNKNOWN"}) == []
    plain = str(tmp_path / "plain.pdf")
    _signed_like(plain, layered=False)
    assert sa.status_appearances(plain, {"Signature1": sa.VALID}) == []
