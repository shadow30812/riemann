import json
import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class BookmarksManager:
    """
    Manages persistent bookmarks for the application.

    This class handles loading, saving, adding, and removing bookmarks
    stored in a JSON file within the user's application data directory.
    """

    def __init__(self) -> None:
        """Initializes the BookmarksManager and loads existing bookmarks."""
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self.path = os.path.join(base, "bookmarks.json")
        self.bookmarks: List[Dict[str, str]] = []
        self.load()

    def load(self) -> None:
        """Loads bookmarks from the JSON persistence file."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    self.bookmarks = json.load(f)
            except Exception:
                self.bookmarks = []

    def save(self) -> None:
        """Saves the current list of bookmarks to the JSON persistence file."""
        try:
            with open(self.path, "w") as f:
                json.dump(self.bookmarks, f)
        except Exception:
            pass

    def add(self, title: str, url: str) -> None:
        """
        Adds a new bookmark.

        Args:
            title: The display title of the bookmark.
            url: The URL string of the bookmark.
        """
        for b in self.bookmarks:
            if b["url"] == url:
                return
        self.bookmarks.append({"title": title, "url": url})
        self.save()

    def remove(self, url: str) -> None:
        """
        Removes a bookmark identified by its URL.

        Args:
            url: The URL string of the bookmark to remove.
        """
        self.bookmarks = [b for b in self.bookmarks if b["url"] != url]
        self.save()

    def is_bookmarked(self, url: str) -> bool:
        """
        Checks if a specific URL is currently bookmarked.

        Args:
            url: The URL string to check.

        Returns:
            True if the URL is in the bookmarks list, False otherwise.
        """
        return any(b["url"] == url for b in self.bookmarks)


class HistoryManager:
    """
    Manages persistent history for opened documents and websites.

    Maintains a list of recently accessed items, ensuring uniqueness and
    capping the list size. Also provides a combined list of history
    and popular sites for autocomplete functionality.
    """

    def __init__(self) -> None:
        """Initializes the HistoryManager and loads existing history."""
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self.path = os.path.join(base, "history.json")
        self.history: List[str] = []
        self.popular_sites: List[str] = [
            "google.com",
            "youtube.com",
            "github.com",
            "stackoverflow.com",
            "reddit.com",
            "wikipedia.org",
            "arxiv.org",
            "chatgpt.com",
        ]
        self.load()

    def load(self) -> None:
        """Loads history from the JSON persistence file."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    self.history = json.load(f)
            except Exception:
                self.history = []

    def save(self) -> None:
        """Saves the current history to the JSON persistence file."""
        try:
            with open(self.path, "w") as f:
                json.dump(self.history, f)
        except Exception:
            pass

    def add(self, item: str) -> None:
        """
        Adds an item to the history.

        Removes duplicates to promote the item to the top of the list
        and limits the history to 500 entries.

        Args:
            item: The file path or URL to add.
        """
        if not item:
            return
        if item in self.history:
            self.history.remove(item)
        self.history.insert(0, item)
        self.history = self.history[:500]
        self.save()

    def get_model_data(self) -> List[str]:
        """
        Retrieves a combined list of history items and popular sites.

        Returns:
            A list of strings suitable for autocomplete models.
        """
        extras = [s for s in self.popular_sites if s not in self.history]
        return self.history + extras


