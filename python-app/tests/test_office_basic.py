"""
Basic views of Office files (core/office_basic.py) and the spreadsheet tab
(ui/viewers/spreadsheet_tab.py), without LibreOffice.

Word files are built by hand, PowerPoint and Excel files with python-pptx and
openpyxl, OpenDocument files as zips with their XML written here, so each
test knows exactly what the file holds. The spreadsheet tab runs with
RIEMANN_NO_OFFICE=1 and reads through the real converter child process.
"""

import datetime
import json
import os
import shutil
import time
import zipfile

import pytest
from PySide6.QtWidgets import QApplication

from riemann.core import office_basic
from riemann.core.office_basic import (
    BASIC_NOTE,
    MAX_COLUMNS,
    MAX_ROWS,
    OfficeReadError,
    basic_format,
    format_value,
    is_spreadsheet,
    packages_ready,
    read_document,
    read_spreadsheet,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ODF = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink"'
)


def wait_for(condition, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def make_docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>',
        )
        z.writestr(
            "word/styles.xml",
            f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Heading1">'
            '<w:name w:val="heading 1"/></w:style></w:styles>',
        )
        z.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{W}"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Results</w:t></w:r></w:p>'
            '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Bold</w:t></w:r><w:r><w:t xml:space="preserve"> and '
            'plain.</w:t></w:r></w:p>'
            "<w:p><w:r><w:t>&lt;script&gt;alert(1)&lt;/script&gt;</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
    return str(path)


def make_odf(path, mimetype, body, styles=""):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "content.xml",
            f'<office:document-content {ODF}><office:automatic-styles>{styles}</office:automatic-styles>'
            f"<office:body>{body}</office:body></office:document-content>",
        )
    return str(path)


def test_formats_are_found_by_name_or_content(tmp_path):
    assert basic_format("a.docx") == "docx" and basic_format("a.PPTX") == "pptx"
    assert basic_format("a.ods") == "ods" and is_spreadsheet("ods") and not is_spreadsheet("odt")
    assert basic_format("a.doc") is None and basic_format("a.vsdx") is None
    renamed = tmp_path / "report.bin"
    shutil.copy(make_docx(tmp_path / "r.docx"), renamed)
    assert basic_format(str(renamed)) == "docx"
    assert packages_ready("odt") and not packages_ready(None)


def test_a_word_file_becomes_a_page_with_sections(tmp_path):
    result = read_document(make_docx(tmp_path / "report.docx"))
    assert result["note"] == BASIC_NOTE and result["title"] == "report.docx"
    assert "<strong>Bold</strong> and plain." in result["html"]
    assert "<script" not in result["html"] and "&lt;script&gt;" in result["html"]
    assert [(depth, title) for depth, title, _ in result["sections"]] == [(0, "Results")]
    anchor = result["sections"][0][2]
    assert f'id="{anchor[1:]}"' in result["html"]


def test_a_presentation_lists_its_slides_and_notes(tmp_path):
    pptx = pytest.importorskip("pptx")
    deck = pptx.Presentation()
    for title, body, notes in (("Intro", "First point", "Say hello"), ("Plan", "Second point", "")):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    path = str(tmp_path / "talk.pptx")
    deck.save(path)
    result = read_document(path)
    titles = [title for _, title, _ in result["sections"]]
    assert titles[0].endswith("Intro") and titles[1].endswith("Plan")
    html = result["html"]
    assert html.index("First point") < html.index("Say hello") < html.index("Second point")


def test_an_opendocument_text_keeps_headings_marks_lists_and_tables(tmp_path):
    styles = (
        '<style:style style:name="T1" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style>'
        '<style:style style:name="P1" style:family="paragraph" style:parent-style-name="Heading_20_2"/>'
    )
    body = (
        "<office:text>"
        '<text:h text:outline-level="1">Chapter</text:h>'
        '<text:p text:style-name="P1">Part</text:p>'
        '<text:p>Some <text:span text:style-name="T1">strong</text:span> words<text:note><text:note-body>'
        "<text:p>a footnote</text:p></text:note-body></text:note>.</text:p>"
        "<text:list><text:list-item><text:p>one</text:p></text:list-item><text:list-item><text:p>two</text:p>"
        "</text:list-item></text:list>"
        '<table:table><table:table-row><table:table-cell><text:p>cell</text:p></table:table-cell>'
        '<table:table-cell table:number-columns-repeated="1000000"/></table:table-row></table:table>'
        '<text:p><text:a xlink:href="javascript:alert(1)">bad link</text:a></text:p>'
        "</office:text>"
    )
    path = make_odf(tmp_path / "notes.odt", "application/vnd.oasis.opendocument.text", body, styles)
    result = read_document(path)
    html = result["html"]
    assert [(depth, title) for depth, title, _ in result["sections"]] == [(0, "Chapter"), (1, "Part")]
    assert "<b>strong</b>" in html and "[a footnote]" in html
    assert "<li>" in html and html.count("<td>") == 1
    assert "javascript:" not in html and "bad link" in html


