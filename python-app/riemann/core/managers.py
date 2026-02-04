"""
State and Data Managers.

This module contains classes responsible for managing persistent application state,
including bookmarks, browsing history, and file downloads. It handles serialization
to the local filesystem (JSON) and provides models for UI consumption.
"""

import json
import os
from typing import Any, Callable, Dict, List, Optional

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

    Handles CRUD operations for bookmarks stored in a JSON file within the
    user's application data directory.
    """

    def __init__(self) -> None:
        """Initializes the manager and loads existing bookmarks from disk."""
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
                with open(self.path, "r", encoding="utf-8") as f:
                    self.bookmarks = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.bookmarks = []

    def save(self) -> None:
        """Saves the current list of bookmarks to disk."""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.bookmarks, f, indent=2)
        except OSError:
            pass

    def add(self, title: str, url: str) -> None:
        """
        Adds a new bookmark if the URL does not already exist.

        Args:
            title: The display title.
            url: The target URL.
        """
        for b in self.bookmarks:
            if b["url"] == url:
                return
        self.bookmarks.append({"title": title, "url": url})
        self.save()

    def remove(self, url: str) -> None:
        """
        Removes a bookmark by URL.

        Args:
            url: The URL to remove.
        """
        self.bookmarks = [b for b in self.bookmarks if b["url"] != url]
        self.save()

    def is_bookmarked(self, url: str) -> bool:
        """
        Checks if a URL is bookmarked.

        Args:
            url: The URL to check.

        Returns:
            True if bookmarked, False otherwise.
        """
        return any(b["url"] == url for b in self.bookmarks)


class HistoryManager:
    """
    Manages browsing and document history.

    Maintains categorized lists (PDF vs Web) of recently accessed items,
    enforcing a maximum limit and handling persistence. Also provides
    data for autocomplete models.
    """

    MAX_ENTRIES = 500

    def __init__(self) -> None:
        """Initializes the manager and loads history from disk."""
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self.path = os.path.join(base, "history.json")
        self.history: Dict[str, List[str]] = {"pdf": [], "web": []}

        self.popular_sites: List[str] = [
            "music.youtube.com",
            "whatsapp.com",
            "google.com",
            "monkeytype.com",
            "erp.iitkgp.ac.in",
            "youtube.com",
            "keep.google.com",
            "linkedin.com",
            "github.com",
            "pplx.ai",
            "reddit.com",
            "wikipedia.org",
            "chatgpt.com",
            "gemini.google.com",
        ]
        self.load()

    def load(self) -> None:
        """Loads history from the JSON persistence file."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.history = {"pdf": [], "web": data}
                    elif isinstance(data, dict):
                        self.history = data
            except (json.JSONDecodeError, OSError):
                self.history = {"pdf": [], "web": []}

    def save(self) -> None:
        """Saves current history to disk."""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2)
        except OSError:
            pass

    def add(self, item: str, item_type: str = "web") -> None:
        """
        Adds an item to history, promoting it to the top.

        Args:
            item: The URL or file path.
            item_type: 'pdf' or 'web'.
        """
        if not item:
            return

        target_list = self.history.get(item_type, [])

        if item in target_list:
            target_list.remove(item)

        target_list.insert(0, item)
        self.history[item_type] = target_list[: self.MAX_ENTRIES]
        self.save()

    def get_model_data(self) -> List[str]:
        """
        Combines history and default sites for autocomplete suggestions.

        Returns:
            A list of strings.
        """
        web_history = self.history.get("web", [])
        pdf_history = self.history.get("pdf", [])
        extras = [s for s in self.popular_sites if s not in web_history]
        return web_history + extras + pdf_history

    def get_list(self, item_type: str) -> List[str]:
        """
        Retrieves the history list for a specific category.

        Args:
            item_type: 'pdf' or 'web'.
        """
        return self.history.get(item_type, [])


class DownloadManager(QDialog):
    """
    A non-modal dialog for managing file downloads.

    Displays active, completed, and failed downloads in a table.
    Supports pausing, resuming, and cancelling active downloads.
    Persists download history across sessions.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """
        Initializes the Download Manager UI.

        Args:
            parent: The parent widget (usually the main window).
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

    def add_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """
        Registers a new active download request.

        Args:
            download_item: The QtWebEngine download request.
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
        Updates the table row based on the download state.

        Args:
            row: Table row index.
            item: Download request object.
            state: Current download state.
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
                    for btn in btns:
                        btn.setEnabled(False)

                path = os.path.join(item.downloadDirectory(), item.downloadFileName())
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
                status = "Interrupted"
                if item.isPaused():
                    status = "Paused"
                    if btns:
                        btns[0].setEnabled(False)
                        btns[1].setEnabled(True)
                        btns[2].setEnabled(True)
                else:
                    status = "Failed"
                    if btns:
                        for btn in btns:
                            btn.setEnabled(False)
                self._persist_entries()

            self.table.setItem(row, 1, QTableWidgetItem(status))

        except RuntimeError:
            pass
        except Exception as e:
            print(f"DownloadManager Error: {e}")

    def clear_finished(self) -> None:
        """Removes rows for downloads that are completed, cancelled, or failed."""
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

    def _set_open_button(self, row: int, full_path: str) -> None:
        """Replaces control buttons with an 'Open' button."""
        container = QWidget()
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(2, 2, 2, 2)
        h_layout.setSpacing(4)

        btn_open = QPushButton("Open")
        btn_open.setFixedWidth(50)

        def _open_path() -> None:
            if full_path and os.path.exists(full_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(full_path))
            else:
                QMessageBox.information(self, "Open File", "File not found on disk.")

        btn_open.clicked.connect(_open_path)
        h_layout.addWidget(btn_open)

        self.table.setCellWidget(row, 3, container)

    def _make_state_slot(
        self, row: int, item: QWebEngineDownloadRequest
    ) -> Callable[[Any], None]:
        """Creates a closure to handle state changes for a specific row."""

        def _slot(state: QWebEngineDownloadRequest.DownloadState) -> None:
            try:
                self.update_status(row, item, state)
            except RuntimeError:
                try:
                    item.stateChanged.disconnect(_slot)
                except Exception:
                    pass

        return _slot

    def _make_finished_slot(
        self, row: int, item: QWebEngineDownloadRequest
    ) -> Callable[[], None]:
        """Creates a closure to handle completion for a specific row."""

        def _slot() -> None:
            try:
                self.on_finished(row, item)
            except RuntimeError:
                try:
                    item.finished.disconnect(_slot)
                except Exception:
                    pass

        return _slot

    def on_finished(self, row: int, item: QWebEngineDownloadRequest) -> None:
        """Signal handler for download completion."""
        try:
            self.update_status(row, item, item.state())
        except Exception:
            pass

    def _load_persistent_entries(self) -> None:
        """Restores download history from disk."""
        if not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError):
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

    def _persist_entries(self) -> None:
        """Writes current table state to disk."""
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
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
        except OSError:
            pass

    def closeEvent(self, event: Any) -> None:
        """Clean up signals and save state on close."""
        for d in list(self.downloads):
            try:
                d["item"].stateChanged.disconnect(d.get("state_slot"))
                d["item"].finished.disconnect(d.get("finished_slot"))
            except Exception:
                pass

        self._persist_entries()
        super().closeEvent(event)