class DownloadManager(QDialog):
    """
    A dialog widget that manages and displays file downloads.

    Provides a table view of active and completed downloads with controls
    to pause, resume, cancel, and open files. Persists download history
    metadata across sessions.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """
        Initializes the DownloadManager dialog.

        Args:
            parent: The parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Downloads")
        self.resize(800, 425)

        layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Path", "Actions"])

        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)

        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 360)
        self.table.setColumnWidth(3, 120)

        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        btn_clear = QPushButton("Clear Finished")
        btn_clear.clicked.connect(self.clear_finished)
        layout.addWidget(btn_clear)

        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        os.makedirs(base, exist_ok=True)
        self._persist_path = os.path.join(base, "downloads_history.json")

        self.downloads: List[Dict[str, Any]] = []

        self._load_persistent_entries()

    def _set_open_button(self, row: int, full_path: str) -> None:
        """
        Replaces action buttons with an 'Open' button for completed downloads.

        Args:
            row: The table row index.
            full_path: The absolute path to the downloaded file.
        """
        container = QWidget()
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(2, 2, 2, 2)
        h_layout.setSpacing(4)

        btn_open = QPushButton("Open")
        btn_open.setFixedWidth(50)

        def _open_path(p: str = full_path) -> None:
            if p and os.path.exists(p):
                QDesktopServices.openUrl(QUrl.fromLocalFile(p))
            else:
                QMessageBox.information(self, "Open File", "File not found on disk.")

        btn_open.clicked.connect(_open_path)
        h_layout.addWidget(btn_open)

        self.table.setCellWidget(row, 3, container)

    def _load_persistent_entries(self) -> None:
        """Loads historical download metadata from disk to populate the table."""
        if not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r") as f:
                entries = json.load(f)
        except Exception:
            return

        for e in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(
                row, 0, QTableWidgetItem(e.get("file_name", "<unknown>"))
            )
            self.table.setItem(row, 1, QTableWidgetItem(e.get("status", "Completed")))
            path_str = e.get("full_path", "...")
            path_item = QTableWidgetItem(path_str)
            path_item.setToolTip(path_str)
            self.table.setItem(row, 2, path_item)

            self._set_open_button(row, path_str)

            container = QWidget()
            h_layout = QHBoxLayout(container)
            h_layout.setContentsMargins(2, 2, 2, 2)
            h_layout.setSpacing(4)
            btn_dummy = QPushButton("Open")
            btn_dummy.setFixedWidth(50)

            def _open_path(p: str = e.get("full_path", "")) -> None:
                if p and os.path.exists(p):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(p))
                else:
                    QMessageBox.information(
                        self, "Open File", "File not found on disk."
                    )

            btn_dummy.clicked.connect(_open_path)
            h_layout.addWidget(btn_dummy)
            self.table.setCellWidget(row, 3, container)

    def _make_state_slot(self, row: int, item: QWebEngineDownloadRequest):
        """Creates a closure slot for handling state changes."""

        def _slot(state: QWebEngineDownloadRequest.DownloadState) -> None:
            try:
                self.update_status(row, item, state)
            except RuntimeError:
                try:
                    item.stateChanged.disconnect(_slot)
                except Exception:
                    pass

        return _slot

    def _persist_entries(self) -> None:
        """Saves current table metadata to disk."""
        out = []
        for i in range(self.table.rowCount()):
            file_item = self.table.item(i, 0)
            status_item = self.table.item(i, 1)
            path_item = self.table.item(i, 2)
            out.append(
                {
                    "file_name": file_item.text() if file_item else "",
                    "status": status_item.text() if status_item else "",
                    "full_path": path_item.text() if path_item else "",
                }
            )
        try:
            with open(self._persist_path, "w") as f:
                json.dump(out, f)
        except Exception:
            pass

    def _make_finished_slot(self, row: int, item: QWebEngineDownloadRequest):
        """Creates a closure slot for handling completion."""

        def _slot() -> None:
            try:
                self.on_finished(row, item)
            except RuntimeError:
                try:
                    item.finished.disconnect(_slot)
                except Exception:
                    pass

        return _slot

    def add_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """
        Registers a new active download request and adds it to the UI.

        Args:
            download_item: The WebEngine download request object.
        """
        row = self.table.rowCount()
        self.table.insertRow(row)

        name = download_item.downloadFileName()
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem("Starting..."))

        path_text = download_item.downloadDirectory()
        if path_text:
            full_path = os.path.join(
                download_item.downloadDirectory(), download_item.downloadFileName()
            )
        else:
            full_path = "..."
        path_item = QTableWidgetItem(full_path)
        path_item.setToolTip(full_path)
        self.table.setItem(row, 2, path_item)

        container = QWidget()
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(2, 2, 2, 2)
        h_layout.setSpacing(4)

        btn_pause = QPushButton("⏸")
        btn_pause.setFixedWidth(30)
        btn_pause.setToolTip("Pause")

        btn_resume = QPushButton("▶")
        btn_resume.setFixedWidth(30)
        btn_resume.setToolTip("Resume")
        btn_resume.setEnabled(False)

        btn_cancel = QPushButton("⏹")
        btn_cancel.setFixedWidth(30)
        btn_cancel.setToolTip("Cancel")

        h_layout.addWidget(btn_pause)
        h_layout.addWidget(btn_resume)
        h_layout.addWidget(btn_cancel)

        try:
            self.table.setCellWidget(row, 3, container)
        except Exception:
            self.table.setCellWidget(row, 2, container)

        btn_pause.clicked.connect(download_item.pause)
        btn_resume.clicked.connect(download_item.resume)
        btn_cancel.clicked.connect(download_item.cancel)

        state_slot = self._make_state_slot(row, download_item)
        finished_slot = self._make_finished_slot(row, download_item)

        download_item.stateChanged.connect(state_slot)
        download_item.stateChanged.connect(finished_slot)

        self.downloads.append(
            {
                "item": download_item,
                "row": row,
                "btns": (btn_pause, btn_resume, btn_cancel),
                "state_slot": state_slot,
                "finished_slot": finished_slot,
            }
        )

        try:
            self.update_status(row, download_item, download_item.state())
        except RuntimeError:
            pass

        self._persist_entries()

    def update_status(
        self,
        row: int,
        item: QWebEngineDownloadRequest,
        state: QWebEngineDownloadRequest.DownloadState,
    ) -> None:
        """
        Updates the UI to reflect the current state of a download.

        Args:
            row: The row index in the table.
            item: The download request object.
            state: The new download state.
        """
        try:
            status = "Unknown"
            btns = None
            for d in self.downloads:
                if d["item"] == item:
                    btns = d["btns"]
                    break

            if state == QWebEngineDownloadRequest.DownloadState.DownloadInProgress:
                percent = 0
                if item.totalBytes() > 0:
                    percent = int(item.receivedBytes() / item.totalBytes() * 100)
                status = f"{percent}%"
                if btns:
                    btns[0].setEnabled(True)
                    btns[1].setEnabled(False)
                    btns[2].setEnabled(True)

            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                status = "Completed"
                if btns:
                    btns[0].setEnabled(False)
                    btns[1].setEnabled(False)
                    btns[2].setEnabled(False)

                path = item.downloadDirectory() + "/" + item.downloadFileName()
                path_item = QTableWidgetItem(path)
                path_item.setToolTip(path)

                self.table.setItem(row, 2, path_item)
                self._set_open_button(row, path)
                self._persist_entries()

            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                status = "Cancelled"
                if btns:
                    btns[0].setEnabled(False)
                    btns[1].setEnabled(True)
                    btns[2].setEnabled(False)
                self._persist_entries()

            elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
                status = "Interrupted/Paused"
                if item.isPaused():
                    status = "Paused"
                    if btns:
                        btns[0].setEnabled(False)
                        btns[1].setEnabled(True)
                        btns[2].setEnabled(True)
                else:
                    status = "Failed"
                    if btns:
                        btns[0].setEnabled(False)
                        btns[1].setEnabled(False)
                        btns[2].setEnabled(False)
                self._persist_entries()

            self.table.setItem(row, 1, QTableWidgetItem(status))

        except RuntimeError:
            pass
        except Exception as e:
            print("update_status error:", e)

    def clear_finished(self) -> None:
        """Removes completed, cancelled, or failed rows from the table."""
        rows_to_remove = []
        for i in range(self.table.rowCount()):
            status_item = self.table.item(i, 1)
            if status_item and status_item.text() in [
                "Completed",
                "Cancelled",
                "Failed",
            ]:
                rows_to_remove.append(i)

        for i in sorted(rows_to_remove, reverse=True):
            self.table.removeRow(i)
            if i < len(self.downloads):
                del self.downloads[i]

        self._persist_entries()

    def on_finished(self, row: int, item: QWebEngineDownloadRequest) -> None:
        """Handler called when a download finishes."""
        try:
            self.update_status(row, item, item.state())
        except Exception:
            pass

    def closeEvent(self, event: Any) -> None:
        """Handles the window close event to cleanup signals and persist state."""
        for d in list(self.downloads):
            try:
                d["item"].stateChanged.disconnect(d.get("state_slot"))
            except Exception:
                pass
            try:
                d["item"].finished.disconnect(d.get("finished_slot"))
            except Exception:
                pass

        self._persist_entries()
        super().closeEvent(event)
