from ..workers import MetadataExtractionWorker


class MetadataMixin:
    def extract_document_metadata(self) -> None:
        """Checks the DB for metadata, or spins up the extraction worker if missing."""
        if not self.current_path or not self.current_doc:
            return

        self.document_metadata = {}

        # 1. Check if we already have it in the local database
        if hasattr(self.window(), "library_manager"):
            cached_data = self.window().library_manager.get_metadata(self.current_path)
            if cached_data and cached_data.get("title"):
                self.document_metadata = cached_data
                self._update_tab_title_with_metadata()
                return

        # 2. If not, grab the first page text and spin up the worker
        try:
            first_page_text = self.current_doc.get_page_text(0)
            if not first_page_text.strip():
                return

            # We only need the first ~3000 characters to find a DOI/Title
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
        """Callback when the background worker finishes."""
        if not data:
            return

        self.document_metadata = data

        # Save to database
        if hasattr(self.window(), "library_manager"):
            self.window().library_manager.save_metadata(self.current_path, data)

        self._update_tab_title_with_metadata()

    def _update_tab_title_with_metadata(self) -> None:
        """
        Metadata is extracted and stored in self.document_metadata,
        but tab is no longer auto-renamed here.
        """
        pass
