"""
Basic views of Office files, for when LibreOffice isn't installed.

basic_format(path) names the reader for a file ("docx", "pptx", "odt", "odp",
"rtf", "xlsx", "xls", "ods"), by name or by content, or None when there is
none (.doc, .ppt, .xlsb, drawings and Apple's formats need LibreOffice).
packages_ready(fmt) says whether the Python packages it needs are installed;
OpenDocument needs only lxml, which Riemann always has.

Documents and presentations become one HTML page for the reader's text view,
as an EPUB does: read_document(path) returns {"title", "html", "sections",
"note"}, the html already passed through core.html_clean, every heading or
slide an anchor in sections.

- Word (.docx, .docm, .dotx): mammoth turns it into HTML with its pictures
  embedded. Comments and tracked changes are left out, as mammoth does.
- PowerPoint (.pptx and friends): python-pptx; each slide becomes a section
  with its title, its text boxes in reading order (top to bottom, then left
  to right), tables and pictures, and the speaker notes under it.
- OpenDocument text and slides: _OdfReader over content.xml with lxml:
  headings (text:h, and paragraphs whose style descends from Title, a
  Heading N style or one with an outline level), paragraphs, bold, italic and underline (from the
  automatic and common styles), links, lists, tables, frames and their pictures, notes in
  brackets. Comments and tracked changes are left out.
- RTF: striprtf, as plain paragraphs.

Spreadsheets go to the table view: read_spreadsheet(path) writes each
visible sheet's rows as JSON lines into a new folder and returns {"folder",
"sheets": [{"name", "file", "spans", "formulas"}]}; the caller reads the
rows lazily and deletes the folder. Values are shown as their number format
asks (format_value: percentages, decimals, thousands separators, dates as
ISO dates); OpenDocument cells keep the text the program saved.

- .xlsx: openpyxl, with the values Excel last saved. Workbooks up to
  FULL_LOAD_BYTES are opened in full, which gives merged cells and formulas;
  bigger ones in read-only mode, which streams rows and has neither.
- .xls: xlrd, with merged cells.
- .ods: _OdfReader again. ODF describes empty space as repeated rows and
  columns (often a million of them), so empty rows and cells count only when
  something follows them (which keeps the row numbers right), and nothing
  goes past MAX_ROWS rows or MAX_COLUMNS columns, the limits of Excel and
  LibreOffice.

Everything here runs in the converter child process (core/convert_process.py,
kinds "office" and "spreadsheet") and imports no Qt.
"""

import base64
import datetime
import html as html_lib
import importlib.util
import json
import os
import posixpath
import re
import tempfile
import zipfile
from typing import Dict, List, Optional

from lxml import etree, html as lxml_html

from .html_clean import clean_html

FULL_LOAD_BYTES = 20 * 1024 * 1024
IMAGE_LIMIT = 15 * 1024 * 1024
MAX_ROWS = 1_048_576
MAX_COLUMNS = 16_384
MAX_FORMULAS = 200_000
CONTENT_LIMIT = 256 * 1024 * 1024
BASIC_NOTE = "LibreOffice isn't installed, so this is a basic view: the text, tables and pictures, without the page layout."

FORMATS = {
    ".docx": "docx", ".docm": "docx", ".dotx": "docx", ".dotm": "docx",
    ".pptx": "pptx", ".pptm": "pptx", ".ppsx": "pptx", ".potx": "pptx",
    ".odt": "odt", ".ott": "odt", ".odp": "odp", ".otp": "odp",
    ".rtf": "rtf",
    ".xlsx": "xlsx", ".xlsm": "xlsx", ".xltx": "xlsx", ".xls": "xls", ".ods": "ods", ".ots": "ods",
}
SPREADSHEETS = ("xlsx", "xls", "ods")
MODULES = {"docx": "mammoth", "pptx": "pptx", "xlsx": "openpyxl", "xls": "xlrd", "rtf": "striprtf"}
ODF_FORMATS = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
}
OOXML_FORMATS = (("word/document.xml", "docx"), ("ppt/presentation.xml", "pptx"), ("xl/workbook.xml", "xlsx"))
IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".bmp": "image/bmp", ".webp": "image/webp", ".svg": "image/svg+xml",
}

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
}


class OfficeReadError(Exception):
    pass


