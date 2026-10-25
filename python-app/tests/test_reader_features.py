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


def test_page_widget_pixmap_and_text_replace_each_other(qapp):
    from PySide6.QtGui import QPixmap

    w = PageWidget()
    pix = QPixmap(40, 50)
    w.setPixmap(pix)
    assert w.pixmap().size() == pix.size() and w.text() == ""
    w.setText("Page 3")
    assert w.pixmap().isNull() and w.text() == "Page 3"
    w.setPixmap(pix)
    w.clear()
    assert w.pixmap().isNull()
    w.deleteLater()


def test_overlays_leave_shared_pixmap_alone_when_idle(reader):
    from PySide6.QtGui import QPixmap

    img = QImage(60, 80, QImage.Format.Format_RGB32)
    pix = QPixmap.fromImage(img)
    key = pix.cacheKey()
    reader.search_result = None
    reader.annotations = {}
    reader._render_overlays(0, pix, 1.0, 60, 80)
    assert pix.cacheKey() == key  # no painter, so no copy of the page

    reader.search_result = (0, [(1.0, 20.0, 10.0, 5.0)])
    reader._render_overlays(0, pix, 1.0, 60, 80)
    assert pix.cacheKey() != key


def _wait_for(qapp, cond, seconds=2.0):
    import time

    deadline = time.time() + seconds
    while not cond() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    return cond()


@pytest.mark.parametrize("has_core", [True, False])
def test_external_comments_load_off_the_ui_thread(reader, qapp, has_core):
    import threading

    seen = {}

    def fake_read(_source):
        seen["thread"] = threading.current_thread() is not threading.main_thread()
        return {2: [{"page": 2, "contents": "hi"}]}

    if not has_core:
        reader.current_doc = MagicMock(spec=["page_count"])
    target = "read_comments_from_core" if has_core else "read_external_comments"
    reader.current_path = "/fake/doc.pdf"
    with patch(f"riemann.ui.reader.mixins.annotations.{target}", fake_read):
        reader._load_external_comments_async("/fake/doc.pdf")
        assert reader.external_comments == {}
        assert _wait_for(qapp, lambda: 2 in reader.external_comments)
    assert seen["thread"] is True


def test_read_comments_from_core_maps_rects_and_dates():
    from riemann.ui.reader.mixins.annotations import read_comments_from_core

    doc = MagicMock()
    doc.page_count = 2
    doc.get_comments.side_effect = lambda i: (
        ((0.0, 0.0, 600.0, 800.0), [("Text", "Note", "Ann", "D:20260102030405", (60.0, 720.0, 120.0, 640.0))])
        if i == 1
        else ((0.0, 0.0, 600.0, 800.0), [])
    )
    out = read_comments_from_core(doc)
    assert list(out) == [1]
    c = out[1][0]
    assert (c["subtype"], c["contents"], c["author"]) == ("Text", "Note", "Ann")
    assert c["date"] == "2026-01-02 03:04:05"
    assert c["rel_rect"] == pytest.approx((0.1, 0.1, 0.2, 0.2))


def test_silent_scroll_moves_the_content_but_skips_the_scroll_handler(qapp):
    with patch("riemann.ui.reader.tab.QSettings"):
        tab = ReaderTab()
    tab.resize(800, 600)
    tab.show()
    tab.stack.setCurrentWidget(tab.scroll)
    tab.scroll_content.setMinimumHeight(5000)
    for _ in range(5):
        qapp.processEvents()
    assert tab.scroll.verticalScrollBar().maximum() > 1200
    seen_ignore = []
    tab.scroll.verticalScrollBar().valueChanged.connect(
        lambda v: seen_ignore.append(getattr(tab, "_ignore_scroll", False))
    )

    tab._set_scroll_value_silently(1200)

    assert tab.scroll.verticalScrollBar().value() == 1200
    assert tab.scroll_content.pos().y() == -1200  # the pages really moved
    assert seen_ignore == [True]
    assert getattr(tab, "_ignore_scroll", False) is False
    tab.deleteLater()
