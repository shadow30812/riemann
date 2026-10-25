import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QApplication
from riemann.core.constants import ViewMode
from riemann.ui.reader.render_worker import PageImageCache
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.reader.widgets import PageWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def _line(text, y, x0=10, char_w=8, h=12, start_seg=0):
    return [(ch, QRect(x0 + i * char_w, y, char_w, h), start_seg) for i, ch in enumerate(text)]


@pytest.fixture
def reader(qapp):
    with patch("riemann.ui.reader.tab.QSettings"):
        tab = ReaderTab()
    tab.current_doc = MagicMock()
    tab.current_doc.page_count = 1
    tab.view_mode = ViewMode.IMAGE
    tab.page_widgets = {0: PageWidget()}
    chars = _line("Hello world", 10) + _line("Second line", 40)
    tab._get_page_chars = lambda page_idx: chars
    tab._sel_page, tab._sel_anchor, tab._sel_focus = 0, 0, 0
    yield tab
    tab.deleteLater()


def _press(tab, key, mods=Qt.KeyboardModifier.ShiftModifier):
    return tab._handle_selection_key(QKeyEvent(QEvent.Type.KeyPress, key, mods))


def test_parse_internal_link():
    assert ReaderTab._parse_internal_link("#page=5") == (4, None)
    assert ReaderTab._parse_internal_link("#page=12&top=310.50") == (11, 310.5)
    assert ReaderTab._parse_internal_link("#nonsense") is None


def test_shift_right_selects_one_character_at_a_time(reader):
    for _ in range(5):
        assert _press(reader, Qt.Key.Key_Right)
    assert reader.current_selected_text == "Hello"
    _press(reader, Qt.Key.Key_Left)
    assert reader.current_selected_text == "Hell"


def test_ctrl_shift_right_jumps_by_word(reader):
    mods = Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
    _press(reader, Qt.Key.Key_Right, mods)
    assert reader.current_selected_text == "Hello"
    _press(reader, Qt.Key.Key_Right, mods)
    assert reader.current_selected_text == "Hello world"


def test_shift_down_and_end_extend_by_line(reader):
    _press(reader, Qt.Key.Key_Down)
    assert reader.current_selected_text == "Hello world"
    _press(reader, Qt.Key.Key_End)
    assert reader.current_selected_text == "Hello world\nSecond line"


def test_shift_end_then_home_stays_on_the_same_line(reader):
    _press(reader, Qt.Key.Key_End)
    assert reader.current_selected_text == "Hello world"
    _press(reader, Qt.Key.Key_Home)
    assert reader.current_selected_text == ""
    reader._sel_anchor = reader._sel_focus = 13  # inside "Second"
    _press(reader, Qt.Key.Key_Home)
    assert reader.current_selected_text == "Se"


def test_plain_arrows_are_not_selection_keys(reader):
    assert not _press(reader, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)


def test_char_range_text_breaks_lines():
    chars = _line("ab", 0) + _line("cd", 30)
    rects, text = ReaderTab._rects_for_char_range(chars, 0, 3)
    assert text == "ab\ncd"
    assert len(rects) == 2


def test_page_image_cache_evicts_oldest_over_budget():
    img = QImage(100, 100, QImage.Format.Format_ARGB32)
    cache = PageImageCache(budget_bytes=img.sizeInBytes() * 2)
    owner = ("tab", 1)
    cache.put((owner, 0, 1.0, 0), img)
    cache.put((owner, 1, 1.0, 0), img)
    cache.get((owner, 0, 1.0, 0))  # page 0 becomes most recently used
    cache.put((owner, 2, 1.0, 0), img)

    assert cache.get((owner, 1, 1.0, 0)) is None
    assert cache.get((owner, 0, 1.0, 0)) is not None
    assert cache.find_any(owner, 2, 0) is not None
    cache.drop_owner(owner)
    assert cache.used == 0
