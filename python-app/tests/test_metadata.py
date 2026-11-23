import os
from unittest.mock import MagicMock, patch

import pytest

from riemann.ui.reader.mixins.metadata import DocumentMetadata


class Host:
    """What DocumentMetadata is given: a library, the document's path and a widget."""

    def __init__(self, qtbot):
        from PySide6.QtWidgets import QWidget

        self.widget = QWidget()
        qtbot.addWidget(self.widget)
        self.path = "/fake/docs/doc.pdf"
        self.library = MagicMock()
        self.metadata = DocumentMetadata(lambda: self.library, lambda: self.path, self.widget)
        self.toasts = []
        self.renamed = []
        self.metadata.toast.connect(self.toasts.append)
        self.metadata.renamed.connect(self._renamed)

    def _renamed(self, old, new):
        self.renamed.append((old, new))
        self.path = new


@pytest.fixture
def host(qtbot):
    return Host(qtbot)


def test_extract_metadata_from_cache(host):
    host.library.get_metadata.return_value = {"title": "Cached Title", "year": "2023"}
    first_page = MagicMock()
    host.metadata.extract(first_page)
    assert host.metadata.data["title"] == "Cached Title"
    first_page.assert_not_called()


@patch("riemann.ui.reader.mixins.metadata.MetadataExtractionWorker")
def test_extract_metadata_spawns_worker(mock_worker_class, host):
    host.library.get_metadata.return_value = None
    mock_worker_instance = MagicMock()
    mock_worker_class.return_value = mock_worker_instance
    host.metadata.extract(lambda: "Abstract: This is a test paper.")
    mock_worker_instance.start.assert_called_once()


def test_extracted_details_are_saved_for_the_file_open_at_the_time(host):
    host.metadata._on_extracted({"title": "Late"})
    host.library.save_metadata.assert_called_once_with("/fake/docs/doc.pdf", {"title": "Late"})
    assert host.metadata.data == {"title": "Late"}


@patch("os.rename")
@patch("os.path.exists", return_value=False)
def test_rename_current_pdf(mock_exists, mock_rename, host):
    host.metadata.data = {"title": "Quantum Comput:ing", "authors": "Doe, John, Smith, A.", "year": "2024"}

    host.metadata.rename()

    expected_new_path = os.path.join("/fake/docs", "[2024] - Doe - Quantum Computing.pdf")
    mock_rename.assert_called_once_with("/fake/docs/doc.pdf", expected_new_path)
    assert host.path == expected_new_path
    host.library.save_metadata.assert_called_with(expected_new_path, host.metadata.data)


@patch("PySide6.QtWidgets.QApplication.clipboard")
def test_copy_citation(mock_clipboard, host):
    mock_clip_instance = MagicMock()
    mock_clipboard.return_value = mock_clip_instance
    host.metadata.data = {"bibtex": "@article{doe2024, title={Quantum}}"}
    host.metadata.copy_citation()
    mock_clip_instance.setText.assert_called_once_with("@article{doe2024, title={Quantum}}")
    assert "BibTeX copied" in host.toasts[-1]


def test_rename_moves_annotations(tmp_path, host, monkeypatch):
    from riemann.core import annotation_store

    store = annotation_store.AnnotationStore(str(tmp_path / "store"))
    monkeypatch.setattr(annotation_store, "_shared", store)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    host.path = str(pdf)
    before = store.json_path(str(pdf))
    with open(before, "w") as f:
        f.write('{"pages": {"0": [{"id": "a"}]}}')
    host.metadata.data = {"title": "Deep\nLearning. ", "authors": "", "year": ""}

    host.metadata.rename()

    new_name = "[UnknownYear] - UnknownAuthor - Deep Learning.pdf"
    assert host.path == str(tmp_path / new_name)
    assert (tmp_path / new_name).exists()
    assert store.json_path(host.path) == before
    assert host.renamed == [(str(pdf), str(tmp_path / new_name))]


def test_rename_reports_existing_target(tmp_path, host):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    (tmp_path / "[2024] - Doe - T.pdf").write_bytes(b"")
    host.path = str(pdf)
    host.metadata.data = {"title": "T", "authors": "Doe", "year": "2024"}

    host.metadata.rename()

    assert host.path == str(pdf)
    assert "already exists" in host.toasts[-1]


def test_renaming_from_the_reader_moves_its_path_and_retitles_the_tab(qapp, tmp_path, monkeypatch):
    from riemann.core import annotation_store
    from riemann.ui.reader.tab import ReaderTab

    monkeypatch.setattr(annotation_store, "_shared", annotation_store.AnnotationStore(str(tmp_path / "store")))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    tab = ReaderTab()
    tab.current_path = str(pdf)
    tab.metadata.data = {"title": "T", "authors": "Doe", "year": "2024"}
    titles = []
    tab._update_tab_title = titles.append
    tab.metadata.rename()
    assert tab.current_path == str(tmp_path / "[2024] - Doe - T.pdf")
    assert tab.document_metadata == {"title": "T", "authors": "Doe", "year": "2024"}
    assert titles == ["[2024] - Doe - T.pdf"]
    tab.deleteLater()


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
    tab.metadata.worker = gone
    with patch("riemann.ui.reader.mixins.metadata.MetadataExtractionWorker") as worker_class:
        tab.extract_document_metadata()
    worker_class.assert_called_once()
    tab.metadata.worker = gone
    tab.cleanup()
    assert tab.metadata.worker is None
    tab.deleteLater()
