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
        self.current_path = None
        self.current_page_index = 0
        self.latex_model = None
        self._pending_snip_image = None

        self.btn_snip = MagicMock()
        self.btn_annotate = MagicMock()
        self.snip_band = MagicMock()
        self.ai_search_bar = MagicMock()
        self.btn_ai_search = MagicMock()
        self.txt_ai_search = MagicMock()
        self.navigate_to = MagicMock()

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
def test_perform_ocr_current_page(mock_input, mock_qapp, qapp, reader):
    reader.current_doc.ocr_page.return_value = "Extracted Text"
    reader.perform_ocr_current_page()
    reader._ocr_thread.wait(5000)
    qapp.processEvents()

    assert reader.current_doc.ocr_page.call_args[0][:2] == (0, 2.0)
    mock_input.getMultiLineText.assert_called_with(
        reader, "OCR Result", "Text of page 1:", "Extracted Text"
    )


@patch("riemann.ui.reader.mixins.ai.QApplication")
@patch("riemann.ui.reader.mixins.ai.QMessageBox")
def test_ocr_failure_is_shown(mock_box, mock_qapp, qapp, reader):
    reader.current_doc.ocr_page.side_effect = RuntimeError("Tesseract process failed to start")
    reader.perform_ocr_current_page()
    reader._ocr_thread.wait(5000)
    qapp.processEvents()

    mock_box.warning.assert_called_once()
    assert "Tesseract" in mock_box.warning.call_args.args[2]
    mock_qapp.restoreOverrideCursor.assert_called_once()


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


def test_ai_reply_handling(reader):
    reader.current_path = "/docs/a.pdf"
    reader._on_ai_reply("index", "/docs/a.pdf", 200, {"status": "success"})
    assert "ready" in reader.last_toast

    reader.rendered_pages = set()
    data = [{"page": 2, "text": "AI generated formula", "score": 0.95}]
    reader._on_ai_reply("search", "/docs/a.pdf", 200, data)

    assert len(reader.ai_results) == 1
    assert reader.current_page_index == 1
    assert reader.ai_result_idx == 0
    assert "95%" in reader.last_toast


def test_ai_ignores_replies_about_other_documents(reader):
    reader.current_path = "/docs/a.pdf"
    reader.rendered_pages = set()
    reader._on_ai_reply("search", "/docs/b.pdf", 200, [{"page": 3, "text": "other", "score": 0.9}])
    assert reader.ai_results is None


def test_ai_error_forgets_index(reader):
    reader.current_path = "/docs/a.pdf"
    reader._ai_indexed_path = "/docs/a.pdf"
    reader._on_ai_reply("search", "/docs/a.pdf", 400, {"detail": "No PDF is currently indexed."})
    assert reader._ai_indexed_path is None
    assert "No PDF" in reader.last_toast


@patch("riemann.ui.reader.mixins.ai.QNetworkAccessManager")
@patch("riemann.ui.reader.mixins.ai.ai_engine")
def test_search_indexes_first_and_sends_one_request_at_a_time(mock_engine, mock_net_cls, reader):
    mock_engine.ensure_started.return_value = (5555, "tok")
    net = mock_net_cls.return_value
    reader.current_path = "/docs/a.pdf"

    reader.ai_search("entropy")

    assert net.post.call_count == 1
    request, body = net.post.call_args.args
    assert request.url().toString() == "http://127.0.0.1:5555/index"
    assert bytes(request.rawHeader("X-Riemann-Token")) == b"tok"
    assert json.loads(body)["pdf_path"] == "/docs/a.pdf"
    assert [q[0] for q in reader._ai_queue] == ["index", "search"]


@patch("riemann.ui.reader.mixins.ai.ai_engine")
def test_closing_a_tab_leaves_the_shared_engine_running(mock_engine, reader):
    reply = MagicMock()
    reader._ai_reply = reply
    reader._ai_queue = [("search", {}, "/docs/a.pdf")]
    reader._kill_ai_engine()
    mock_engine.stop.assert_not_called()
    reply.abort.assert_called_once()
    assert reader._ai_queue == [] and reader._ai_reply is None


@patch("riemann.core.ai_engine.subprocess.Popen")
@patch("riemann.core.ai_engine._command", return_value=(["engine"], "/tmp"))
def test_engine_starts_once_with_port_and_token(mock_command, mock_popen, tmp_path, monkeypatch):
    from riemann.core import ai_engine

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(ai_engine, "_process", None)
    mock_popen.return_value.poll.return_value = None

    port, token = ai_engine.ensure_started()
    assert ai_engine.ensure_started() == (port, token)
    assert mock_popen.call_count == 1
    env = mock_popen.call_args.kwargs["env"]
    assert env["RIEMANN_AI_PORT"] == str(port)
    assert env["RIEMANN_AI_TOKEN"] == token and len(token) >= 24
    assert ai_engine.connection() == (port, token)

    monkeypatch.setattr(ai_engine, "_process", None)
    ai_engine.stop()


def test_closing_tab_during_ocr_keeps_thread_alive(qapp, reader):
    import threading

    from riemann.ui.reader.mixins import ai as ai_mod

    gate = threading.Event()
    reader.current_doc.ocr_page.side_effect = lambda *a: (gate.wait(5), "text")[1]
    with patch("riemann.ui.reader.mixins.ai.QApplication"):
        reader.perform_ocr_current_page()
        thread = reader._ocr_thread
        reader._kill_ai_engine()
    assert thread in ai_mod._ORPHANED_THREADS
    gate.set()
    thread.wait(5000)
    qapp.processEvents()
    assert thread not in ai_mod._ORPHANED_THREADS


class _Reply:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def error(self):
        from PySide6.QtNetwork import QNetworkReply

        return QNetworkReply.NetworkError.NoError

    def attribute(self, _):
        return self.status

    def readAll(self):
        class _Data:
            def __init__(self, b):
                self.b = b

            def data(self):
                return self.b

        return _Data(self.body)

    def deleteLater(self):
        pass


def test_ai_gives_up_when_the_model_never_finishes_loading(qapp, reader, monkeypatch):
    from riemann.ui.reader.mixins import ai

    monkeypatch.setattr(ai, "AI_LOADING_ATTEMPTS", 3)
    retries = []
    monkeypatch.setattr(reader, "_ai_retry_later", lambda: retries.append(1), raising=False)
    reader.current_path = "/papers/a.pdf"
    reader._ai_queue = [("index", {"pdf_path": "/papers/a.pdf"}, "/papers/a.pdf")]
    body = json.dumps({"detail": "loading: downloading the search model (40% of 86 MB)"}).encode()
    for _ in range(4):
        reply = _Reply(503, body)
        reader._ai_reply = reply
        reader._on_ai_reply_finished(reply)
    assert len(retries) == 3
    assert reader._ai_queue == []
    assert "did not finish loading" in reader.last_toast
