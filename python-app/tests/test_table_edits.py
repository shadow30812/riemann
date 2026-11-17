"""
Changing CSV and TSV cells in the table view and saving them: the file keeps
its encoding, delimiter and line endings, nothing is written without asking,
a sorted or partly loaded table saves every row in the file's order, undo
reaches back to the saved state, and closing a tab with changes asks.
"""

import codecs
import os
import stat
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from riemann.app import RiemannWindow
from riemann.core import tables
from riemann.core.tables import open_delimited, write_delimited
from riemann.ui.viewers import TableTab
from riemann.ui.viewers import table_tab
from riemann.ui.viewers.table_tab import CHUNK_ROWS


def wait_for(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def write(path, data: bytes):
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"name,city\r\nAda,London\r\n", b"name,city\r\nAda,\"Paris, France\"\r\n"),
        (codecs.BOM_UTF8 + b"name;city\nAda;London\n", codecs.BOM_UTF8 + b"name;city\nAda;Paris, France\n"),
        (b"name\tcity\nAda\tLondon\n", b"name\tcity\nAda\tParis, France\n"),
        ("name,city\nZoë,Köln\n".encode("cp1252"), "name,city\nZoë,\"Paris, France\"\n".encode("cp1252")),
    ],
)
def test_saving_keeps_the_encoding_delimiter_and_line_endings(tmp_path, data, expected):
    path = write(tmp_path / "people.csv", data)
    source = open_delimited(path)
    rows = list(source.rows())
    rows[1][1] = "Paris, France"
    assert write_delimited(path, source, rows) == source.encoding
    assert open(path, "rb").read() == expected


def test_text_the_old_encoding_cant_hold_saves_as_utf8(tmp_path):
    path = write(tmp_path / "old.csv", "a,b\nZoë,1\n".encode("cp1252"))
    source = open_delimited(path)
    assert source.encoding == "cp1252"
    assert write_delimited(path, source, [["a", "b"], ["Zoë 🙂", "1"]]) == "utf-8"
    assert open(path, encoding="utf-8").read() == "a,b\nZoë 🙂,1\n"


def test_a_failed_save_leaves_the_file_and_no_temporary_file(tmp_path, monkeypatch):
    path = write(tmp_path / "keep.csv", b"a,b\n1,2\n")
    source = open_delimited(path)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(tables.os, "replace", fail)
    with pytest.raises(OSError):
        write_delimited(path, source, [["x", "y"]])
    assert open(path, "rb").read() == b"a,b\n1,2\n"
    assert os.listdir(tmp_path) == ["keep.csv"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_saving_keeps_the_permissions(tmp_path):
    path = write(tmp_path / "private.csv", b"a\n1\n")
    os.chmod(path, 0o640)
    write_delimited(path, open_delimited(path), [["a"], ["2"]])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


def edit(tab, row, column, text):
    index = tab.model.index(row, column)
    tab.view.setCurrentIndex(index)
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key.Key_F2)
    editor = tab.cell_editor()
    assert editor is not None and editor.text() == tab.model.cell(row, column)
    editor.selectAll()
    QTest.keyClicks(editor, text)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert wait_for(lambda: tab.model.cell(row, column) == text)


@pytest.fixture
def tab(qtbot, tmp_path):
    widget = TableTab(write(tmp_path / "scores.csv", b"name,score\nAda,3\nBob,1\nCy,2\n"))
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    widget.cleanup()


