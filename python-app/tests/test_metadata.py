import os
from unittest.mock import MagicMock, patch

import pytest
from riemann.ui.reader.mixins.metadata import MetadataMixin


class DummyMetadataReader(MetadataMixin):
    def __init__(self):
        self.current_path = "/fake/docs/doc.pdf"
        self.current_doc = MagicMock()
        self.document_metadata = {}
        self.settings = MagicMock()

        self._window = MagicMock()
        self._window.library_manager = MagicMock()

    def window(self):
        return self._window

    def show_toast(self, msg):
        self.last_toast = msg


@pytest.fixture
def reader():
    return DummyMetadataReader()


def test_extract_metadata_from_cache(reader):
    reader.window().library_manager.get_metadata.return_value = {
        "title": "Cached Title",
        "year": "2023",
    }
    reader.extract_document_metadata()

    assert reader.document_metadata["title"] == "Cached Title"
    reader.current_doc.get_page_text.assert_not_called()


@patch("riemann.ui.reader.mixins.metadata.MetadataExtractionWorker")
def test_extract_metadata_spawns_worker(mock_worker_class, reader):
    reader.window().library_manager.get_metadata.return_value = None
    reader.current_doc.get_page_text.return_value = "Abstract: This is a test paper."

    mock_worker_instance = MagicMock()
    mock_worker_class.return_value = mock_worker_instance

    reader.extract_document_metadata()
    mock_worker_instance.start.assert_called_once()


@patch("os.rename")
@patch("os.path.exists", return_value=False)
def test_rename_current_pdf(mock_exists, mock_rename, reader):
    reader.document_metadata = {
        "title": "Quantum Comput:ing",
        "authors": "Doe, John, Smith, A.",
        "year": "2024",
    }

    reader.rename_current_pdf()

    expected_new_name = "[2024] - Doe - Quantum Computing.pdf"
    expected_new_path = os.path.join("/fake/docs", expected_new_name)

    mock_rename.assert_called_once_with("/fake/docs/doc.pdf", expected_new_path)
    assert reader.current_path == expected_new_path
    reader.settings.setValue.assert_called_with("lastFile", expected_new_path)
    reader.window().library_manager.save_metadata.assert_called_with(
        expected_new_path, reader.document_metadata
    )


@patch("PySide6.QtWidgets.QApplication.clipboard")
def test_copy_citation(mock_clipboard, reader):
    mock_clip_instance = MagicMock()
    mock_clipboard.return_value = mock_clip_instance

    reader.document_metadata = {"bibtex": "@article{doe2024, title={Quantum}}"}
    reader.copy_citation()

    mock_clip_instance.setText.assert_called_once_with(
        "@article{doe2024, title={Quantum}}"
    )
    assert "BibTeX copied" in reader.last_toast
