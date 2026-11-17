"""
The window's File menu and tab icons treat every kind of document alike: the
tools that only work on PDFs sit in a PDF Tools submenu, Split takes the PDF
the current tab shows (a Markdown tab used to hand its own file to the PDF
splitter), and each kind of document gets its own tab icon.
"""

import os
import shutil
import time

import pytest
from PySide6.QtWidgets import QApplication, QMenu

from riemann.app import RiemannWindow
from riemann.core.assets import icon_path
from riemann.ui.file_icons import CLASS_ICONS, KIND_ICONS, OFFICE_ICONS, name_icon

CORPUS_PDF = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


def wait_for(condition, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


@pytest.fixture
def window(qtbot):
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)
    return win


def file_menu(window):
    return next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "File")


def test_only_the_pdf_tools_submenu_names_pdfs(window):
    menu = file_menu(window)
    names = [a.text() for a in menu.actions() if a.text()]
    assert [n for n in names if "PDF" in n] == ["PDF Tools"]
    assert "New Document Tab (Ctrl+T)" in names
    tools = [a.text() for a in window.pdf_tools_menu.actions()]
    assert tools == ["Split a PDF...", "Merge PDFs..."]


def test_split_takes_the_pdf_the_tab_shows_and_nothing_else(window, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n")
    reader = window._open_document(str(notes), preview=False)
    assert wait_for(lambda: reader.current_path)
    assert window._shown_pdf() == ""
    window._label_split_action()
    assert window.action_split_pdf.text() == "Split a PDF..."

    pdf = shutil.copy(CORPUS_PDF, tmp_path / "paper.pdf")
    reader = window._open_document(str(pdf), preview=False)
    assert wait_for(lambda: reader.current_doc is not None)
    window.tabs_main.setCurrentWidget(reader)
    assert window._shown_pdf() == os.path.abspath(pdf)
    window._label_split_action()
    assert window.action_split_pdf.text() == "Split paper.pdf..."


def test_each_kind_of_document_has_its_own_tab_icon(window, tmp_path):
    names = {}
    for name, data in (
        ("a.pdf", open(CORPUS_PDF, "rb").read()),
        ("b.csv", b"a,b\n1,2\n"),
        ("c.md", b"# c\n"),
        ("d.png", b"\x89PNG\r\n\x1a\n" + bytes(16)),
        ("e.py", b"print(1)\n"),
        ("f.docx", b""),
        ("g.pptx", b""),
        ("h.xlsx", b""),
        ("i.epub", b""),
        ("notes", b"plain words\n"),
    ):
        path = tmp_path / name
        path.write_bytes(data)
        names[name] = window._document_icon_name(str(path))
    assert names == {
        "a.pdf": "pdf.png",
        "b.csv": "sheet.png",
        "c.md": "text.png",
        "d.png": "gallery.png",
        "e.py": "code.png",
        "f.docx": "word.png",
        "g.pptx": "slide.png",
        "h.xlsx": "sheet.png",
        "i.epub": "epub.png",
        "notes": "text.png",
    }
    assert window._document_icon_name() == "file.svg"
    assert window._document_icon_name(str(tmp_path / "b.csv"), text=True) == "text.png"


def test_the_explorer_names_icons_without_reading_files():
    assert name_icon("/nowhere/report.docx") == "word.png"
    assert name_icon("/nowhere/deck.odp") == "slide.png"
    assert name_icon("/nowhere/Makefile") == "code.png"
    assert name_icon("/nowhere/photo.JPG") == "gallery.png"
    assert name_icon("/nowhere/page.html") == "browser.png"
    assert name_icon("/nowhere/blob") == ""


def test_every_document_icon_is_bundled():
    for name in set(KIND_ICONS.values()) | set(OFFICE_ICONS.values()) | {"code.png", "file.svg"}:
        assert os.path.exists(icon_path(name)), name


def test_history_shows_each_document_type_with_its_icon(window, tmp_path):
    from riemann.core.doctypes import DOCUMENT_CLASSES
    from riemann.ui.system.lists import HistoryTab

    assert set(CLASS_ICONS) == set(DOCUMENT_CLASSES)
    report = tmp_path / "report.docx"
    report.write_bytes(b"")
    window.history_manager.add(str(report), "pdf")
    window.history_manager.add(str(tmp_path), "folder")
    tab = HistoryTab(window)
    tab.refresh()
    tree = tab.trees["pdf"]
    item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(1) == "Word")
    assert not item.icon(1).isNull()
    assert not tab.trees["folder"].topLevelItem(0).icon(0).isNull()
    assert all(not tab.doc_class.itemIcon(i).isNull() for i in range(1, tab.doc_class.count()))
