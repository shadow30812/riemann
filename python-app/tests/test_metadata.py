import os
from unittest.mock import MagicMock, patch

import pytest

from riemann.ui.reader.mixins.metadata import MetadataMixin


class DummyMetadataReader(MetadataMixin):
    def __init__(self):
        self.current_path = "/fake/docs/doc.pdf"
        self.converted_pdf = None
        self.current_doc = MagicMock()
        self.document_metadata = {}
        self._get_annotation_path = MagicMock()
        self.notes_panel = None
        self._update_tab_title = MagicMock()
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


def test_rename_moves_annotations_and_retitles_tab(tmp_path, reader, monkeypatch):
    from riemann.core import annotation_store

    store = annotation_store.AnnotationStore(str(tmp_path / "store"))
    monkeypatch.setattr(annotation_store, "_shared", store)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    reader.current_path = str(pdf)
    reader._anno_json_cache = None
    before = store.json_path(str(pdf))
    with open(before, "w") as f:
        f.write('{"pages": {"0": [{"id": "a"}]}}')
    reader._update_tab_title = MagicMock()
    reader.document_metadata = {"title": "Deep\nLearning. ", "authors": "", "year": ""}

    reader.rename_current_pdf()

    new_name = "[UnknownYear] - UnknownAuthor - Deep Learning.pdf"
    assert reader.current_path == str(tmp_path / new_name)
    assert (tmp_path / new_name).exists()
    assert store.json_path(reader.current_path) == before
    reader._update_tab_title.assert_called_once_with(new_name)


def test_rename_reports_existing_target(tmp_path, reader):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    (tmp_path / "[2024] - Doe - T.pdf").write_bytes(b"")
    reader.current_path = str(pdf)
    reader.document_metadata = {"title": "T", "authors": "Doe", "year": "2024"}

    reader.rename_current_pdf()

    assert reader.current_path == str(pdf)
    assert "already exists" in reader.last_toast


def test_a_finished_and_deleted_worker_doesnt_break_the_next_extraction_or_closing(qapp, tmp_path, monkeypatch):
    """The worker deletes itself when done; the tab used to call isRunning on it at the next load and on close."""
    import sys
    import time

    from PySide6.QtCore import QCoreApplication, QEvent

    from riemann.core import annotation_store
    from riemann.ui.reader.tab import ReaderTab
    from riemann.ui.reader.workers import MetadataExtractionWorker

    sys.path.insert(0, os.path.dirname(__file__))
    from text_fixtures import make_text_pdf

    monkeypatch.setattr(annotation_store, "_shared", annotation_store.AnnotationStore(str(tmp_path / "store")))
    tab = ReaderTab()
    tab.load_document(make_text_pdf(tmp_path / "a.pdf", [(72, 700, 12, "A paper title")]))
    end = time.time() + 15
    while time.time() < end and tab.current_doc is None:
        qapp.processEvents()
    gone = MetadataExtractionWorker("text")
    gone.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    tab.metadata_worker = gone
    with patch("riemann.ui.reader.mixins.metadata.MetadataExtractionWorker") as worker_class:
        tab.extract_document_metadata()
    worker_class.assert_called_once()
    tab.metadata_worker = gone
    tab.cleanup()
    assert tab.metadata_worker is None
    tab.deleteLater()