def test_nothing_is_written_until_the_person_says_save(tab, monkeypatch):
    path = tab.current_path
    edit(tab, 0, 1, "30")
    assert tab.model.cell(0, 1) == "30" and tab.modified and tab.btn_save.isVisibleTo(tab)
    asked = []
    monkeypatch.setattr(tab, "_ask", lambda closing: asked.append(closing) or "")
    assert tab.save() is False
    assert asked == [False] and open(path, "rb").read() == b"name,score\nAda,3\nBob,1\nCy,2\n"
    monkeypatch.setattr(tab, "_ask", lambda closing: "save")
    QTest.keyClick(tab.view, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert open(path, "rb").read() == b"name,score\nAda,30\nBob,1\nCy,2\n"
    assert not tab.modified and not tab.btn_save.isVisibleTo(tab)


def test_a_sorted_table_saves_in_the_files_order(tab, monkeypatch):
    tab.sort_by(1)
    assert [tab.model.cell(r, 0) for r in range(3)] == ["Bob", "Cy", "Ada"]
    edit(tab, 0, 0, "Bea")
    monkeypatch.setattr(tab, "_ask", lambda closing: "save")
    assert tab.save()
    assert open(tab.current_path, "rb").read() == b"name,score\nAda,3\nBea,1\nCy,2\n"


def test_rows_not_loaded_yet_are_saved_too(qtbot, tmp_path, monkeypatch):
    lines = "n,square\n" + "".join(f"{i},{i * i}\n" for i in range(CHUNK_ROWS * 3))
    path = write(tmp_path / "squares.csv", lines.encode())
    tab = TableTab(path)
    qtbot.addWidget(tab)
    assert not tab.model.sheet.exhausted
    tab.model.setData(tab.model.index(0, 1), "zero")
    monkeypatch.setattr(tab, "_ask", lambda closing: "save")
    assert tab.save()
    assert open(path).read() == lines.replace("0,0\n", "0,zero\n", 1)
    tab.cleanup()


def test_delete_empties_cells_and_undo_goes_back_to_the_saved_state(tab, monkeypatch):
    tab.view.setFocus()
    tab.view.selectRow(1)
    QTest.keyClick(tab.view, Qt.Key.Key_Delete)
    assert tab.model.cell(1, 0) == "" and tab.model.cell(1, 1) == "" and tab.modified
    QTest.keyClick(tab.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert tab.model.cell(1, 0) == "Bob" and tab.model.cell(1, 1) == "1" and not tab.modified
    edit(tab, 2, 1, "9")
    monkeypatch.setattr(tab, "_ask", lambda closing: "save")
    assert tab.save()
    QTest.keyClick(tab.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert tab.model.cell(2, 1) == "2" and tab.modified
    tab.model.undo()
    assert tab.modified


def test_an_edit_after_undoing_past_the_save_still_counts_as_a_change(tab, monkeypatch):
    edit(tab, 0, 1, "5")
    monkeypatch.setattr(tab, "_ask", lambda closing: "save")
    assert tab.save()
    tab.model.undo()
    edit(tab, 1, 1, "7")
    assert tab.modified


def test_previews_large_files_and_spreadsheets_are_not_editable(qtbot, tmp_path, monkeypatch):
    path = write(tmp_path / "a.csv", b"a,b\n1,2\n")
    preview = TableTab(path, preview=True)
    qtbot.addWidget(preview)
    assert not preview.editable and not (preview.model.flags(preview.model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)
    monkeypatch.setattr(table_tab, "EDIT_LIMIT_BYTES", 4)
    large = TableTab(path)
    qtbot.addWidget(large)
    assert not large.editable
    assert large.model.setData(large.model.index(0, 0), "x") is False
    preview.cleanup()
    large.cleanup()


def test_ctrl_c_in_a_cell_editor_copies_its_text(tab):
    tab.view.setCurrentIndex(tab.model.index(0, 0))
    QTest.keyClick(tab.view, Qt.Key.Key_F2)
    editor = tab.cell_editor()
    editor.setText("needle")
    editor.selectAll()
    QApplication.clipboard().clear()
    tab.copy_cells()
    assert QApplication.clipboard().text() == "needle"


@pytest.fixture
def window(qtbot):
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)
    return win


def test_closing_a_changed_table_asks_and_cancel_keeps_it(window, tmp_path, monkeypatch):
    path = write(tmp_path / "close.csv", b"a,b\n1,2\n")
    tab = window._open_document(path, preview=False)
    tab.model.setData(tab.model.index(0, 0), "9")
    monkeypatch.setattr(tab, "_ask", lambda closing: "")
    window.close_tab(window.tabs_main.indexOf(tab))
    assert window.tabs_main.indexOf(tab) != -1
    assert not window.confirm_close()
    monkeypatch.setattr(tab, "_ask", lambda closing: "discard")
    window.close_tab(window.tabs_main.indexOf(tab))
    assert window.tabs_main.indexOf(tab) == -1
    assert open(path, "rb").read() == b"a,b\n1,2\n"


def test_save_as_moves_the_tab_to_the_new_file(window, tmp_path, monkeypatch):
    path = write(tmp_path / "first.csv", b"a,b\n1,2\n")
    tab = window._open_document(path, preview=False)
    tab.model.setData(tab.model.index(0, 1), "20")
    target = str(tmp_path / "second.csv")
    monkeypatch.setattr(tab, "_ask", lambda closing: "save_as")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (target, ""))
    assert tab.save()
    assert open(path, "rb").read() == b"a,b\n1,2\n"
    assert open(target, "rb").read() == b"a,b\n1,20\n"
    assert tab.current_path == target
    assert window.tabs_main.tabText(window.tabs_main.indexOf(tab)) == "second.csv"
    tab.cleanup()
