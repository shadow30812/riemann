"""
Files Riemann has no viewer for open as bytes in a HexTab (core/binary.py,
ui/viewers/hex_tab.py). Programs are named by their signature or their
name, warned about, and never offered to another application; files
Riemann can't read say why.
"""

import os
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from riemann.core.binary import BLOCK_BYTES, hex_row, program_kind, text_row
from riemann.ui.components import populate_open_with_menu
from riemann.ui.viewers.hex_tab import CACHED_BLOCKS, HexTab


def write(path, data):
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize(
    "name, data, kind",
    [
        ("tool", b"\x7fELF\x02\x01\x01" + bytes(9), "Linux program"),
        ("setup.dat", b"MZ\x90\x00" + bytes(12), "Windows program"),
        ("app", b"\xcf\xfa\xed\xfe" + bytes(12), "Mac program"),
        ("Universal", b"\xca\xfe\xba\xbe" + bytes(12), "Mac or Java program"),
        ("module", b"\x00asm\x01\x00\x00\x00", "WebAssembly program"),
        ("link", b"L\x00\x00\x00\x01\x14\x02\x00" + bytes(8), "Windows shortcut"),
        ("run-me", b"#!/bin/sh\n\x00", "script"),
        ("INSTALL.EXE", bytes(16), "Windows program"),
        ("start.bat", bytes(16), "Windows script"),
        ("libfoo.so.1", bytes(16), "Linux library"),
        ("Tool.AppImage", bytes(16), "Linux program"),
    ],
)
def test_programs_are_known_by_signature_or_name(tmp_path, name, data, kind):
    assert program_kind(write(tmp_path / name, data)) == kind


def test_other_bytes_are_not_programs(tmp_path):
    assert program_kind(write(tmp_path / "blob.dat", b"\x00\x01\x02\x03" * 4)) == ""
    assert program_kind(write(tmp_path / "archive.zip", b"PK\x03\x04" + bytes(12))) == ""
    assert program_kind(str(tmp_path / "missing")) == ""


def test_rows_show_hex_and_printable_text():
    data = b"Riemann\x00\x01\xffABCDEF"
    assert hex_row(data) == "52 69 65 6D 61 6E 6E 00  01 FF 41 42 43 44 45 46"
    assert text_row(data) == "Riemann...ABCDEF"
    assert hex_row(b"ab") == "61 62"


def test_the_hex_view_reads_rows_a_block_at_a_time(qtbot, tmp_path):
    size = BLOCK_BYTES * (CACHED_BLOCKS + 3) + 5
    data = bytes(i % 251 for i in range(size))
    tab = HexTab(write(tmp_path / "big.dat", data))
    qtbot.addWidget(tab)
    model = tab.model
    assert model.rowCount() == size // 16 + 1
    last = model.rowCount() - 1
    assert model.data(model.index(last, 0)) == f"{last * 16:08X}"
    assert model.data(model.index(last, 1)) == hex_row(data[last * 16:])
    for row in range(0, model.rowCount(), BLOCK_BYTES // 16):
        assert model.row_bytes(row) == data[row * 16:row * 16 + 16]
    assert len(model._blocks) == CACHED_BLOCKS
    assert tab.banner_tone() == "neutral" and tab.btn_open_with is not None


def test_a_program_is_warned_about_and_never_opened_elsewhere(qtbot, tmp_path):
    path = write(tmp_path / "setup.exe", b"MZ" + bytes(62))
    tab = HexTab(path)
    qtbot.addWidget(tab)
    assert tab.banner_tone() == "warning"
    assert "never runs it" in tab.lbl_banner.text()
    assert tab.btn_open_with is None
    menu = QMenu()
    populate_open_with_menu(menu, path)
    assert [a.text() for a in menu.actions()] == ["Riemann doesn't start programs"]
    assert not menu.actions()[0].isEnabled()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_a_file_riemann_cannot_read_says_why(qtbot, tmp_path):
    path = write(tmp_path / "secret.bin", b"\x00\x01")
    os.chmod(path, 0)
    try:
        tab = HexTab(path)
        qtbot.addWidget(tab)
        assert tab.banner_tone() == "danger"
        assert "can't read secret.bin" in tab.lbl_banner.text()
        assert tab.model.rowCount() == 0 and tab.btn_open_with is None
    finally:
        os.chmod(path, 0o600)


def test_the_n_key_switches_the_bytes_between_dark_and_light(qtbot, tmp_path):
    tab = HexTab(write(tmp_path / "blob.dat", b"\x00\x01"), dark_mode=True)
    qtbot.addWidget(tab)
    tab.toggle_theme()
    assert tab.bytes_dark is False and tab.dark_mode is True
    QApplication.processEvents()
    assert tab.model.data(tab.model.index(0, 2), Qt.ItemDataRole.DisplayRole) == ".."


def test_f_goes_full_screen_although_the_table_takes_letters(qtbot, tmp_path):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    tab = HexTab(write(tmp_path / "blob.dat", b"\x00\x01"))
    qtbot.addWidget(tab)
    calls = []
    tab.toggle_reader_fullscreen = lambda: calls.append("f")
    QApplication.sendEvent(tab.view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier, "f"))
    assert calls == ["f"]
