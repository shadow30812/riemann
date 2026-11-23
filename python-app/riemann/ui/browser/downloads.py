"""
File downloads, video downloads with yt-dlp, and saving a page as PDF.

Downloads works for one QWebEngineView and asks its questions over the widget
it is given (its parent). It reports through signals: toast for messages,
progress for the loading line, document_downloaded for a finished download
that opens in a tab (opens_after_download, when "open downloads" is on),
pdf_saved for a page printed to PDF, download_added and video_download_added
for the downloads windows, video_cancelling when a video download is being
stopped (the yt-dlp window shows its row stopping), and video_running when a
video download starts or ends. While video_active, the toolbar's download
button cancels the download instead of starting one.

Every tab on a profile is connected to its downloadRequested signal, so each
request is claimed once (DOWNLOAD_CLAIMED, a dynamic property on the request)
and answered by the Downloads whose parent shows the page that started it; a
pop-up's download goes to the first one that sees it.
"""

import os

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget

from ...core.settings import (
    AUTO_OPEN_DOWNLOADS_KEY,
    app_settings,
    get_dialog_directory,
    save_last_directory,
)
from .page import DOWNLOAD_CLAIMED, opens_after_download, owning_tab
from .ytdlp import YtDlpSettingsDialog, YtDlpWorker

DONE_SHOWN_MS = 2000


class Downloads(QObject):
    toast = Signal(str)
    progress = Signal(int)
    document_downloaded = Signal(str)
    pdf_saved = Signal(str)
    download_added = Signal(object)
    video_download_added = Signal(object, str, str, bool)
    video_cancelling = Signal(object)
    video_running = Signal(bool)

    def __init__(self, web: QWebEngineView, parent: QWidget) -> None:
        super().__init__(parent)
        self.web = web
        self._widget = parent
        self.dl_worker = None
        self.video_active = False

    def handle_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """Asks where to save a download started on the profile; only one Downloads answers each request."""
        if download_item.property(DOWNLOAD_CLAIMED):
            return
        owner = owning_tab(download_item.page())
        if owner is not None and owner is not self._widget:
            return
        download_item.setProperty(DOWNLOAD_CLAIMED, True)
        store = app_settings()
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Save File", os.path.join(get_dialog_directory(store), download_item.downloadFileName())
        )
        if not path:
            download_item.cancel()
            return

        save_last_directory(store, path)
        download_item.setDownloadDirectory(os.path.dirname(path))
        download_item.setDownloadFileName(os.path.basename(path))
        download_item.accept()
        self.toast.emit(f"Starting: {download_item.downloadFileName()}")

        def update_progress() -> None:
            total = download_item.totalBytes()
            if total > 0:
                self.progress.emit(int((download_item.receivedBytes() / total) * 100))

        download_item.receivedBytesChanged.connect(update_progress)
        download_item.totalBytesChanged.connect(update_progress)
        download_item.stateChanged.connect(lambda state: self._on_state_changed(download_item, state))
        self.download_added.emit(download_item)

    def _on_state_changed(
        self, item: QWebEngineDownloadRequest, state: QWebEngineDownloadRequest.DownloadState
    ) -> None:
        states = QWebEngineDownloadRequest.DownloadState
        messages = {
            states.DownloadCompleted: "Download complete!",
            states.DownloadCancelled: "Download cancelled.",
            states.DownloadInterrupted: "Download failed.",
        }
        if state not in messages:
            return
        self.toast.emit(messages[state])
        self.progress.emit(0)
        if state == states.DownloadCompleted:
            self._open_if_document(item, item.downloadDirectory())

    def _open_if_document(self, item: QWebEngineDownloadRequest, folder: str) -> None:
        if not app_settings().value(AUTO_OPEN_DOWNLOADS_KEY, True, type=bool):
            return
        full_path = os.path.join(folder, item.downloadFileName())
        if opens_after_download(full_path, item.mimeType()) and os.path.exists(full_path):
            self.document_downloaded.emit(full_path)

    def video_download_running(self) -> bool:
        worker = self.dl_worker
        return worker is not None and worker.isRunning()

    def download_video(self) -> None:
        """Asks for yt-dlp's options and a folder, then downloads the page's video there."""
        url = self.web.url().toString()
        if not url or "http" not in url:
            self.toast.emit("Invalid URL for download.")
            return

        is_playlist = "list=" in url.lower() or "/playlist" in url.lower()
        dialog = YtDlpSettingsDialog(self._widget, is_playlist=is_playlist)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.get_options()
        store = app_settings()
        dest_dir = QFileDialog.getExistingDirectory(
            self._widget, "Select Download Directory", get_dialog_directory(store)
        )
        if not dest_dir:
            return

        save_last_directory(store, dest_dir)
        self.toast.emit("Starting download...")
        self.progress.emit(0)
        self.video_active = True
        self.video_running.emit(True)
        self.dl_worker = YtDlpWorker(url, dest_dir, options)
        self.video_download_added.emit(
            self.dl_worker, self.web.title() or "Video_Download", dest_dir, bool(options.get("playlist", False))
        )
        self.dl_worker.progress.connect(self.progress.emit)
        self.dl_worker.done.connect(self._on_video_finished)
        self.dl_worker.start()

    def cancel_video_download(self) -> None:
        worker = self.dl_worker
        if worker is None or not worker.isRunning():
            return
        self.toast.emit("Cancelling download...")
        worker.stop()
        self.video_cancelling.emit(worker)
        self.progress.emit(0)

    def _on_video_finished(self, success: bool, message: str) -> None:
        self.video_active = False
        self.video_running.emit(False)
        if success:
            self.progress.emit(100)
            QTimer.singleShot(DONE_SHOWN_MS, self, lambda: self.progress.emit(0))
        else:
            self.progress.emit(0)
        self.toast.emit(message)

    def print_to_pdf(self) -> None:
        """Asks where to save, then renders the page to a PDF there."""
        store = app_settings()
        title = self.web.title()
        path, _ = QFileDialog.getSaveFileName(
            self._widget,
            "Save Webpage as PDF",
            os.path.join(get_dialog_directory(store), f"{title}.pdf" if title else "webpage.pdf"),
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        save_last_directory(store, path)
        self.toast.emit("Rendering PDF...")
        page = self.web.page()

        def finished(file_path: str, success: bool) -> None:
            page.pdfPrintingFinished.disconnect(finished)
            if success:
                self.toast.emit("PDF saved successfully!")
                self.pdf_saved.emit(file_path)
            else:
                self.toast.emit("Failed to render PDF.")

        page.pdfPrintingFinished.connect(finished)
        page.printToPdf(path)