def _q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def basic_format(path: str) -> Optional[str]:
    suffix = os.path.splitext(path)[1].lower()
    if suffix in FORMATS:
        return FORMATS[suffix]
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if not head.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "mimetype" in names:
                return ODF_FORMATS.get(archive.read("mimetype")[:100].decode("ascii", "replace").strip())
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    return next((fmt for part, fmt in OOXML_FORMATS if part in names), None)


def packages_ready(fmt: Optional[str]) -> bool:
    if fmt is None:
        return False
    module = MODULES.get(fmt)
    return module is None or importlib.util.find_spec(module) is not None


def is_spreadsheet(fmt: Optional[str]) -> bool:
    return fmt in SPREADSHEETS


def _data_uri(data: bytes, name: str) -> Optional[str]:
    media = IMAGE_TYPES.get(posixpath.splitext(name.lower())[1])
    if media is None or len(data) > IMAGE_LIMIT:
        return None
    return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"


def _with_sections(body: str, title: str, note: str = "") -> dict:
    """Gives every heading an id and lists them as sections."""
    if not body.strip():
        return {"title": title, "html": "", "sections": [], "note": note}
    fragment = lxml_html.fragment_fromstring(body, create_parent="div")
    sections = []
    for number, heading in enumerate(fragment.iter("h1", "h2", "h3")):
        text = " ".join(heading.text_content().split())
        if not text:
            continue
        anchor = heading.get("id") or f"h{number}"
        heading.set("id", anchor)
        sections.append((int(heading.tag[1]) - 1, text[:120], f"#{anchor}"))
    parts = [html_lib.escape(fragment.text) if fragment.text else ""]
    parts += [lxml_html.tostring(child, encoding="unicode", method="html") for child in fragment]
    return {"title": title, "html": "".join(parts), "sections": sections, "note": note}


def read_docx(path: str) -> dict:
    import mammoth

    with open(path, "rb") as f:
        result = mammoth.convert_to_html(f, external_file_access=False)
    return _with_sections(clean_html(result.value), os.path.basename(path), BASIC_NOTE)


def _runs_html(paragraph) -> str:
    parts = []
    for run in paragraph.runs:
        text = html_lib.escape(run.text)
        if not text:
            continue
        font = run.font
        if font.bold:
            text = f"<b>{text}</b>"
        if font.italic:
            text = f"<i>{text}</i>"
        if font.underline:
            text = f"<u>{text}</u>"
        link = run.hyperlink.address if run.hyperlink is not None else None
        if link:
            text = f'<a href="{html_lib.escape(link, quote=True)}">{text}</a>'
        parts.append(text)
    return "".join(parts)


def _pptx_shapes(shapes, title_id) -> List[str]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    ordered = sorted(shapes, key=lambda s: ((s.top or 0), (s.left or 0)))
    out = []
    for shape in ordered:
        if shape.shape_id == title_id:
            continue
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            out += _pptx_shapes(shape.shapes, title_id)
            continue
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                image = shape.image
                uri = _data_uri(image.blob, "x." + image.ext)
            except Exception:
                uri = None
            if uri:
                out.append(f'<p><img src="{uri}" alt=""></p>')
            continue
        if getattr(shape, "has_table", False) and shape.has_table:
            rows = []
            for row in shape.table.rows:
                cells = "".join(f"<td>{html_lib.escape(cell.text)}</td>" for cell in row.cells)
                rows.append(f"<tr>{cells}</tr>")
            out.append(f"<table>{''.join(rows)}</table>")
            continue
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                text = _runs_html(paragraph)
                if text.strip():
                    out.append(f"<li>{text}</li>" if paragraph.level > 0 else f"<p>{text}</p>")
    return out


def read_pptx(path: str) -> dict:
    from pptx import Presentation

    deck = Presentation(path)
    parts = []
    for number, slide in enumerate(deck.slides, 1):
        title_shape = slide.shapes.title
        title = " ".join(title_shape.text.split()) if title_shape is not None and title_shape.has_text_frame else ""
        heading = f"{number}. {html_lib.escape(title)}" if title else f"Slide {number}"
        body = _pptx_shapes(slide.shapes, title_shape.shape_id if title_shape is not None else None)
        notes = ""
        if slide.has_notes_slide:
            text = slide.notes_slide.notes_text_frame.text if slide.notes_slide.notes_text_frame is not None else ""
            if text.strip():
                lines = "".join(f"<p>{html_lib.escape(line)}</p>" for line in text.splitlines() if line.strip())
                notes = f"<blockquote><p><em>Notes</em></p>{lines}</blockquote>"
        parts.append(f'<section><h2 id="slide{number}">{heading}</h2>{"".join(body)}{notes}<hr></section>')
    return _with_sections(clean_html("".join(parts)), os.path.basename(path), BASIC_NOTE)


