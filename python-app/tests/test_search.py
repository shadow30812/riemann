from unittest.mock import MagicMock

import pytest
from PySide6.QtWebEngineCore import QWebEnginePage

from riemann.core.constants import ViewMode
from riemann.ui.reader.mixins.search import PageSearch


def _doc(texts):
    doc = MagicMock()
    doc.page_count = len(texts)
    doc.get_page_text.side_effect = lambda idx: texts[idx]
    doc.search_page.return_value = [(10, 20, 30, 40)]
    return doc


@pytest.fixture
def reader(qapp, qtbot):
    from riemann.ui.reader.tab import ReaderTab

    tab = ReaderTab()
    qtbot.addWidget(tab)
    tab.show()
    tab.toasts = []
    tab.show_toast = tab.toasts.append
    tab.update_view = MagicMock()
    tab.rebuild_layout = MagicMock()
    tab.ensure_visible = MagicMock()
    return tab


def test_page_search_finds_pages_in_order_and_wraps():
    doc = _doc(["target", "", "target again"])
    search = PageSearch(lambda: doc, dict)
    assert [idx for idx, _ in search.matches("Target", 1, 1)] == [2, 0]
    assert doc.search_page.call_args_list[0].args == (2, "Target")
    assert [idx for idx, _ in search.matches("target", 1, -1)] == [0, 2]


def test_page_search_reads_annotation_text_and_skips_unreadable_pages():
    doc = _doc(["plain", "plain", "plain"])

    def page_text(idx):
        if idx == 2:
            raise RuntimeError("unreadable page")
        return "plain"

    doc.get_page_text.side_effect = page_text
    notes = {
        "1": [{"type": "note", "text": "This is a hidden_note annotation"}],
        "2": [{"type": "text", "text": "hidden_note"}],
    }
    search = PageSearch(lambda: doc, lambda: notes)
    assert list(search.matches("hidden_note", 0, 1)) == [(1, [(10, 20, 30, 40)])]


def test_page_search_gives_no_boxes_when_they_cant_be_read():
    doc = _doc(["target"])
    doc.search_page.side_effect = RuntimeError
    assert list(PageSearch(lambda: doc, dict).matches("target", 0, 1)) == [(0, None)]


def test_toggle_search_bar(reader):
    reader.toggle_search_bar()
    assert reader.search_bar.isVisible()
    assert reader.btn_search.isChecked()

    reader.search_result = (0, [])
    reader.rendered_pages.add(0)
    reader.toggle_search_bar()
    assert not reader.search_bar.isVisible()
    assert reader.search_result is None
    assert len(reader.rendered_pages) == 0


def test_find_next_and_prev_in_reflow_mode(reader):
    reader.view_mode = ViewMode.REFLOW
    reader.web = MagicMock()
    reader.txt_search.setText("query")
    reader.find_next()
    reader.web.findText.assert_called_with("query")
    reader.find_prev()
    reader.web.findText.assert_called_with("query", QWebEnginePage.FindFlag.FindBackward)


def test_find_text_image_mode_success(reader):
    reader.current_doc = _doc(["", "target found here", ""])
    reader.txt_search.setText("target")
    reader._find_text(1)
    assert reader.current_page_index == 1
    assert reader.search_result == (1, [(10, 20, 30, 40)])


def test_find_text_in_annotations(reader):
    reader.current_doc = _doc(["normal text", "normal text"])
    reader.current_doc.search_page.return_value = []
    reader.annotations = {"1": [{"type": "note", "text": "This is a hidden_note annotation"}]}
    reader.txt_search.setText("hidden_note")
    reader._find_text(1)
    assert reader.current_page_index == 1
    assert reader.search_result == (1, [])


def test_a_match_that_fails_to_show_moves_on_to_the_next(reader):
    reader.current_doc = _doc(["", "target", "target"])
    reader.txt_search.setText("target")
    reader.ensure_visible.side_effect = [RuntimeError, None]
    reader._find_text(1)
    assert reader.current_page_index == 2


def test_no_match_says_so(reader):
    reader.current_doc = _doc(["nothing", "here"])
    reader.txt_search.setText("absent")
    reader._find_text(1)
    assert reader.toasts == ["No matches for 'absent'"]
