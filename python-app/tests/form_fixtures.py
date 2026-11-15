"""A small PDF with a text field, a checkbox and a two-option radio group, for form tests."""

import pikepdf
from pikepdf import Array, Dictionary, Name, String


def make_form_pdf(path):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    helv = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))

    def stream(text=b""):
        s = pikepdf.Stream(pdf, text)
        s.Type = Name.XObject
        s.Subtype = Name.Form
        s.BBox = Array([0, 0, 14, 14])
        return s

    name = pdf.make_indirect(Dictionary(
        FT=Name.Tx, T=String("name"), V=String(""), Type=Name.Annot, Subtype=Name.Widget,
        Rect=Array([100, 700, 300, 720]), DA=String("/Helv 12 Tf 0 g"), F=4, P=page.obj,
    ))
    agree = pdf.make_indirect(Dictionary(
        FT=Name.Btn, T=String("agree"), V=Name.Off, AS=Name.Off, Type=Name.Annot, Subtype=Name.Widget,
        Rect=Array([100, 650, 114, 664]), F=4, P=page.obj,
        AP=Dictionary(N=Dictionary(Yes=stream(b"0 0 14 14 re f"), Off=stream())),
    ))
    radio = pdf.make_indirect(Dictionary(FT=Name.Btn, Ff=49152, T=String("size"), V=Name.Off, Kids=Array()))
    kids = []
    for i, state in enumerate(("Small", "Large")):
        kid = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Widget, Parent=radio, AS=Name.Off, F=4, P=page.obj,
            Rect=Array([100 + 30 * i, 600, 114 + 30 * i, 614]),
            AP=Dictionary(N=Dictionary({f"/{state}": stream(b"0 0 14 14 re f"), "/Off": stream()})),
        ))
        kids.append(kid)
    radio.Kids = Array(kids)
    page.obj.Annots = Array([name, agree] + kids)
    pdf.Root.AcroForm = Dictionary(Fields=Array([name, agree, radio]), DR=Dictionary(Font=Dictionary(Helv=helv)), DA=String("/Helv 12 Tf 0 g"))
    pdf.save(path)
    return path