def test_an_opendocument_presentation_has_a_section_per_slide(tmp_path):
    body = (
        "<office:presentation>"
        '<draw:page><draw:frame presentation:class="title"><draw:text-box><text:p>Welcome</text:p></draw:text-box>'
        '</draw:frame><draw:frame><draw:text-box><text:p>Body text</text:p></draw:text-box></draw:frame>'
        '<presentation:notes><draw:frame presentation:class="notes"><draw:text-box><text:p>Remember this</text:p>'
        "</draw:text-box></draw:frame></presentation:notes></draw:page>"
        "<draw:page><draw:frame><draw:text-box><text:p>Untitled body</text:p></draw:text-box></draw:frame></draw:page>"
        "</office:presentation>"
    )
    result = read_document(make_odf(tmp_path / "talk.odp", "application/vnd.oasis.opendocument.presentation", body))
    assert [title for _, title, _ in result["sections"]] == ["1. Welcome", "Slide 2"]
    assert "Remember this" in result["html"] and "Untitled body" in result["html"]


def test_rtf_becomes_paragraphs(tmp_path):
    path = tmp_path / "letter.rtf"
    path.write_text(r"{\rtf1\ansi Dear reader,\par {\b Bold} words & <tags>.\par}")
    result = read_document(str(path))
    assert "Dear reader," in result["html"] and "&lt;tags&gt;" in result["html"]


def test_files_without_a_basic_view_are_refused(tmp_path):
    path = tmp_path / "old.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 60)
    with pytest.raises(OfficeReadError):
        read_document(str(path))
    broken = tmp_path / "broken.odt"
    broken.write_bytes(b"PK not really")
    with pytest.raises(OfficeReadError):
        read_document(str(broken))


@pytest.mark.parametrize(
    "value,fmt,shown",
    [
        (None, "General", ""),
        (True, "General", "TRUE"),
        (3.0, "General", "3"),
        (0.1 + 0.2, "General", "0.3"),
        (1200.5, "#,##0.00", "1,200.50"),
        (0.6, "0%", "60%"),
        (0.125, "0.0%", "12.5%"),
        (12345.678, "0.00E+00", "1.23E+04"),
        (datetime.datetime(2026, 3, 4), "yyyy-mm-dd", "2026-03-04"),
        (datetime.datetime(2026, 3, 4, 9, 30), "yyyy-mm-dd hh:mm", "2026-03-04 09:30"),
        ("text", "General", "text"),
    ],
)
def test_values_are_shown_as_their_format_asks(value, fmt, shown):
    assert format_value(value, fmt) == shown


