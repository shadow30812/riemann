"""
Text and source files open in the reader the way Markdown does.

The page the reader would show is captured from its web view (setHtml for
small pages, the cache file it loads for big ones) rather than rendered, since
no event loop runs under pytest.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from riemann.core.constants import ViewMode
from riemann.ui.reader.mixins.document import SET_HTML_LIMIT, TEXT_VIEW_LIMIT
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.viewers import HexTab


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def reader(qapp):
    settings = MagicMock()
    settings.return_value.value.side_effect = lambda key, default=None, type=None: default
    with patch("riemann.ui.reader.tab.app_settings", settings), patch(
        "riemann.ui.reader.mixins.appearance.app_settings", settings
    ):
        tab = ReaderTab()
        yield tab
        tab.cleanup()
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def shown_page(tab, path):
    pages = []
    with patch.object(tab._get_or_create_web_view(), "setHtml", side_effect=pages.append):
        tab.load_document(str(path))
    return pages[-1] if pages else None


@pytest.mark.parametrize("name", ["script.py", "notes.txt", "LICENSE", "style.css", "app.js"])
def test_text_files_open_in_the_reader_as_a_page(reader, tmp_path, name):
    path = tmp_path / name
    path.write_text("first_line = 'hello <world>'\n")
    page = shown_page(reader, path)
    assert reader.text_document is True
    assert reader.current_path == str(path)
    assert reader.view_mode == ViewMode.REFLOW
    assert reader.stack.currentIndex() == 1
    assert "first_line" in page and "&lt;world&gt;" in page and "<world>" not in page


def test_markdown_still_renders_as_markdown(reader, tmp_path):
    path = tmp_path / "readme.md"
    path.write_text("# Heading\n\nSome *text*.")
    page = shown_page(reader, path)
    assert "<h1>Heading</h1>" in page and "<em>text</em>" in page


def test_a_page_over_the_sethtml_limit_loads_from_a_cache_file(reader, tmp_path):
    path = tmp_path / "big.log"
    line = "x" * 99 + "\n"
    path.write_text(line * (3 * 1024 * 1024 // len(line)))
    web = reader._get_or_create_web_view()
    with patch.object(web, "setHtml") as set_html, patch.object(web, "load") as load:
        reader.load_document(str(path))
    set_html.assert_not_called()
    page_file = load.call_args.args[0].toLocalFile()
    assert page_file == reader._text_page_file
    assert os.path.getsize(page_file) > SET_HTML_LIMIT
    with open(page_file, encoding="utf-8") as f:
        assert line.strip() in f.read()
    reader.cleanup()
    assert not os.path.exists(page_file)


def test_a_file_over_the_view_limit_is_cut_off_with_a_note(reader, tmp_path):
    path = tmp_path / "huge.txt"
    with open(path, "wb") as f:
        f.truncate(TEXT_VIEW_LIMIT + 1024 * 1024)
    with open(path, "r+b") as f:
        f.write(b"start of the file")
    web = reader._get_or_create_web_view()
    with patch("riemann.core.doctypes.looks_like_text", return_value=True), patch.object(web, "load"):
        reader.load_document(str(path))
    with open(reader._text_page_file, encoding="utf-8") as f:
        page = f.read()
    assert "start of the file" in page
    assert "Showing the first 20 MB of 21 MB." in page


def test_the_page_theme_rerenders_the_text_page(reader, tmp_path):
    path = tmp_path / "main.c"
    path.write_text("int main(void) { return 0; }\n")
    shown_page(reader, path)
    pages = []
    with patch.object(reader.web, "setHtml", side_effect=pages.append), patch.object(reader, "show_toast"):
        reader.toggle_theme()
        reader._rebuild_debounce_timer.timeout.emit()
    from riemann.ui.palette import tokens

    assert pages and tokens(True)["canvas"] in pages[-1]


def test_a_pdf_after_a_text_file_leaves_text_mode(reader, tmp_path):
    import pikepdf

    text = tmp_path / "notes.txt"
    text.write_text("hello")
    shown_page(reader, text)
    pdf = tmp_path / "blank.pdf"
    with pikepdf.new() as document:
        document.add_blank_page()
        document.save(pdf)
    reader.load_document(str(pdf))
    reader.load_worker.wait(10000)
    QCoreApplication.processEvents()
    assert reader.current_doc is not None
    assert reader.text_document is False
    assert reader.current_path == str(pdf)


def test_the_window_routes_text_to_the_reader_and_binaries_to_the_hex_view(qtbot, tmp_path):
    from riemann.app import RiemannWindow

    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    source = tmp_path / "tool.py"
    source.write_text("print('hi')\n")
    opened = window._open_document(str(source), preview=False)
    assert isinstance(opened, ReaderTab) and opened.text_document is True

    binary = tmp_path / "photo.dat"
    binary.write_bytes(b"\0\1\2\3 neither text nor a picture")
    opened = window._open_document(str(binary), preview=False)
    assert isinstance(opened, HexTab) and opened.current_path == str(binary)
    assert window._document_icon_name(str(binary)) == "binary.png"


def test_a_document_with_no_telling_name_is_classed_by_its_contents(tmp_path):
    from riemann.core.doctypes import document_class

    pdf = tmp_path / "download"
    pdf.write_bytes(b"%PDF-1.7\n")
    text = tmp_path / "notes"
    text.write_text("plain\n")
    assert document_class(str(pdf)) == "PDF"
    assert document_class(str(text)) == "Text"
    assert document_class(str(tmp_path / "missing")) == "Text"
    blob = tmp_path / "blob"
    blob.write_bytes(b"\0\1\2")
    assert document_class(str(blob)) == "Other"
