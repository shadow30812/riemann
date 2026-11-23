"""
Metadata Extraction and Renaming Mixin.

This module provides functionality to extract, store, and utilize metadata
from PDF documents, including automated file renaming and citation copying.
"""

import os
import re

import shiboken6
from PySide6.QtWidgets import QApplication, QMessageBox

from ....core import annotation_store
from ...threads import track
from ..notes import notes_path_for
from ..workers import MetadataExtractionWorker
from .host import ReaderHostBase


def _filename_part(value, limit: int) -> str:
    """Makes text safe in a file name on Linux and Windows, cut to limit characters."""
    text = re.sub(r"\s+", " ", str(value or ""))
    text = re.sub(r'[\\/*?:"<>|\x00-\x1f\x7f]', "", text).strip()
    return text[:limit].rstrip(" .")


class MetadataMixin(ReaderHostBase):
    """
    Provides methods for background metadata extraction and document management operations.
    Expected to be mixed into a class providing document context (e.g., ReaderTab).
    """
    document_metadata = None
    metadata_worker = None

    def _live_metadata_worker(self):
        """The metadata worker, or None once it has finished and deleted itself."""
        if self.metadata_worker is not None and not shiboken6.isValid(self.metadata_worker):
            self.metadata_worker = None
        return self.metadata_worker

    def extract_document_metadata(self) -> None:
        """
        Initiates the extraction of document metadata.
        Checks the local library database first for cached metadata. If not found,
        spins up a background worker to extract metadata from the first page's text.
        """
        if not self.current_path or not self.current_doc or self.converted_pdf:
            return

        worker = self._live_metadata_worker()
        if worker is not None and worker.isRunning():
            return

        self.document_metadata = {}

        if hasattr(self.window(), "library_manager"):
            cached_data = self.window().library_manager.get_metadata(self.current_path)
            if cached_data and cached_data.get("title"):
                self.document_metadata = cached_data
                self._update_tab_title_with_metadata()
                return

        try:
            first_page_text = self.current_doc.get_page_text(0)
            if not first_page_text.strip():
                return

            self.metadata_worker = MetadataExtractionWorker(
                first_page_text[:3000], parent=self
            )
            self.metadata_worker.finished_extraction.connect(
                self._on_metadata_extracted
            )
            self.metadata_worker.finished.connect(self.metadata_worker.deleteLater)
            track(self.metadata_worker).start()
        except Exception as e:
            print(f"Failed to start metadata extraction: {e}")

    def _on_metadata_extracted(self, data: dict) -> None:
        """Saves extracted metadata to the library and updates the title."""
        if not data:
            return

        self.document_metadata = data

        if hasattr(self.window(), "library_manager"):
            self.window().library_manager.save_metadata(self.current_path, data)

        self._update_tab_title_with_metadata()

    def _update_tab_title_with_metadata(self) -> None:
        """
        Updates the UI tab title using the extracted metadata.
        Currently implemented as a no-op placeholder for extended subclasses.
        Behavior inferred from implementation.
        """
        pass

    def rename_current_pdf(self) -> None:
        """
        Renames the active PDF file on the filesystem using the extracted document metadata.
        Constructs a sanitized filename and moves the path state, Riemann's annotation
        copy and the library record along with the file.
        """
        if not self.current_path or not self.document_metadata:
            return

        year = _filename_part(self.document_metadata.get("year"), 4) or "UnknownYear"
        authors = str(self.document_metadata.get("authors") or "").split(",")[0]
        safe_author = _filename_part(authors, 20) or "UnknownAuthor"
        safe_title = _filename_part(self.document_metadata.get("title"), 50) or "UnknownTitle"

        new_filename = f"[{year}] - {safe_author} - {safe_title}.pdf"
        new_path = os.path.join(os.path.dirname(self.current_path), new_filename)

        if new_path == self.current_path:
            return
        if os.path.exists(new_path):
            self.show_toast(f"Not renamed: {new_filename} already exists.")
            return

        old_path = self.current_path
        self._get_annotation_path()
        try:
            os.rename(old_path, new_path)
        except Exception as e:
            QMessageBox.critical(self, "Rename Failed", f"Could not rename file:\n{e}")
            return

        self.current_path = new_path
        notes = self.notes_panel
        if notes is not None:
            notes.save()
            old_notes = notes_path_for(old_path)
            if os.path.exists(old_notes):
                try:
                    os.replace(old_notes, notes_path_for(new_path))
                except OSError as e:
                    print(f"[Warning] Could not move notes for {new_path}: {e}")
            notes.path = notes_path_for(new_path)
        annotation_store.shared().moved(old_path, new_path)
        self._anno_json_cache = None

        if hasattr(self.window(), "library_manager"):
            self.window().library_manager.save_metadata(new_path, self.document_metadata)

        self._update_tab_title(new_filename)
        self.show_toast(f"Renamed to: {new_filename}")
        self._update_tab_title_with_metadata()

    def copy_citation(self) -> None:
        """
        Extracts the BibTeX citation format from the document metadata and copies it to the system clipboard.
        Displays a UI toast notification indicating success or failure.
        """
        bibtex = (self.document_metadata or {}).get("bibtex")
        if bibtex:
            QApplication.clipboard().setText(bibtex)
            self.show_toast("BibTeX copied to clipboard! 📋")
        else:
            self.show_toast("Citation data not available.")