def read_rtf(path: str) -> dict:
    from striprtf.striprtf import rtf_to_text

    with open(path, "rb") as f:
        raw = f.read(CONTENT_LIMIT).decode("latin-1")
    text = rtf_to_text(raw, errors="ignore")
    body = "".join(f"<p>{html_lib.escape(line)}</p>" for line in text.splitlines() if line.strip())
    return {"title": os.path.basename(path), "html": body, "sections": [], "note": BASIC_NOTE}


def _parser() -> etree.XMLParser:
    return etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=True)


def _repeat(element, name: str) -> int:
    try:
        return max(1, int(element.get(_q("table", name)) or 1))
    except ValueError:
        return 1


class _OdfReader:
    """Reads an OpenDocument file's content.xml into HTML or rows (see the module docstring)."""

    def __init__(self, path: str):
        try:
            self.archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as error:
            raise OfficeReadError(f"The file can't be opened ({error}).")
        self.content = self._xml("content.xml")
        if self.content is None:
            raise OfficeReadError("The file has no content.xml.")
        self.styles: Dict[str, set] = {}
        self.parents: Dict[str, str] = {}
        self.levels: Dict[str, int] = {}
        self.hidden_tables: set = set()
        for source in (self._xml("styles.xml"), self.content):
            if source is not None:
                self._read_styles(source)

    def close(self) -> None:
        self.archive.close()

    def _xml(self, name: str):
        try:
            info = self.archive.getinfo(name)
        except KeyError:
            return None
        if info.file_size > CONTENT_LIMIT:
            raise OfficeReadError(f"{name} is too large to show.")
        return etree.fromstring(self.archive.read(info), _parser())

    def _read_styles(self, root) -> None:
        for style in root.iter(_q("style", "style")):
            name = style.get(_q("style", "name"))
            parent = style.get(_q("style", "parent-style-name"))
            if name and parent:
                self.parents[name] = parent
            level = style.get(_q("style", "default-outline-level"))
            if name and level and level.isdigit():
                self.levels[name] = int(level)
            marks = set()
            for props in style.iter(_q("style", "text-properties")):
                if props.get(_q("fo", "font-weight")) == "bold":
                    marks.add("b")
                if props.get(_q("fo", "font-style")) == "italic":
                    marks.add("i")
                underline = props.get(_q("style", "text-underline-style"))
                if underline and underline != "none":
                    marks.add("u")
            for props in style.iter(_q("style", "table-properties")):
                if props.get(_q("table", "display")) == "false":
                    self.hidden_tables.add(name)
            if name and marks:
                self.styles[name] = marks

    def heading_level(self, style: Optional[str]) -> int:
        for _ in range(12):
            if not style:
                return 0
            if style in self.levels:
                return min(6, max(1, self.levels[style]))
            if style == "Title":
                return 1
            if re.fullmatch(r"Heading_20_[1-6]", style):
                return int(style[-1])
            style = self.parents.get(style)
        return 0

    def _picture(self, href: str) -> Optional[str]:
        if not href or ":" in href.split("/")[0]:
            return None
        name = posixpath.normpath(href.lstrip("./"))
        try:
            info = self.archive.getinfo(name)
        except KeyError:
            return None
        if info.file_size > IMAGE_LIMIT:
            return None
        return _data_uri(self.archive.read(info), name)

    def inline(self, element) -> str:
        parts = [html_lib.escape(element.text or "")]
        for child in element:
            parts.append(self._inline_child(child))
            parts.append(html_lib.escape(child.tail or ""))
        return "".join(parts)

    def _inline_child(self, child) -> str:
        tag = child.tag
        if tag == _q("text", "span"):
            text = self.inline(child)
            for mark in sorted(self.styles.get(child.get(_q("text", "style-name")), ())):
                text = f"<{mark}>{text}</{mark}>"
            return text
        if tag == _q("text", "a"):
            href = html_lib.escape(child.get(_q("xlink", "href")) or "", quote=True)
            return f'<a href="{href}">{self.inline(child)}</a>'
        if tag == _q("text", "line-break"):
            return "<br>"
        if tag == _q("text", "tab"):
            return " "
        if tag == _q("text", "s"):
            try:
                return " " * min(100, int(child.get(_q("text", "c")) or 1))
            except ValueError:
                return " "
        if tag == _q("text", "note"):
            body = child.find(_q("text", "note-body"))
            return f"<small> [{self.block_text(body)}]</small>" if body is not None else ""
        if tag == _q("draw", "frame"):
            return "".join(self.frame(child))
        if tag in (_q("office", "annotation"), _q("office", "annotation-end"), _q("text", "tracked-changes")):
            return ""
        return self.inline(child)

    def block_text(self, element) -> str:
        return " ".join(self.inline(p) for p in element.iter(_q("text", "p"), _q("text", "h")))

    def frame(self, frame) -> List[str]:
        out = []
        for child in frame:
            if child.tag == _q("draw", "image"):
                uri = self._picture(child.get(_q("xlink", "href")))
                if uri:
                    out.append(f'<img src="{uri}" alt="">')
                    break
            elif child.tag == _q("draw", "text-box"):
                out += self.blocks(child)
            elif child.tag == _q("table", "table"):
                out.append(self.table_html(child))
        return out

    def blocks(self, parent) -> List[str]:
        out = []
        for child in parent:
            tag = child.tag
            if tag == _q("text", "h"):
                try:
                    level = min(6, max(1, int(child.get(_q("text", "outline-level")) or 1)))
                except ValueError:
                    level = 1
                out.append(f"<h{level}>{self.inline(child)}</h{level}>")
            elif tag == _q("text", "p"):
                level = self.heading_level(child.get(_q("text", "style-name")))
                tag_name = f"h{level}" if level else "p"
                out.append(f"<{tag_name}>{self.inline(child)}</{tag_name}>")
            elif tag == _q("text", "list"):
                items = []
                for item in child:
                    if item.tag in (_q("text", "list-item"), _q("text", "list-header")):
                        items.append(f"<li>{''.join(self.blocks(item))}</li>")
                out.append(f"<ul>{''.join(items)}</ul>")
            elif tag == _q("table", "table"):
                out.append(self.table_html(child))
            elif tag == _q("draw", "frame"):
                out += self.frame(child)
            elif tag in (_q("text", "section"), _q("text", "index-body"), _q("text", "table-of-content"),
                         _q("draw", "g"), _q("draw", "custom-shape")):
                out += self.blocks(child)
        return out

    def table_rows(self, table):
        """(cells, spans, formulas) per row; empty space at the ends dropped, repeats capped at the sheet limits."""
        pending_rows = 0
        produced = 0
        for row in table.iter(_q("table", "table-row")):
            cells, spans, formulas = [], [], {}
            pending = 0
            for cell in row:
                if len(cells) + pending >= MAX_COLUMNS:
                    break
                if cell.tag == _q("table", "covered-table-cell"):
                    pending += _repeat(cell, "number-columns-repeated")
                    continue
                if cell.tag != _q("table", "table-cell"):
                    continue
                text = "\n".join(" ".join(self.inline(p).split()) for p in cell.iter(_q("text", "p")))
                text = html_lib.unescape(re.sub(r"<[^>]+>", "", text))
                column = len(cells) + pending
                columns = _repeat(cell, "number-columns-spanned")
                rows = _repeat(cell, "number-rows-spanned")
                if columns > 1 or rows > 1:
                    spans.append((column, min(rows, MAX_ROWS), min(columns, MAX_COLUMNS)))
                formula = cell.get(_q("table", "formula"))
                if formula:
                    formulas[column] = re.sub(r"\[\.?([A-Z$]+[0-9$]+(?::\.?[A-Z$]+[0-9$]+)?)\]", r"\1", formula.split(":", 1)[-1])
                repeat = _repeat(cell, "number-columns-repeated")
                if not text:
                    pending += repeat
                    continue
                cells += [""] * pending
                pending = 0
                cells += [text] * min(repeat, MAX_COLUMNS - len(cells))
            repeat = _repeat(row, "number-rows-repeated")
            if not cells:
                pending_rows += repeat
                if produced + pending_rows >= MAX_ROWS:
                    return
                continue
            for _ in range(pending_rows):
                yield [], [], {}
            produced += pending_rows
            pending_rows = 0
            for _ in range(min(repeat, MAX_ROWS - produced)):
                yield cells, spans, formulas
            produced += min(repeat, MAX_ROWS - produced)
            if produced >= MAX_ROWS:
                return

    def table_html(self, table) -> str:
        rows = []
        for cells, _spans, _formulas in self.table_rows(table):
            rows.append("<tr>" + "".join(f"<td>{html_lib.escape(c)}</td>" for c in cells) + "</tr>")
        return f"<table>{''.join(rows)}</table>"

    def document_html(self) -> str:
        body = self.content.find(_q("office", "body"))
        if body is None:
            return ""
        text = body.find(_q("office", "text"))
        if text is not None:
            return "".join(self.blocks(text))
        presentation = body.find(_q("office", "presentation"))
        if presentation is None:
            return ""
        parts = []
        for number, page in enumerate(presentation.iter(_q("draw", "page")), 1):
            title, content = "", []
            for frame in page.findall(_q("draw", "frame")):
                if frame.get(_q("presentation", "class")) == "title" and not title:
                    box = frame.find(_q("draw", "text-box"))
                    title = " ".join(re.sub(r"<[^>]+>", "", "".join(self.blocks(box if box is not None else frame))).split())
                    continue
                content += self.frame(frame)
            notes_html = ""
            notes = page.find(_q("presentation", "notes"))
            if notes is not None:
                note_blocks = []
                for frame in notes.iter(_q("draw", "frame")):
                    if frame.get(_q("presentation", "class")) == "notes":
                        note_blocks += self.frame(frame)
                if "".join(note_blocks).strip():
                    notes_html = f"<blockquote><p><em>Notes</em></p>{''.join(note_blocks)}</blockquote>"
            heading = f"{number}. {html_lib.escape(html_lib.unescape(title))}" if title else f"Slide {number}"
            parts.append(f'<section><h2 id="slide{number}">{heading}</h2>{"".join(content)}{notes_html}<hr></section>')
        return "".join(parts)

    def sheets(self):
        body = self.content.find(_q("office", "body"))
        spreadsheet = body.find(_q("office", "spreadsheet")) if body is not None else None
        if spreadsheet is None:
            return
        for table in spreadsheet.findall(_q("table", "table")):
            if table.get(_q("table", "style-name")) in self.hidden_tables:
                continue
            yield table.get(_q("table", "name")) or "Sheet", self.table_rows(table)