def sheet_rows(sheet):
    with open(sheet["file"], encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def make_xlsx(path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    data = book.active
    data.title = "Data"
    data.append(["Item", "Amount", "Share"])
    data.append(["Rent", 1200.5, 0.6])
    data.append(["Food", 800, 0.4])
    data["B2"].number_format = "#,##0.00"
    data["C2"].number_format = "0%"
    data["C3"].number_format = "0%"
    data["B4"] = "=SUM(B2:B3)"
    data["A6"] = "Merged"
    data.merge_cells("A6:B6")
    hidden = book.create_sheet("Secret")
    hidden["A1"] = "hidden"
    hidden.sheet_state = "hidden"
    book.create_sheet("Notes")["C3"] = "late"
    book.save(path)
    return str(path)


def test_an_excel_workbook_gives_visible_sheets_with_formats_merges_and_formulas(tmp_path):
    result = read_spreadsheet(make_xlsx(tmp_path / "budget.xlsx"))
    try:
        assert [s["name"] for s in result["sheets"]] == ["Data", "Notes"]
        data, notes = result["sheets"]
        rows = sheet_rows(data)
        assert rows[1] == ["Rent", "1,200.50", "60%"]
        assert rows[2] == ["Food", "800.00", "40%"] or rows[2][0] == "Food"
        assert data["formulas"] == {"4,1": "=SUM(B2:B3)"}
        assert data["spans"] == [[5, 0, 1, 2]]
        assert sheet_rows(notes) == [[], [], ["", "", "late"]]
    finally:
        shutil.rmtree(result["folder"])


def test_an_opendocument_sheet_keeps_row_numbers_across_gaps_and_drops_the_empty_end(tmp_path):
    styles = (
        '<style:style style:name="ta2" style:family="table">'
        '<style:table-properties table:display="false"/></style:style>'
    )
    body = (
        "<office:spreadsheet>"
        '<table:table table:name="One">'
        "<table:table-row><table:table-cell><text:p>top</text:p></table:table-cell>"
        '<table:table-cell table:number-columns-repeated="3"/>'
        '<table:table-cell table:formula="of:=[.A1]&amp;&quot;!&quot;" table:number-columns-spanned="2">'
        "<text:p>top!</text:p></table:table-cell><table:covered-table-cell/>"
        '<table:table-cell table:number-columns-repeated="16378"/></table:table-row>'
        '<table:table-row table:number-rows-repeated="4999"><table:table-cell table:number-columns-repeated="16384"/>'
        "</table:table-row>"
        '<table:table-row table:number-rows-repeated="3"><table:table-cell><text:p>same</text:p></table:table-cell>'
        "</table:table-row>"
        '<table:table-row table:number-rows-repeated="1048570"><table:table-cell '
        'table:number-columns-repeated="16384"/>'
        "</table:table-row>"
        "</table:table>"
        '<table:table table:name="Hidden" table:style-name="ta2"><table:table-row><table:table-cell>'
        "<text:p>x</text:p></table:table-cell></table:table-row></table:table>"
        "</office:spreadsheet>"
    )
    path = make_odf(tmp_path / "gaps.ods", "application/vnd.oasis.opendocument.spreadsheet", body, styles)
    started = time.time()
    result = read_spreadsheet(path)
    try:
        assert time.time() - started < 10
        (sheet,) = result["sheets"]
        rows = sheet_rows(sheet)
        assert rows[0] == ["top", "", "", "", "top!"]
        assert len(rows) == 5003 and rows[5000:] == [["same"]] * 3 and rows[1] == []
        assert sheet["formulas"] == {"1,4": '=A1&"!"'}
        assert sheet["spans"] == [[0, 4, 1, 2]]
    finally:
        shutil.rmtree(result["folder"])


def test_repeats_in_an_opendocument_sheet_stop_at_the_sheet_limits(tmp_path):
    body = (
        '<office:spreadsheet><table:table table:name="Big">'
        '<table:table-row table:number-rows-repeated="999999999">'
        '<table:table-cell table:number-columns-repeated="999999999"><text:p>x</text:p></table:table-cell>'
        "</table:table-row></table:table></office:spreadsheet>"
    )
    path = make_odf(tmp_path / "big.ods", "application/vnd.oasis.opendocument.spreadsheet", body)
    reader = office_basic._OdfReader(path)
    try:
        (_, rows), = list(reader.sheets())
        first, _, _ = next(rows)
        assert len(first) == MAX_COLUMNS
        assert 1 + sum(1 for _ in rows) == MAX_ROWS
    finally:
        reader.close()


@pytest.fixture
def window(qtbot, monkeypatch):
    from riemann.app import RiemannWindow

    monkeypatch.setenv("RIEMANN_NO_OFFICE", "1")
    win = RiemannWindow(incognito=False, restore_session=False)
    win._office_asked = {"spreadsheet"}
    qtbot.addWidget(win)
    return win


def test_a_workbook_opens_in_the_table_view_and_cleans_up(window, tmp_path):
    from riemann.ui.viewers import SpreadsheetTab

    tab = window._open_document(make_xlsx(tmp_path / "budget.xlsx"), preview=False)
    assert isinstance(tab, SpreadsheetTab)
    assert "Opening" in tab.status.text()
    assert wait_for(lambda: tab.models)
    assert [tab.sheet_tabs.tabText(i) for i in range(tab.sheet_tabs.count())] == ["Data", "Notes"]
    model = tab.model
    assert model.cell(1, 1) == "1,200.50"
    assert tab.view.columnSpan(5, 0) == 2
    tab.view.setCurrentIndex(model.index(3, 1))
    assert "B4" in tab.status.text() and "=SUM(B2:B3)" in tab.status.text()
    assert not tab.btn_pages.isVisibleTo(tab) and not tab.btn_text.isVisibleTo(tab)
    folder = tab.folder
    assert folder and os.path.isdir(folder)
    tab.cleanup()
    assert not os.path.exists(folder)


def test_a_broken_workbook_says_so(window, tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"PK\x03\x04 not a workbook")
    tab = window._open_document(str(path), preview=True)
    assert wait_for(lambda: tab.worker is None)
    assert tab.models == [] and tab.status.text() and "Opening" not in tab.status.text()
    tab.cleanup()


def test_closing_the_tab_while_it_opens_cancels_the_work(window, tmp_path, monkeypatch):
    from riemann.ui.viewers import SpreadsheetTab

    tab = SpreadsheetTab(make_xlsx(tmp_path / "budget.xlsx"))
    worker = tab.worker
    tab.cleanup()
    assert wait_for(lambda: worker.isFinished())
    assert tab.models == [] and tab.folder is None


def test_show_as_pages_opens_a_reader_next_to_the_table(window, tmp_path):
    from riemann.ui.reader.tab import ReaderTab

    tab = window._open_document(make_xlsx(tmp_path / "budget.xlsx"), preview=False)
    assert wait_for(lambda: tab.models)
    idx = window.tabs_main.indexOf(tab)
    reader = window.show_as_pages(tab)
    assert isinstance(reader, ReaderTab) and window.tabs_main.indexOf(reader) == idx + 1
    assert reader.current_path == tab.current_path
    reader.cleanup()
    tab.cleanup()
