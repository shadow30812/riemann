"""
E-books: the HTML cleaner every converted document passes through, the EPUB
reader (chapters joined into one page, links, pictures, table of contents,
size limits), the converter child process (answers, errors, time limit,
crashes, cancel) and the reader tab showing a book with its sections.
"""

import base64
import os
import sys
import threading
import time
import zipfile

import pytest
from PySide6.QtWidgets import QApplication

from riemann.core import convert_process, epub
from riemann.core.convert_process import Conversion, ConvertError, convert
from riemann.core.doctypes import document_class, document_kind, is_reader_name
from riemann.core.epub import EpubError, read_epub
from riemann.core.html_clean import clean_html

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""


def chapter(body: str, title: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>{title}</title><link rel=\"stylesheet\" href=\"style.css\"/></head><body>{body}</body></html>"
    )


def opf(items: str, spine: str, toc: str = "") -> str:
    return f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Test Book</dc:title><dc:creator>A. Writer</dc:creator>
  </metadata>
  <manifest>{items}</manifest>
  <spine{f' toc="{toc}"' if toc else ''}>{spine}</spine>
</package>"""


def make_epub(path, files: dict, mimetype: bool = True) -> str:
    with zipfile.ZipFile(path, "w") as z:
        if mimetype:
            z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        for name, data in files.items():
            z.writestr(name, data)
    return str(path)


def book_files(nav: bool = True, ncx: bool = False) -> dict:
    items = (
        '<item id="c1" href="text/one.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="text/two.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="pic" href="images/dot.png" media-type="image/png"/>'
    )
    files = {
        "OEBPS/text/one.xhtml": chapter(
            '<h1 id="start">One</h1><p onclick="steal()">See <a href="two.xhtml#fig">the figure</a>.</p>'
            '<script>steal()</script><h2 id="s1">Sub</h2><img src="../images/dot.png" alt="dot"/>'
            '<p><a href="https://example.org/">web</a> <a href="javascript:steal()">bad</a></p>',
            "Chapter one",
        ),
        "OEBPS/text/two.xhtml": chapter('<h1>Two</h1><p id="fig" style="color:red">Figure</p><a href="#fig">here</a>'),
        "OEBPS/images/dot.png": PNG,
    }
    toc = ""
    if nav:
        items += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        files["OEBPS/nav.xhtml"] = chapter(
            '<nav epub:type="toc"><ol><li><a href="text/one.xhtml">One</a>'
            '<ol><li><a href="text/one.xhtml#s1">Sub</a></li></ol></li>'
            '<li><a href="text/two.xhtml">Two</a></li></ol></nav>'
        )
    if ncx:
        items += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        toc = "ncx"
        files["OEBPS/toc.ncx"] = (
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
            '<navPoint id="a"><navLabel><text>First</text></navLabel><content src="text/one.xhtml"/>'
            '<navPoint id="b"><navLabel><text>Inner</text></navLabel><content src="text/one.xhtml#s1"/></navPoint>'
            "</navPoint></navMap></ncx>"
        )
    files["OEBPS/content.opf"] = opf(items, '<itemref idref="c1"/><itemref idref="c2"/>', toc)
    return files


def wait_for(condition, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_the_cleaner_keeps_text_and_drops_anything_that_runs_or_reaches_out():
    out = clean_html(
        '<div onclick="x()" style="color:red" class="c"><p>Hi <b>there</b></p>'
        '<script>alert(1)</script><iframe src="https://e.org"></iframe>'
        '<a href="javascript:alert(1)">js</a><a href="file:///etc/passwd">file</a>'
        '<a href="https://ok.org/">ok</a><a href="#here">anchor</a>'
        '<img src="https://tracker.example/p.gif"><img src="data:image/png;base64,AAAA" onerror="x()">'
        '<form><input value="pw"></form><custom-tag>kept text</custom-tag><!-- comment --></div>',
        id_prefix="p-",
    )
    for gone in ("script", "alert", "iframe", "onclick", "style", "class", "javascript", "file:", "tracker",
                 "onerror", "input", "form", "comment", "custom-tag"):
        assert gone not in out
    for kept in ("<b>there</b>", '<a href="https://ok.org/">ok</a>', 'href="#p-here"',
                 'src="data:image/png;base64,AAAA"', "kept text", "</p>jsfile<a"):
        assert kept in out


def test_the_cleaner_reads_xhtml_without_entities_or_namespaces():
    xhtml = (
        '<?xml version="1.0"?><!DOCTYPE html [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title/></head><body><p id="a">&xxe;text</p>'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<image xlink:href="cover.png"/></svg></body></html>'
    )
    out = clean_html(xhtml.encode(), resolve_image=lambda src: "data:image/png;base64,QUJD", xml=True, id_prefix="c0-")
    assert "root:" not in out and "xmlns" not in out
    assert '<p id="c0-a">' in out and "text" in out
    assert '<img src="data:image/png;base64,QUJD"' in out


def test_epub_chapters_join_into_one_page_with_working_links(tmp_path):
    path = make_epub(tmp_path / "book.epub", book_files())
    book = read_epub(path)
    page = book["html"]
    assert book["title"] == "The Test Book"
    assert "<h1>The Test Book</h1>" in page and "A. Writer" in page
    assert '<section id="c0">' in page and '<section id="c1">' in page
    assert page.index("One") < page.index("Two")
    assert '<a href="#c1-fig">the figure</a>' in page
    assert '<p id="c1-fig">' in page and '<a href="#c1-fig">here</a>' in page
    assert '<a href="https://example.org/">web</a>' in page
    for gone in ("steal", "onclick", "style", "stylesheet", "javascript"):
        assert gone not in page
    assert "data:image/png;base64," in page
    assert [tuple(s) for s in book["sections"]] == [(0, "One", "#c0"), (1, "Sub", "#c0-s1"), (0, "Two", "#c1")]
    assert book["note"] == ""


def test_epub2_books_use_the_ncx_and_others_fall_back_to_headings(tmp_path):
    ncx = read_epub(make_epub(tmp_path / "old.epub", book_files(nav=False, ncx=True)))
    assert [tuple(s) for s in ncx["sections"]] == [(0, "First", "#c0"), (1, "Inner", "#c0-s1")]
    bare = read_epub(make_epub(tmp_path / "bare.epub", book_files(nav=False)))
    assert [tuple(s) for s in bare["sections"]] == [(0, "One", "#c0"), (0, "Two", "#c1")]


def test_large_pictures_are_left_out_with_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(epub, "IMAGE_LIMIT", 10)
    book = read_epub(make_epub(tmp_path / "pics.epub", book_files()))
    assert "data:image" not in book["html"]
    assert "pictures" in book["note"]


def test_broken_books_are_refused_with_a_message(tmp_path):
    junk = tmp_path / "junk.epub"
    junk.write_bytes(b"not a zip at all")
    with pytest.raises(EpubError):
        read_epub(str(junk))
    with zipfile.ZipFile(tmp_path / "empty.epub", "w") as z:
        z.writestr("mimetype", "application/epub+zip")
    with pytest.raises(EpubError, match="container"):
        read_epub(str(tmp_path / "empty.epub"))
    files = book_files()
    files["OEBPS/content.opf"] = opf("", "")
    with pytest.raises(EpubError, match="no chapters"):
        read_epub(make_epub(tmp_path / "hollow.epub", files))


def test_a_zip_that_lies_about_its_sizes_stops_early(tmp_path, monkeypatch):
    monkeypatch.setattr(epub, "BOOK_LIMIT", 2000)
    files = book_files()
    files["OEBPS/text/two.xhtml"] = chapter("<p>" + "x" * 50_000 + "</p>")
    book = read_epub(make_epub(tmp_path / "big.epub", files))
    assert "x" * 1000 not in book["html"]
    assert "One" in book["html"]


def test_epub_is_an_ebook_kind_even_without_its_suffix(tmp_path):
    path = make_epub(tmp_path / "book.epub", book_files())
    assert document_kind(path) == "ebook"
    assert document_class(path) == "E-book"
    assert is_reader_name("Novel.EPUB")
    renamed = tmp_path / "download"
    os.rename(path, renamed)
    assert document_kind(str(renamed)) == "ebook"
    plain = tmp_path / "archive.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", "hello")
    assert document_kind(str(plain)) == "binary"


def test_the_converter_child_returns_the_book(tmp_path):
    path = make_epub(tmp_path / "book.epub", book_files())
    result = convert("ebook", path)
    assert next(tuple(s) for s in result["sections"]) == (0, "One", "#c0")
    with pytest.raises(ConvertError, match="container"):
        junk = tmp_path / "junk.epub"
        with zipfile.ZipFile(junk, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
        convert("ebook", str(junk))
    with pytest.raises(ConvertError):
        Conversion("spreadsheet-of-doom", path)


def test_a_converter_that_runs_too_long_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        convert_process, "child_command", lambda kind, path, out: [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    start = time.time()
    with pytest.raises(ConvertError, match="more than 1 seconds"):
        Conversion("ebook", str(tmp_path / "x.epub"), timeout=1).run()
    assert time.time() - start < 10


def test_a_converter_that_crashes_only_ends_its_child(tmp_path, monkeypatch):
    monkeypatch.setattr(
        convert_process, "child_command", lambda kind, path, out: [sys.executable, "-c", "import os; os._exit(3)"]
    )
    with pytest.raises(ConvertError, match="code 3"):
        convert("ebook", str(tmp_path / "x.epub"))
    assert not [n for n in os.listdir(tmp_path) if n.startswith("riemann-convert-")]


def test_cancel_kills_a_running_conversion(tmp_path, monkeypatch):
    monkeypatch.setattr(
        convert_process, "child_command", lambda kind, path, out: [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    conversion = Conversion("ebook", str(tmp_path / "x.epub"))
    errors = []

    def run():
        try:
            conversion.run()
        except ConvertError as e:
            errors.append(str(e))

    thread = threading.Thread(target=run)
    thread.start()
    end = time.time() + 10
    while conversion._proc is None and time.time() < end:
        time.sleep(0.01)
    conversion.cancel()
    thread.join(10)
    assert not thread.is_alive()
    assert errors == ["Cancelled."]


def test_the_reader_shows_a_book_with_its_sections(qtbot, tmp_path):
    from riemann.app import RiemannWindow
    from riemann.ui.reader.tab import ReaderTab

    files = book_files()
    files["OEBPS/text/one.xhtml"] = files["OEBPS/text/one.xhtml"].replace(
        "</body>", "<p>Long text.</p>" * 300 + "</body>"
    )
    path = make_epub(tmp_path / "book.epub", files)
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show()
    tab = window._open_document(path, preview=False)
    assert isinstance(tab, ReaderTab) and tab.text_document
    assert "Opening book.epub" in tab._text_page_html()
    assert wait_for(lambda: tab._converted is not None)
    assert tab._document_sections[0] == (0, "One", "#c0")
    assert not tab.sidebar_nav.btn_thumbs.isEnabled()
    assert tab.sidebar_nav.tree.topLevelItemCount() == 2
    tab.toggle_sidebar()
    assert tab.sidebar_nav.isVisible()
    page = tab._text_page_html()
    assert "the figure" in page and "katex" not in page.lower()

    html = []
    assert wait_for(lambda: (tab.web.page().toHtml(html.append), len(html) and "the figure" in html[-1])[1])
    tab.sidebar_nav.resize(250, 600)
    tab.sidebar_nav._on_outline_clicked(tab.sidebar_nav.tree.topLevelItem(1), 0)
    scrolled = []
    assert wait_for(
        lambda: (
            tab.web.page().runJavaScript("window.scrollY", 0, scrolled.append),
            bool(scrolled) and scrolled[-1] > 0,
        )[1]
    )

    tab.toggle_theme()
    assert tab._converted is not None and tab.convert_worker is None
    tab.cleanup()


def test_a_broken_book_says_so_in_the_tab(qtbot, tmp_path):
    from riemann.ui.reader.tab import ReaderTab

    junk = tmp_path / "broken.epub"
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
    tab = ReaderTab()
    qtbot.addWidget(tab)
    tab.load_document(str(junk))
    assert wait_for(lambda: tab._convert_error != "")
    assert "open broken.epub. This isn" in tab._text_page_html()
    assert not tab._document_sections
    tab.cleanup()


def test_closing_a_tab_while_a_book_converts_kills_the_child(qtbot, tmp_path, monkeypatch):
    from riemann.ui.reader.tab import ReaderTab

    monkeypatch.setattr(
        convert_process, "child_command", lambda kind, path, out: [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    path = make_epub(tmp_path / "slow.epub", book_files())
    tab = ReaderTab()
    qtbot.addWidget(tab)
    tab.load_document(path)
    worker = tab.convert_worker
    end = time.time() + 10
    while worker.conversion._proc is None and time.time() < end:
        time.sleep(0.01)
    tab.cleanup()
    assert worker.wait(10_000)
    assert worker.conversion._proc.poll() is not None
