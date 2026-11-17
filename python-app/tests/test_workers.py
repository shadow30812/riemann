import hashlib
import http.server
import io
import threading
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from riemann.ui.reader.workers import (
    InferenceThread,
    InstallerThread,
    LoaderThread,
    MetadataExtractionWorker,
    ModelDownloader,
    SignatureValidationWorker,
    tag_request,
)


@pytest.fixture
def app(qtbot):
    return qtbot


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def serve():
    servers = []

    def start(payload):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/pack.zip"

    yield start
    for server in servers:
        server.shutdown()


def _run(downloader):
    results = []
    downloader.finished.connect(results.append)
    downloader.run()
    return results


def test_model_downloader_extracts_a_matching_pack(serve, tmp_path):
    payload = _zip_bytes({"pix2tex/__init__.py": "x = 1\n"})
    url = serve(payload)
    d = ModelDownloader(url, str(tmp_path), hashlib.sha256(payload).hexdigest())
    assert _run(d) == [True]
    assert (tmp_path / "pix2tex" / "__init__.py").read_text() == "x = 1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pix2tex"]


def test_model_downloader_rejects_a_tampered_pack(serve, tmp_path):
    payload = _zip_bytes({"pix2tex/__init__.py": "import os  # evil\n"})
    d = ModelDownloader(serve(payload), str(tmp_path), "0" * 64)
    assert _run(d) == [False]
    assert "checksum" in d.error
    assert list(tmp_path.iterdir()) == []


def test_model_downloader_refuses_paths_outside_the_folder(serve, tmp_path):
    payload = _zip_bytes({"../escaped.py": "boom"})
    dest = tmp_path / "pack"
    d = ModelDownloader(serve(payload), str(dest), hashlib.sha256(payload).hexdigest())
    assert _run(d) == [False]
    assert not (tmp_path / "escaped.py").exists()


def test_model_downloader_can_be_cancelled(serve, tmp_path):
    payload = _zip_bytes({"a.txt": "a"})
    d = ModelDownloader(serve(payload), str(tmp_path), hashlib.sha256(payload).hexdigest())
    d.cancel()
    assert _run(d) == [False]
    assert d.error == "Download cancelled."
    assert list(tmp_path.iterdir()) == []


def test_model_downloader_reports_network_errors(tmp_path):
    d = ModelDownloader("http://127.0.0.1:9/none.zip", str(tmp_path), "0" * 64)
    assert _run(d) == [False]
    assert d.error


@patch("riemann.ui.reader.workers.setup_and_get_venv", return_value="/venv/python")
@patch("subprocess.check_call")
def test_installer_pins_versions(mock_check_call, mock_venv):
    InstallerThread().run()
    args = mock_check_call.call_args[0][0]
    assert "--require-hashes" in args and "--no-deps" in args
    assert args[-1].endswith("latex-ocr.txt")


def test_tag_request_sends_the_users_labels(qapp, monkeypatch):
    monkeypatch.setattr("riemann.ui.reader.workers.string_list", lambda key: ["Topology", "Number Theory"])
    assert tag_request("x" * 2000) == {"text_chunk": "x" * 1500, "tags": ["Topology", "Number Theory"]}
    monkeypatch.setattr("riemann.ui.reader.workers.string_list", lambda key: [])
    assert tag_request("abc") == {"text_chunk": "abc"}


@patch("riemann.ui.reader.workers.setup_and_get_venv", return_value="/venv/python")
@patch("subprocess.check_call")
def test_installer_thread_success(mock_check_call, mock_venv, qtbot):
    installer = InstallerThread()
    with qtbot.waitSignal(installer.finished_install, timeout=1000):
        installer.run()
    mock_check_call.assert_called_once()


@patch("riemann.ui.reader.workers.setup_and_get_venv", return_value="/venv/python")
@patch("subprocess.check_call", side_effect=Exception("Install Error"))
def test_installer_thread_failure(mock_check_call, mock_venv, qtbot):
    installer = InstallerThread()
    with qtbot.waitSignal(installer.install_error, timeout=1000) as blocker:
        installer.run()
    assert "Install Error" in blocker.args[0]


@patch.dict("sys.modules", {"pix2tex.cli": MagicMock()})
def test_loader_thread_success(qtbot):
    loader = LoaderThread()
    with qtbot.waitSignal(loader.finished_loading, timeout=1000):
        loader.run()


@patch.dict("sys.modules", {"pix2tex.cli": None})
def test_loader_thread_import_error(qtbot):
    loader = LoaderThread()
    with qtbot.waitSignal(loader.error_occurred, timeout=1000) as blocker:
        loader.run()
    assert blocker.args == ["Module 'pix2tex' not found."]


def test_inference_thread_success(qtbot):
    mock_model = MagicMock(return_value="mocked_code")
    inference = InferenceThread(mock_model, "fake_image")
    with qtbot.waitSignal(inference.finished_inference, timeout=1000) as blocker:
        inference.run()
    assert blocker.args == ["mocked_code"]


def test_inference_thread_failure(qtbot):
    mock_model = MagicMock(side_effect=Exception("Inference failed"))
    inference = InferenceThread(mock_model, "fake_image")
    with qtbot.waitSignal(inference.error_occurred, timeout=1000) as blocker:
        inference.run()
    assert blocker.args == ["Inference Error: Inference failed"]


@patch("builtins.open")
@patch("pyhanko.pdf_utils.reader.PdfFileReader")
@patch("pyhanko_certvalidator.ValidationContext")
def test_signature_validation_worker_no_sigs(mock_vc, mock_reader, mock_open, qtbot):
    mock_instance = MagicMock()
    mock_instance.embedded_signatures = []
    mock_reader.return_value = mock_instance
    worker = SignatureValidationWorker("fake.pdf", [])
    with qtbot.waitSignal(worker.finished_validation, timeout=1000) as blocker:
        worker.run()
    assert blocker.args[0] == "NONE"


@patch("requests.Session.get")
def test_metadata_extraction_worker_doi(mock_get, qtbot):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {
            "title": ["Fake Title"],
            "author": [{"given": "John", "family": "Doe"}],
        }
    }
    mock_get.return_value = mock_response
    worker = MetadataExtractionWorker("Text with 10.1234/fake.doi inside.")
    with qtbot.waitSignal(worker.finished_extraction, timeout=1000) as blocker:
        worker.run()
    assert blocker.args[0]["doi"] == "10.1234/fake.doi"
    assert blocker.args[0]["title"] == "Fake Title"
    assert blocker.args[0]["authors"] == "John Doe"
