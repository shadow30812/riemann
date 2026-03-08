from unittest.mock import MagicMock, patch

import pytest
from riemann.ui.reader.workers import (
    InferenceThread,
    InstallerThread,
    LoaderThread,
    MetadataExtractionWorker,
    ModelDownloader,
    SignatureValidationWorker,
)


@pytest.fixture
def app(qtbot):
    return qtbot


@patch("urllib.request.urlretrieve")
@patch("zipfile.ZipFile")
@patch("os.remove")
@patch("os.makedirs")
def test_model_downloader_success(
    mock_makedirs, mock_remove, mock_zip, mock_urlretrieve, qtbot
):
    downloader = ModelDownloader("http://fake.url", "/fake/dest")
    with qtbot.waitSignal(downloader.finished, timeout=1000) as blocker:
        downloader.run()
    assert blocker.args == [True]
    mock_makedirs.assert_called_once_with("/fake/dest", exist_ok=True)


@patch("urllib.request.urlretrieve", side_effect=Exception("Network Error"))
def test_model_downloader_failure(mock_urlretrieve, qtbot):
    downloader = ModelDownloader("http://fake.url", "/fake/dest")
    with qtbot.waitSignal(downloader.finished, timeout=1000) as blocker:
        downloader.run()
    assert blocker.args == [False]


@patch("subprocess.check_call")
def test_installer_thread_success(mock_check_call, qtbot):
    installer = InstallerThread()
    with qtbot.waitSignal(installer.finished_install, timeout=1000):
        installer.run()
    mock_check_call.assert_called_once()


@patch("subprocess.check_call", side_effect=Exception("Install Error"))
def test_installer_thread_failure(mock_check_call, qtbot):
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
@patch("riemann.ui.reader.workers.PdfFileReader")
@patch("riemann.ui.reader.workers.ValidationContext")
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
