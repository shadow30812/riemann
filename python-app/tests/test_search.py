from unittest.mock import MagicMock

import pytest
import riemann.ui.reader.mixins.search
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from riemann.core.constants import ViewMode
from riemann.ui.reader.mixins.search import SearchMixin

riemann.ui.reader.mixins.search.QWebEngineView.FindFlag = QWebEnginePage.FindFlag


class DummySearchReader(SearchMixin):
    def __init__(self):
        self.view_mode = ViewMode.IMAGE
        self.current_page_index = 0
        self.current_doc = MagicMock()
        self.continuous_scroll = False
        self.rendered_pages = set([0])

        self.search_bar = MagicMock()
        self.btn_search = MagicMock()
        self.txt_search = MagicMock()
        self.web = MagicMock()

    def update_view(self):
        pass

    def rebuild_layout(self):
        pass

    def ensure_visible(self, idx):
        pass

    def show_toast(self, msg):
        self.last_toast = msg


@pytest.fixture
def reader():
    return DummySearchReader()


def test_toggle_search_bar(reader):
    reader.search_bar.isVisible.return_value = False
    reader.toggle_search_bar()

    reader.search_bar.setVisible.assert_called_with(True)
    reader.btn_search.setChecked.assert_called_with(True)
    reader.txt_search.setFocus.assert_called_once()

    reader.search_bar.isVisible.return_value = True
    reader.toggle_search_bar()
    assert reader.search_result is None
    assert len(reader.rendered_pages) == 0


def test_find_next_reflow_mode(reader):
    reader.view_mode = ViewMode.REFLOW
    reader.txt_search.text.return_value = "query"
    reader.find_next()
    reader.web.findText.assert_called_with("query")


def test_find_prev_reflow_mode(reader):
    reader.view_mode = ViewMode.REFLOW
    reader.txt_search.text.return_value = "query"
    reader.find_prev()
    reader.web.findText.assert_called_with(
        "query", QWebEngineView.FindFlag.FindBackward
    )


def test_find_text_image_mode_success(reader):
    reader.txt_search.text.return_value = "target"
    reader.current_doc.page_count = 3

    def mock_get_text(page_idx):
        if page_idx == 1:
            return "target found here"
        return ""

    reader.current_doc.get_page_text.side_effect = mock_get_text
    reader.current_doc.search_page.return_value = [(10, 20, 30, 40)]
    reader._find_text(1)

    assert reader.current_page_index == 1
    assert reader.search_result == (1, [(10, 20, 30, 40)])


def test_find_text_in_annotations(reader):
    reader.txt_search.text.return_value = "hidden_note"
    reader.current_doc.page_count = 2
    reader.current_doc.get_page_text.return_value = "normal text"
    reader.current_doc.search_page.return_value = []

    reader.annotations = {
        "1": [{"type": "note", "text": "This is a hidden_note annotation"}]
    }

    reader._find_text(1)

    assert reader.current_page_index == 1
    assert reader.search_result == (1, [])