def read_odf(path: str) -> dict:
    reader = _OdfReader(path)
    try:
        body = reader.document_html()
    finally:
        reader.close()
    return _with_sections(clean_html(body), os.path.basename(path), BASIC_NOTE)


DOCUMENT_READERS = {"docx": read_docx, "pptx": read_pptx, "odt": read_odf, "odp": read_odf, "rtf": read_rtf}


def read_document(path: str) -> dict:
    fmt = basic_format(path)
    reader = DOCUMENT_READERS.get(fmt)
    if reader is None:
        raise OfficeReadError("Riemann needs LibreOffice to show this kind of file.")
    if not packages_ready(fmt):
        raise OfficeReadError(f"The {MODULES[fmt]} package that reads this file isn't installed.")
    return reader(path)


def _decimals(fmt: str) -> int:
    match = re.search(r"\.(0+)", fmt)
    return len(match.group(1)) if match else 0


def format_value(value, fmt: str = "General") -> str:
    """value as a spreadsheet shows it, as far as the number format fmt says."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time(0) and not re.search(r"[hHs]", fmt or ""):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, (int, float)):
        fmt = (fmt or "General").split(";", 1)[0]
        if fmt == "General" or fmt == "@":
            if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
                return str(int(value))
            return f"{value:.10g}" if isinstance(value, float) else str(value)
        places = _decimals(fmt)
        if "%" in fmt:
            return f"{value * 100:.{places}f}%"
        if "E+" in fmt.upper():
            return f"{value:.{places}E}"
        return f"{value:{',' if ',' in fmt else ''}.{places}f}"
    return str(value)


def _write_sheet(folder: str, index: int, rows) -> str:
    name = os.path.join(folder, f"sheet{index}.jsonl")
    with open(name, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return name


def _xlsx_sheets(path: str, folder: str) -> list:
    import openpyxl

    full = os.path.getsize(path) <= FULL_LOAD_BYTES
    values = openpyxl.load_workbook(path, read_only=not full, data_only=True)
    formulas_book = openpyxl.load_workbook(path, read_only=False, data_only=False) if full else None
    sheets = []
    try:
        for index, sheet in enumerate(values.worksheets):
            if sheet.sheet_state != "visible":
                continue
            rows = (
                [format_value(cell.value, getattr(cell, "number_format", "General")) for cell in row]
                for row in sheet.iter_rows()
            )
            file = _write_sheet(folder, index, (_trimmed(r) for r in rows))
            spans, formulas = [], {}
            if full:
                for merged in sheet.merged_cells.ranges:
                    spans.append([merged.min_row - 1, merged.min_col - 1, merged.max_row - merged.min_row + 1,
                                  merged.max_col - merged.min_col + 1])
                source = formulas_book[sheet.title]
                for row in source.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value.startswith("=") and len(formulas) < MAX_FORMULAS:
                            formulas[f"{cell.row},{cell.column - 1}"] = cell.value
            sheets.append({"name": sheet.title, "file": file, "spans": spans, "formulas": formulas})
    finally:
        values.close()
        if formulas_book is not None:
            formulas_book.close()
    return sheets


def _trimmed(row: list) -> list:
    while row and row[-1] == "":
        row.pop()
    return row


def _xls_sheets(path: str, folder: str) -> list:
    import xlrd

    book = xlrd.open_workbook(path, formatting_info=True, on_demand=True)
    sheets = []
    try:
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            if sheet.visibility != 0:
                continue

            def rows(sheet=sheet):
                for r in range(sheet.nrows):
                    out = []
                    for c in range(sheet.ncols):
                        cell = sheet.cell(r, c)
                        fmt = "General"
                        try:
                            fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                        except (IndexError, KeyError, AttributeError):
                            pass
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            try:
                                value = xlrd.xldate_as_datetime(cell.value, book.datemode)
                            except (ValueError, OverflowError):
                                value = cell.value
                        elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            value = bool(cell.value)
                        elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                            value = None
                        elif cell.ctype == xlrd.XL_CELL_ERROR:
                            value = xlrd.error_text_from_code.get(cell.value, "#ERR")
                        else:
                            value = cell.value
                        out.append(format_value(value, fmt))
                    yield _trimmed(out)

            file = _write_sheet(folder, index, rows())
            spans = [[r0, c0, r1 - r0, c1 - c0] for r0, r1, c0, c1 in sheet.merged_cells]
            sheets.append({"name": sheet.name, "file": file, "spans": spans, "formulas": {}})
            book.unload_sheet(index)
    finally:
        book.release_resources()
    return sheets


def _ods_sheets(path: str, folder: str) -> list:
    reader = _OdfReader(path)
    sheets = []
    try:
        for index, (name, rows) in enumerate(reader.sheets()):
            spans, formulas = [], {}

            def cells(rows=rows, spans=spans, formulas=formulas):
                for number, (row, row_spans, row_formulas) in enumerate(rows):
                    for column, height, width in row_spans:
                        spans.append([number, column, height, width])
                    for column, formula in row_formulas.items():
                        if len(formulas) < MAX_FORMULAS:
                            formulas[f"{number + 1},{column}"] = formula
                    yield row

            file = _write_sheet(folder, index, cells())
            sheets.append({"name": name, "file": file, "spans": spans, "formulas": formulas})
    finally:
        reader.close()
    return sheets


SPREADSHEET_READERS = {"xlsx": _xlsx_sheets, "xls": _xls_sheets, "ods": _ods_sheets}


def read_spreadsheet(path: str) -> dict:
    fmt = basic_format(path)
    reader = SPREADSHEET_READERS.get(fmt)
    if reader is None:
        raise OfficeReadError("Riemann needs LibreOffice to show this kind of spreadsheet.")
    if not packages_ready(fmt):
        raise OfficeReadError(f"The {MODULES[fmt]} package that reads this file isn't installed.")
    folder = tempfile.mkdtemp(prefix="riemann-sheets-")
    try:
        sheets = reader(path, folder)
    except Exception:
        import shutil

        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"folder": folder, "sheets": sheets}
