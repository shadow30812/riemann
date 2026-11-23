"""
A document's bibliographic details: finding them, renaming the file after
them, and copying the BibTeX citation.

DocumentMetadata is given library(), the library manager or None, which
caches details per file, and path(), the document's file; its parent is the
widget its error dialog opens over and the extraction worker belongs to.
extract reads the first page's text on a worker (MetadataExtractionWorker)
unless the library already knows the file; data is None until then. rename
moves the file to "[year] - author - title.pdf" beside it, with Riemann's
annotation copy and the library record: renaming(old) comes just before the
move, so the reader can settle what belongs to the old name, and
renamed(old, new) once it is done. Messages go out through toast.
"""

import os
import re
from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from ....core import annotation_store
from ...threads import track
from ..workers import MetadataExtractionWorker


def _filename_part(value, limit: int) -> str:
    """Makes text safe in a file name on Linux and Windows, cut to limit characters."""
    text = re.sub(r"\s+", " ", str(value or ""))
    text = re.sub(r'[\\/*?:"<>|\x00-\x1f\x7f]', "", text).strip()
    return text[:limit].rstrip(" .")


def renamed_filename(data: dict) -> str:
    """The file name a document with these details is renamed to."""
    year = _filename_part(data.get("year"), 4) or "UnknownYear"
    authors = str(data.get("authors") or "").split(",")[0]
    author = _filename_part(authors, 20) or "UnknownAuthor"
    title = _filename_part(data.get("title"), 50) or "UnknownTitle"
    return f"[{year}] - {author} - {title}.pdf"


class DocumentMetadata(QObject):
    toast = Signal(str)
    renaming = Signal(str)
    renamed = Signal(str, str)

    def __init__(self, library: Callable[[], Any], path: Callable[[], str | None], parent: QWidget) -> None:
        super().__init__(parent)
        self.library = library
        self.path = path
        self._widget = parent
        self.data: dict | None = None
        self.worker: MetadataExtractionWorker | None = None

    def live_worker(self) -> MetadataExtractionWorker | None:
        """The extraction worker, or None once it has finished and deleted itself."""
        if self.worker is not None and not shiboken6.isValid(self.worker):
            self.worker = None
        return self.worker

    def extract(self, first_page_text: Callable[[], str]) -> None:
        """Finds the document's details, from the library or from first_page_text()."""
        worker = self.live_worker()
        if worker is not None and worker.isRunning():
            return
        self.data = {}
        library = self.library()
        if library is not None:
            cached = library.get_metadata(self.path())
            if cached and cached.get("title"):
                self.data = cached
                return
        try:
            text = first_page_text()
            if not text.strip():
                return
            self.worker = MetadataExtractionWorker(text[:3000], parent=self._widget)
            self.worker.finished_extraction.connect(self._on_extracted)
            self.worker.finished.connect(self.worker.deleteLater)
            track(self.worker).start()
        except Exception as e:
            print(f"Failed to start metadata extraction: {e}")

    def _on_extracted(self, data: dict) -> None:
        if not data:
            return
        self.data = data
        library = self.library()
        if library is not None:
            library.save_metadata(self.path(), data)

    def rename(self) -> None:
        """Renames the document's file after its details."""
        path = self.path()
        if not path or not self.data:
            return
        new_filename = renamed_filename(self.data)
        new_path = os.path.join(os.path.dirname(path), new_filename)
        if new_path == path:
            return
        if os.path.exists(new_path):
            self.toast.emit(f"Not renamed: {new_filename} already exists.")
            return

        self.renaming.emit(path)
        try:
            os.rename(path, new_path)
        except Exception as e:
            QMessageBox.critical(self._widget, "Rename Failed", f"Could not rename file:\n{e}")
            return

        annotation_store.shared().moved(path, new_path)
        self.renamed.emit(path, new_path)
        library = self.library()
        if library is not None:
            library.save_metadata(new_path, self.data)
        self.toast.emit(f"Renamed to: {new_filename}")

    def copy_citation(self) -> None:
        """Copies the BibTeX citation to the clipboard."""
        bibtex = (self.data or {}).get("bibtex")
        if bibtex:
            QApplication.clipboard().setText(bibtex)
            self.toast.emit("BibTeX copied to clipboard! 📋")
        else:
            self.toast.emit("Citation data not available.")
