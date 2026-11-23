"""
Saving filled-in form fields into the PDF.

The reader shows text boxes and check boxes over a PDF's form widgets and
records what the user enters, keyed by (page index, annotation index): the
index PDFium reports for a widget is its position in the page's /Annots.
write_form_values sets those values through pikepdf's form support, which
also draws the fields' appearance streams so every viewer shows the new
values, and appends the changed objects as an incremental update, the way
annotations are saved (see riemann.core.pdf_annotations).

Signed and encrypted files are refused (returns "blocked"): adding to a
signed form can invalidate its signatures depending on what the signer
allowed. Values for such files stay in the reader until a copy is saved.
"""

import io

from .pdf_annotations import _is_signed, collect_new_objects, incremental_save

Value = str | bool


def _field_dict(annot):
    node = annot
    for _ in range(32):
        if node is None:
            return annot
        if "/T" in node:
            return node
        node = node.get("/Parent")
    return annot


def write_form_values(
    path: str, values: dict[tuple[int, int], Value], dest: str | None = None
) -> str:
    """
    Stores values in the PDF at path (or in a copy at dest).

    Returns "ok", "blocked" (signed or encrypted), "changed" (the file was
    modified while it was being read) or "none" (nothing to write).
    """
    import pikepdf
    from pikepdf.form import CheckboxField, DefaultAppearanceStreamGenerator, Form, RadioButtonGroup, TextField

    if not values:
        return "none"
    with open(path, "rb") as f:
        original = f.read()
    with pikepdf.open(io.BytesIO(original)) as pdf:
        if pdf.is_encrypted or _is_signed(pdf):
            return "blocked"
        first_new = pdf.make_indirect(pikepdf.Dictionary()).objgen[0]
        form = Form(pdf, DefaultAppearanceStreamGenerator)
        by_field = {}
        radio_options = {}
        for _, wrapper in form.items():
            field = getattr(wrapper, "_field", None)
            if field is None:
                continue
            by_field[field.obj.objgen] = wrapper
            if isinstance(wrapper, RadioButtonGroup):
                for option in wrapper.options:
                    radio_options[option._annot_dict.objgen] = option

        touched = []
        for (page_idx, annot_idx), value in sorted(values.items()):
            try:
                annot = pdf.pages[page_idx].Annots[annot_idx]
            except (IndexError, KeyError, AttributeError):
                continue
            field = _field_dict(annot)
            wrapper = by_field.get(field.objgen)
            if isinstance(wrapper, TextField) and isinstance(value, str):
                wrapper.value = value
            elif isinstance(wrapper, CheckboxField):
                wrapper.checked = bool(value)
            elif isinstance(wrapper, RadioButtonGroup):
                option = radio_options.get(annot.objgen)
                if option is not None and value:
                    option.select()
            else:
                continue
            touched.append(field)
            touched.append(annot)
            for kid in field.get("/Kids", []):
                touched.append(kid)

        if not touched:
            return "none"
        objects: dict[tuple[int, int], object] = {}
        for obj in touched:
            if obj.is_indirect:
                collect_new_objects(objects, obj, first_new)
        acro = pdf.Root.get("/AcroForm")
        if acro is not None and acro.is_indirect:
            collect_new_objects(objects, acro, first_new)
        else:
            collect_new_objects(objects, pdf.Root, first_new)
        written = incremental_save(path, original, pdf, objects, dest=dest)
    return "ok" if written else "changed"


def read_form_values(path: str) -> dict[str, Value]:
    """Field name -> value, for checking what a file holds."""
    import pikepdf
    from pikepdf.form import CheckboxField, Form, RadioButtonGroup

    out: dict[str, Value] = {}
    with pikepdf.open(path) as pdf:
        for name, wrapper in Form(pdf).items():
            if isinstance(wrapper, CheckboxField):
                out[name] = bool(wrapper.checked)
            elif isinstance(wrapper, RadioButtonGroup):
                out[name] = str(wrapper.value).lstrip("/")
            else:
                out[name] = str(wrapper.value or "")
    return out
