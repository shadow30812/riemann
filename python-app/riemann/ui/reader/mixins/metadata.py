"""
Metadata Extraction and Renaming Mixin.

This module provides functionality to extract, store, and utilize metadata
from PDF documents, including automated file renaming and citation copying.
"""

import os
import re

from PySide6.QtWidgets import QApplication, QMessageBox

from ..workers import MetadataExtractionWorker


class MetadataMixin:
    """
    Provides methods for background metadata extraction and document management operations.
    Expected to be mixed into a class providing document context (e.g., ReaderTab).
    """

    def extract_document_metadata(self) -> None:
        """
        Initiates the extraction of document metadata.
        Checks the local library database first for cached metadata. If not found,
        spins up a background worker to extract metadata from the first page's text.
        """
        if not self.current_path or not self.current_doc:
            return

        if hasattr(self, "metadata_worker") and self.metadata_worker.isRunning():
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
            self.metadata_worker.start()
        except Exception as e:
            print(f"Failed to start metadata extraction: {e}")

    def _on_metadata_extracted(self, data: dict) -> None:
        """
        Callback handler executed when the background metadata extraction worker completes successfully.
        Saves the extracted data to the library database and triggers a title update.

        Args:
            data (dict): The dictionary containing the extracted document metadata.
        """
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
        Constructs a sanitized filename and updates the internal path state and library database upon success.
        """
        if not self.current_path or not self.document_metadata:
            return

        year = self.document_metadata.get("year", "UnknownYear")
        authors = self.document_metadata.get("authors", "UnknownAuthor").split(",")[0]
        title = self.document_metadata.get("title", "UnknownTitle")

        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        safe_author = re.sub(r'[\\/*?:"<>|]', "", authors)[:20]

        new_filename = f"[{year}] - {safe_author} - {safe_title}.pdf"
        new_path = os.path.join(os.path.dirname(self.current_path), new_filename)

        if new_path != self.current_path and not os.path.exists(new_path):
            try:
                os.rename(self.current_path, new_path)

                old_path = self.current_path
                self.current_path = new_path
                self.settings.setValue("lastFile", new_path)

                if hasattr(self.window(), "library_manager"):
                    self.window().library_manager.save_metadata(
                        new_path, self.document_metadata
                    )

                self.show_toast(f"Renamed to: {new_filename}")
                self._update_tab_title_with_metadata()

            except Exception as e:
                QMessageBox.critical(
                    self, "Rename Failed", f"Could not rename file:\n{e}"
                )

    def copy_citation(self) -> None:
        """
        Extracts the BibTeX citation format from the document metadata and copies it to the system clipboard.
        Displays a UI toast notification indicating success or failure.
        """
        bibtex = getattr(self, "document_metadata", {}).get("bibtex")
        if bibtex:
            QApplication.clipboard().setText(bibtex)
            self.show_toast("BibTeX copied to clipboard! 📋")
        else:
            self.show_toast("Citation data not available.")
