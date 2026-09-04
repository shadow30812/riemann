import json
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from riemann.ui.reader.mixins.ai import AiMixin


class DummyAiReader(AiMixin):
    def __init__(self):
        self.is_snipping = False
        self.is_annotating = False
        self.current_doc = MagicMock()
        self.current_page_index = 0
        self.latex_model = None
        self._pending_snip_image = None

        self.btn_snip = MagicMock()
        self.btn_annotate = MagicMock()
        self.snip_band = MagicMock()
        self.ai_search_bar = MagicMock()
        self.btn_ai_search = MagicMock()
        self.txt_ai_search = MagicMock()

    def setCursor(self, cursor):
        self.cursor = cursor

    def width(self):
        return 800

    def height(self):
        return 600

    def show_toast(self, msg):
        self.last_toast = msg

    def rebuild_layout(self):
        pass

    def update_view(self):
        pass

    def ensure_visible(self, idx):
        pass

    def toggle_search_bar(self):
        pass


@pytest.fixture
def reader():
    return DummyAiReader()


def test_toggle_snip_mode(reader):
    reader.toggle_snip_mode(True)
    assert reader.is_snipping is True
    assert reader.is_annotating is False
    assert reader.cursor == Qt.CursorShape.CrossCursor
    reader.btn_snip.setChecked.assert_called_with(True)

    reader.toggle_snip_mode(False)
    assert reader.is_snipping is False
    assert reader.cursor == Qt.CursorShape.ArrowCursor
    reader.snip_band.hide.assert_called_once()


@patch("riemann.ui.reader.mixins.ai.QApplication")
@patch("riemann.ui.reader.mixins.ai.QInputDialog")
def test_perform_ocr_current_page(mock_input, mock_qapp, reader):
    reader.current_doc.ocr_page.return_value = "Extracted Text"
    reader.perform_ocr_current_page()

    reader.current_doc.ocr_page.assert_called_with(0, 2.0)
    mock_input.getMultiLineText.assert_called_with(
        reader, "OCR Result", "Text:", "Extracted Text"
    )


@patch("riemann.ui.reader.mixins.ai.LoaderThread")
@patch("riemann.ui.reader.mixins.ai.QProgressDialog")
def test_run_latex_inference_no_model(mock_progress, mock_loader_cls, reader):
    mock_img = MagicMock()
    reader._setup_external_env = MagicMock()

    reader.run_latex_inference(mock_img)

    assert reader._pending_snip_image == mock_img
    assert reader.is_snipping is False
    mock_progress.assert_called_once()
    mock_loader_cls.return_value.start.assert_called_once()


@patch("riemann.ui.reader.mixins.ai.InferenceThread")
@patch("riemann.ui.reader.mixins.ai.QProgressDialog")
def test_run_latex_inference_with_model(mock_progress, mock_inference_cls, reader):
    reader.latex_model = MagicMock()
    mock_img = MagicMock()
    reader._setup_external_env = MagicMock()

    reader.run_latex_inference(mock_img)

    mock_inference_cls.assert_called_with(reader.latex_model, mock_img)
    mock_inference_cls.return_value.start.assert_called_once()


def test_toggle_ai_search_bar(reader):
    reader.ai_search_bar.isVisible.return_value = False
    reader.search_bar = MagicMock()
    reader.search_bar.isVisible.return_value = True

    reader.toggle_search_bar = MagicMock()
    reader.toggle_ai_search_bar()

    reader.ai_search_bar.setVisible.assert_called_with(True)
    reader.btn_ai_search.setChecked.assert_called_with(True)
    reader.txt_ai_search.setFocus.assert_called_once()
    reader.toggle_search_bar.assert_called_once()


def test_ws_message_handling(reader):
    reader._on_ws_message(json.dumps({"status": "success"}))
    assert "ready" in reader.last_toast

    mock_results = json.dumps(
        {
            "status": "results",
            "data": [{"page": 2, "text": "AI generated formula", "score": 0.95}],
        }
    )

    reader.rendered_pages = set()
    reader._on_ws_message(mock_results)

    assert len(reader.ai_results) == 1
    assert reader.current_page_index == 1
    assert reader.ai_result_idx == 0
    assert "95%" in reader.last_toast
