"""
The table view for CSV and TSV: delimiters, encodings and headers detected,
rows loaded in chunks, sorting by value, Find past the loaded rows, copying
cells, "Open as text", and the window routing tables to it.
"""

import codecs
import os
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from riemann.app import RiemannWindow
from riemann.core.doctypes import document_class, document_kind, is_text_document
from riemann.core.tables import column_name, detect_encoding, number_value, open_delimited, sort_key
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.viewers import TableTab
from riemann.ui.viewers.table_tab import CHUNK_ROWS, TableSheet


def wait_for(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def write(path, text, encoding="utf-8"):
    path.write_bytes(text.encode(encoding))
    return str(path)


def cells(tab, row):
    return [tab.model.cell(row, c) for c in range(tab.model.columnCount())]


def test_csv_and_tsv_are_tables_and_spreadsheets_in_history(tmp_path):
    assert document_kind("sales.CSV") == "table"
    assert document_kind("log.tsv") == "table"
    assert document_class("sales.csv") == "Spreadsheet"
    assert is_text_document(write(tmp_path / "a.csv", "x,y\n"))


@pytest.mark.parametrize(
    "text,delimiter",
    [("name,age\nAda,36\nAlan,41\n", ","), ("name;age\nAda;36\nAlan;41\n", ";"), ("name\tage\nAda\t36\nAlan\t41\n", "\t"),
     ("name|age\nAda|36\nAlan|41\n", "|")],
)
def test_the_delimiter_and_header_are_detected(tmp_path, text, delimiter):
    source = open_delimited(write(tmp_path / "people.csv", text))
    assert source.dialect.delimiter == delimiter
    assert source.has_header
    assert list(source.rows())[1] == ["Ada", "36"]


def test_encodings(tmp_path):
    assert detect_encoding(codecs.BOM_UTF8 + b"a") == "utf-8-sig"
    assert detect_encoding(codecs.BOM_UTF16_LE + "a".encode("utf-16-le")) == "utf-16"
    assert detect_encoding("café".encode()) == "utf-8"
    assert detect_encoding("café".encode()[:-1]) == "utf-8"
    assert detect_encoding("café crème".encode("cp1252")) == "cp1252"
    bom = open_delimited(write(tmp_path / "bom.csv", "﻿city,pop\nMünchen,1\n"))
    assert next(bom.rows()) == ["city", "pop"]
    excel = open_delimited(write(tmp_path / "excel.txt.tsv", "﻿city\tpop\nZürich\t2\n", "utf-16"))
    assert list(excel.rows())[1] == ["Zürich", "2"]
    old = open_delimited(write(tmp_path / "old.csv", "city,pop\nSão Paulo,3\n", "cp1252"))
    assert list(old.rows())[1][0] == "São Paulo"


def test_a_quoted_field_may_hold_commas_and_line_breaks(tmp_path):
    source = open_delimited(write(tmp_path / "notes.csv", 'id,note\n1,"one, two\nthree"\n2,plain\n'))
    assert list(source.rows())[1] == ["1", "one, two\nthree"]


def test_numbers_sort_by_value_and_blanks_go_last():
    values = ["10", "", "9", "1,200", "apple", "Banana", "-3", "(4)", "50%", "$7.50"]
    assert sorted(values, key=sort_key) == ["(4)", "-3", "$7.50", "9", "10", "50%", "1,200", "apple", "Banana", ""]
    assert number_value("1,234.5") == 1234.5
    assert number_value("12a") is None
    assert [column_name(i) for i in (0, 25, 26, 27, 701, 702)] == ["A", "Z", "AA", "AB", "ZZ", "AAA"]


def test_rows_load_in_chunks_as_you_scroll(qtbot, tmp_path):
    lines = "n,square\n" + "".join(f"{i},{i * i}\n" for i in range(CHUNK_ROWS * 3 + 7))
    tab = TableTab(write(tmp_path / "squares.csv", lines))
    qtbot.addWidget(tab)
    tab.resize(600, 400)
    tab.show()
    assert tab.model.rowCount() == CHUNK_ROWS
    assert "+ rows" in tab.lbl_size.text()
    tab.model.fetchMore()
    assert tab.model.rowCount() == CHUNK_ROWS * 2
    assert tab.model.headerData(0, Qt.Orientation.Horizontal) == "n"
    assert tab.model.headerData(0, Qt.Orientation.Vertical) == "2"
    tab.cleanup()


def test_sorting_reads_every_row_then_cycles_up_down_and_back(qtbot, tmp_path):
    lines = "n\n" + "".join(f"{(i * 7919) % 2503}\n" for i in range(2503))
    tab = TableTab(write(tmp_path / "shuffled.csv", lines))
    qtbot.addWidget(tab)
    tab.show()
    original = [tab.model.cell(r, 0) for r in range(5)]
    tab.sort_by(0)
    assert wait_for(lambda: tab.model.sort_state == (0, False))
    assert tab.model.rowCount() == 2503
    assert [tab.model.cell(r, 0) for r in range(3)] == ["0", "1", "2"]
    tab.sort_by(0)
    assert [tab.model.cell(r, 0) for r in range(2)] == ["2502", "2501"]
    tab.sort_by(0)
    assert tab.model.sort_state is None
    assert [tab.model.cell(r, 0) for r in range(5)] == original
    tab.cleanup()


def test_find_goes_past_the_loaded_rows_and_back(qtbot, tmp_path):
    lines = "id,name\n" + "".join(f"{i},row{i}\n" for i in range(CHUNK_ROWS * 2)) + "last,needle here\n"
    tab = TableTab(write(tmp_path / "big.csv", lines))
    qtbot.addWidget(tab)
    tab.show()
    QTest.keyClick(tab, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    tab.show_find()
    assert tab.find_bar.isVisible()
    tab.txt_find.setText("NEEDLE")
    QTest.keyClick(tab.txt_find, Qt.Key.Key_Return)
    assert wait_for(lambda: tab.view.currentIndex().row() == CHUNK_ROWS * 2)
    assert tab.view.currentIndex().column() == 1
    assert "needle here" in tab.status.text() and tab.status.text().startswith("B")
    tab.txt_find.setText("row5")
    QTest.keyClick(tab.txt_find, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert wait_for(lambda: tab.model.cell(tab.view.currentIndex().row(), 1).startswith("row5"))
    QTest.keyClick(tab.txt_find, Qt.Key.Key_Escape)
    assert not tab.find_bar.isVisible()
    tab.cleanup()


def test_ctrl_c_copies_cells_as_tab_separated_text(qtbot, tmp_path):
    tab = TableTab(write(tmp_path / "grid.csv", "a,b,c\n1,2,3\n4,5,6\n"))
    qtbot.addWidget(tab)
    tab.show()
    tab.view.selectAll()
    tab.copy_cells()
    assert QApplication.clipboard().text() == "1\t2\t3\n4\t5\t6"
    tab.cleanup()


def test_rows_of_different_lengths_and_no_header(qtbot, tmp_path):
    tab = TableTab(write(tmp_path / "ragged.csv", "1,2\n3,4,5,6\n7\n"))
    qtbot.addWidget(tab)
    assert tab.model.columnCount() == 4
    assert tab.model.headerData(3, Qt.Orientation.Horizontal) == "D"
    assert cells(tab, 2) == ["7", "", "", ""]
    tab.cleanup()


def test_an_empty_or_unreadable_file_says_so(qtbot, tmp_path):
    empty = TableTab(write(tmp_path / "empty.csv", ""))
    qtbot.addWidget(empty)
    assert empty.model.rowCount() == 0
    missing = TableTab(str(tmp_path / "gone.csv"))
    qtbot.addWidget(missing)
    assert "can't be read" in missing.status.text()


def test_a_sheet_with_formulas_shows_them_in_the_status_line(qtbot, tmp_path):
    sheet = TableSheet("Budget", iter([["10", "20", "30"]]), ["a", "b", "total"], first_row=2)
    sheet.formulas[(2, 2)] = "=A2+B2"
    other = TableSheet("Notes", iter([["hello"]]))
    tab = TableTab(str(tmp_path / "book.xlsx"), sheets=[sheet, other])
    qtbot.addWidget(tab)
    assert tab.sheet_tabs.count() == 2 and not tab.btn_text.isVisibleTo(tab)
    tab.view.setCurrentIndex(tab.model.index(0, 2))
    assert tab.status.text().startswith("C2") and "=A2+B2" in tab.status.text()
    tab.sheet_tabs.setCurrentIndex(1)
    assert tab.model.cell(0, 0) == "hello"
    tab.cleanup()


def test_the_window_opens_tables_and_open_as_text_swaps_in_the_reader(qtbot, tmp_path):
    path = write(tmp_path / "sales.csv", "region,total\nNorth,10\n")
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    table = window._open_document(path, preview=False)
    assert isinstance(table, TableTab)
    idx = window.tabs_main.indexOf(table)
    reader = window.open_as_text(table)
    assert isinstance(reader, ReaderTab) and reader.text_document
    assert window.tabs_main.indexOf(reader) == idx
    assert window.tabs_main.indexOf(table) == -1
    reader.cleanup()
    window._on_file_single_clicked(write(tmp_path / "other.tsv", "a\tb\n"))
    preview = window._live_preview_tab()
    assert isinstance(preview, TableTab) and preview.is_preview


def test_a_running_riemann_accepts_tables_from_another_launch(tmp_path):
    from riemann.window.instance import accepts_handoff

    assert accepts_handoff(write(tmp_path / "data.tsv", "a\tb\n"))
    assert os.path.exists(tmp_path / "data.tsv")
