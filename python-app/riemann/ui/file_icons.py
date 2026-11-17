"""
Which icon stands for a file, shared by document tabs and the file explorer.

Kinds of document have coloured icons of their own in assets/icons (PNGs,
which read on both themes, so they have no -white variant): pdf, word, slide,
sheet, epub, gallery (images), code, text, browser (HTML) and binary.
Office files are split by office_type: documents are Word, presentations and
drawings are slides, spreadsheets are sheets. Text and Markdown files whose
name says they are source code get the code icon.

name_icon decides from the name alone and returns "" when the name says
nothing, for the explorer, which never reads files. document_icon takes the
kind core.doctypes found and may read the file (office_type of a renamed
Office file).
"""

import os

from ..core.doctypes import (
    EBOOK_SUFFIXES,
    HTML_SUFFIXES,
    MARKDOWN_SUFFIXES,
    has_code_name,
    has_image_name,
    has_office_name,
    has_table_name,
    has_text_name,
    office_type,
)

BLANK_ICON = "file.svg"
TEXT_ICON = "text.png"
KIND_ICONS = {
    "pdf": "pdf.png",
    "image": "gallery.png",
    "table": "sheet.png",
    "spreadsheet": "sheet.png",
    "ebook": "epub.png",
    "markdown": TEXT_ICON,
    "text": TEXT_ICON,
    "html": "browser.png",
    "binary": "binary.png",
}
OFFICE_ICONS = {"document": "word.png", "presentation": "slide.png", "drawing": "slide.png", "spreadsheet": "sheet.png"}


def name_icon(path: str) -> str:
    lower = os.path.basename(path).lower()
    if lower.endswith(".pdf"):
        return KIND_ICONS["pdf"]
    if has_office_name(lower):
        return OFFICE_ICONS[office_type(lower)]
    if has_table_name(lower):
        return KIND_ICONS["table"]
    if lower.endswith(EBOOK_SUFFIXES):
        return KIND_ICONS["ebook"]
    if has_image_name(lower):
        return KIND_ICONS["image"]
    if lower.endswith(HTML_SUFFIXES):
        return KIND_ICONS["html"]
    if has_code_name(lower):
        return "code.png"
    if lower.endswith(MARKDOWN_SUFFIXES) or has_text_name(lower):
        return TEXT_ICON
    return ""


def document_icon(path: str, kind: str) -> str:
    if kind == "office":
        return OFFICE_ICONS.get(office_type(path) or "document", OFFICE_ICONS["document"])
    if kind in ("text", "markdown") and has_code_name(path):
        return "code.png"
    return KIND_ICONS.get(kind, TEXT_ICON)
