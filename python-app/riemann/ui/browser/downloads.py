"""
File downloads, video downloads with yt-dlp, and saving a page as PDF.

Every tab on a profile is connected to its downloadRequested signal, so each
request is claimed once (DOWNLOAD_CLAIMED, a dynamic property on the request)
and answered by the tab whose page started it; a pop-up's download goes to the
first tab that sees it. Only documents (opens_after_download) are opened in a
tab when they finish.

cancel_download goes through the yt-dlp downloads window when there is one,
so its row shows the download stopping too.
"""

import os

from PySide6.QtCore import QTimer
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWidgets import QDialog, QFileDialog

from ...core.settings import (
    AUTO_OPEN_DOWNLOADS_KEY,
    app_settings,
    get_dialog_directory,
    save_last_directory,
)
from .ytdlp import YtDlpWorker, YtDlpSettingsDialog
from .page import DOWNLOAD_CLAIMED, owning_tab, opens_after_download


class DownloadsMixin:
    def _handle_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """Asks where to save a download started on this profile; only one tab answers each request."""
        if download_item.property(DOWNLOAD_CLAIMED):
            return
        owner = owning_tab(download_item.page())
        if owner is not None and owner is not self:
            return
        download_item.setProperty(DOWNLOAD_CLAIMED, True)
        store = app_settings()
        start_dir = get_dialog_directory(store)

        path, _ = QFileDialog.getSaveFileName(
            self, "Save File", os.path.join(start_dir, download_item.downloadFileName())
        )

        if not path:
            download_item.cancel()
            return

        save_last_directory(store, path)
        download_item.setDownloadDirectory(os.path.dirname(path))
        download_item.setDownloadFileName(os.path.basename(path))
        download_item.accept()

        self.show_toast(f"Starting: {download_item.downloadFileName()}")

        def update_prog():
            total = download_item.totalBytes()
            if total > 0:
                self.progress.setValue(int((download_item.receivedBytes() / total) * 100))

        download_item.receivedBytesChanged.connect(update_prog)
        download_item.totalBytesChanged.connect(update_prog)

        def on_state_changed(state):
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self.show_toast("Download complete!")
                self.progress.setValue(0)
                self._check_download_open(
                    state, download_item, download_item.downloadDirectory()
                )
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                self.show_toast("Download cancelled.")
                self.progress.setValue(0)
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
                self.show_toast("Download failed.")
                self.progress.setValue(0)

        download_item.stateChanged.connect(on_state_changed)
        if self.window() and hasattr(self.window(), "download_manager_dialog"):
            self.window().download_manager_dialog.add_download(download_item)

    def _check_download_open(
        self, state: int, item: QWebEngineDownloadRequest, temp_folder: str
    ) -> None:
        if state != QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            return
        if not app_settings().value(AUTO_OPEN_DOWNLOADS_KEY, True, type=bool):
            return
        full_path = os.path.join(temp_folder, item.downloadFileName())
        if opens_after_download(full_path, item.mimeType()):
            self._on_document_downloaded(full_path)

    def _on_document_downloaded(self, path: str) -> None:
        if (
            os.path.exists(path)
            and self.window()
            and hasattr(self.window(), "open_pdf_in_new_tab")
        ):
            self.window().open_pdf_in_new_tab(path)

    def download_video(self) -> None:
        url = self.web.url().toString()
        if not url or "http" not in url:
            self.show_toast("Invalid URL for download.")
            return

        is_pl = "list=" in url.lower() or "/playlist" in url.lower()
        dialog = YtDlpSettingsDialog(self, is_playlist=is_pl)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dl_opts = dialog.get_options()

            store = app_settings()
            start_dir = get_dialog_directory(store)
            dest_dir = QFileDialog.getExistingDirectory(
                self, "Select Download Directory", start_dir
            )

            if not dest_dir:
                return

            save_last_directory(store, dest_dir)
            self.show_toast("Starting download...")
            self.progress.setValue(0)

            self.btn_download.setIcon(self._get_icon("circle-stop.svg"))
            self.btn_download.setToolTip("Cancel Download")

            try:
                self.btn_download.clicked.disconnect()
            except RuntimeError:
                pass
            self.btn_download.clicked.connect(self.cancel_download)

            raw_title = self.web.title() or "Video_Download"
            self.dl_worker = YtDlpWorker(url, dest_dir, dl_opts)

            try:
                if self.window() and hasattr(self.window(), "ytdlp_manager_dialog"):
                    self.window().ytdlp_manager_dialog.add_download(
                        self.dl_worker,
                        raw_title,
                        dest_dir,
                        dl_opts.get("playlist", False),
                    )
            except Exception as e:
                print(f"[Warning] Could not link to yt-dlp Download Manager: {e}")

            self.dl_worker.progress.connect(self.progress.setValue)
            self.dl_worker.finished.connect(self._on_download_finished)
            self.dl_worker.start()

    def cancel_download(self) -> None:
        if hasattr(self, "dl_worker") and self.dl_worker.isRunning():
            self.show_toast("Cancelling download...")
            manager = getattr(self.window(), "ytdlp_manager_dialog", None)
            if manager is not None:
                manager.cancel(self.dl_worker)
            else:
                self.dl_worker.stop()
            self.progress.setValue(0)

    def print_to_pdf(self) -> None:
        """Renders the current web page directly to a PDF and opens it in Riemann."""
        store = app_settings()
        start_dir = get_dialog_directory(store)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Webpage as PDF",
            os.path.join(
                start_dir,
                f"{self.web.title()}.pdf" if self.web.title() else "webpage.pdf",
            ),
            "PDF Files (*.pdf)",
        )

        if path:
            save_last_directory(store, path)
            self.show_toast("Rendering PDF...")

            def handle_pdf_print(file_path, success):
                self.web.page().pdfPrintingFinished.disconnect(handle_pdf_print)
                if success:
                    self.show_toast("PDF saved successfully!")
                    if self.window() and hasattr(self.window(), "new_pdf_tab"):
                        self.window().new_pdf_tab(file_path)
                else:
                    self.show_toast("Failed to render PDF.")

            self.web.page().pdfPrintingFinished.connect(handle_pdf_print)
            self.web.page().printToPdf(path)

    def _on_download_finished(self, success: bool, message: str) -> None:
        self.btn_download.setIcon(self._get_icon("download.svg"))
        self.btn_download.setToolTip("Download Video via yt-dlp")
        try:
            self.btn_download.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_download.clicked.connect(self.download_video)

        if success:
            self.progress.setValue(100)
            QTimer.singleShot(2000, self.progress, lambda: self.progress.setValue(0))
        else:
            self.progress.setValue(0)

        self.show_toast(message)
