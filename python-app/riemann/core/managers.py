"""
Core data managers for the Riemann application.

Provides persistent data handling for the library metadata, bookmarks,
browsing history, and active/historical downloads. Uses SQLite for
complex metadata querying and JSON for lightweight list persistence.
"""

import hashlib
import json
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QStandardPaths, Qt, QUrl
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


class LibraryManager:
    """
    Manages persistent metadata for the local PDF library.
    Backed by a SQLite database stored in the user's application data directory.
    """

    def __init__(self) -> None:
        """
        Initializes the library manager and ensures the persistent database exists.
        """
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self.db_path = os.path.join(base, "riemann_library.db")
        self._init_db()

    def _init_db(self) -> None:
        """
        Creates the metadata table in the SQLite database if it does not already exist.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    file_hash TEXT PRIMARY KEY,
                    file_path TEXT,
                    title TEXT,
                    authors TEXT,
                    year TEXT,
                    doi TEXT,
                    arxiv_id TEXT
                )
            """)

    def get_file_hash(self, file_path: str) -> str:
        """
        Generates a SHA-256 hash for a given file path.

        Args:
            file_path (str): The absolute path to the file.

        Returns:
            str: The hexadecimal SHA-256 hash representing the file path.
        """
        return hashlib.sha256(file_path.encode("utf-8")).hexdigest()

    def get_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Retrieves metadata associated with a specific file path from the database.

        Args:
            file_path (str): The absolute path to the file.

        Returns:
            Dict[str, Any]: A dictionary containing the file's metadata, or an empty dictionary if not found.
        """
        file_hash = self.get_file_hash(file_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM metadata WHERE file_hash = ?", (file_hash,))
            row = cur.fetchone()
            return dict(row) if row else {}

    def save_metadata(self, file_path: str, data: Dict[str, Any]) -> None:
        """
        Saves or updates metadata for a specific file path in the database.

        Args:
            file_path (str): The absolute path to the file.
            data (Dict[str, Any]): The metadata payload to persist. Expected keys include 'title', 'authors', 'year', 'doi', and 'arxiv_id'.
        """
        file_hash = self.get_file_hash(file_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO metadata (file_hash, file_path, title, authors, year, doi, arxiv_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_path=excluded.file_path,
                    title=excluded.title,
                    authors=excluded.authors,
                    year=excluded.year,
                    doi=excluded.doi,
                    arxiv_id=excluded.arxiv_id
                """,
                (
                    file_hash,
                    file_path,
                    data.get("title", ""),
                    data.get("authors", ""),
                    data.get("year", ""),
                    data.get("doi", ""),
                    data.get("arxiv_id", ""),
                ),
            )

    def search_library(self, query: str) -> List[Dict[str, Any]]:
        """
        Executes a SQL search across the library metadata based on a query string.
        Supports specific field filters such as 'author:' and 'year:'.

        Args:
            query (str): The search query provided by the user.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries representing matching database records.
        """
        import re

        author_match = re.search(r'author:\s*"?([^"\s]+)"?', query, re.IGNORECASE)
        year_match = re.search(r"year:\s*(\d{4})", query, re.IGNORECASE)

        conditions: List[str] = []
        params: List[str] = []

        if author_match:
            conditions.append("authors LIKE ?")
            params.append(f"%{author_match.group(1)}%")
            query = query.replace(author_match.group(0), "")

        if year_match:
            conditions.append("year = ?")
            params.append(year_match.group(1))
            query = query.replace(year_match.group(0), "")

        keywords = query.strip()
        if keywords:
            conditions.append("(title LIKE ? OR authors LIKE ? OR file_path LIKE ?)")
            kw_param = f"%{keywords}%"
            params.extend([kw_param, kw_param, kw_param])

        sql = "SELECT * FROM metadata"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


class BookmarksManager:
    """
    Manages persistent bookmarks for the application.
    Handles CRUD operations for bookmarks stored in a JSON file within the
    user's application data directory.
    """

    def __init__(self) -> None:
        """
        Initializes the manager, determines the storage path, and loads existing bookmarks from disk.
        """
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self.path = os.path.join(base, "bookmarks.json")
        self.bookmarks: List[Dict[str, str]] = []
        self.load()

    def load(self) -> None:
        """
        Loads bookmarks from the JSON persistence file. If the file is missing or corrupted, initializes an empty list.
        """
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.bookmarks = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.bookmarks = []

    def save(self) -> None:
        """
        Saves the current list of bookmarks to the JSON persistence file. Silently ignores file system errors.
        """
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.bookmarks, f, indent=2)
        except OSError:
            pass

    def add(self, title: str, url: str) -> None:
        """
        Adds a new bookmark if the URL does not already exist in the bookmarks list.

        Args:
            title (str): The display title of the bookmark.
            url (str): The target URL.
        """
        if not any(b["url"] == url for b in self.bookmarks):
            self.bookmarks.append({"title": title, "url": url})
            self.save()

    def remove(self, url: str) -> None:
        """
        Removes a bookmark by matching its URL.

        Args:
            url (str): The URL to remove from the bookmarks list.
        """
        self.bookmarks = [b for b in self.bookmarks if b["url"] != url]
        self.save()

    def is_bookmarked(self, url: str) -> bool:
        """
        Checks if a specified URL is currently bookmarked.

        Args:
            url (str): The URL to check.

        Returns:
            bool: True if the URL exists in the bookmarks, False otherwise.
        """
        return any(b["url"] == url for b in self.bookmarks)


class HistoryManager:
    """
    Manages browsing and document history.
    Maintains categorized lists (PDF vs Web) of recently accessed items,
    enforcing a maximum limit and handling persistence. Also provides
    data for autocomplete models.
    """

    def __init__(self) -> None:
        """
        Initializes the manager, defines popular default sites, and loads history from disk.
        """
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
            "gmail.com",
            "mail.google.com",
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
        """
        Loads history from the JSON persistence file. Handles legacy format migration and corrupted states.
        """
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
        """
        Saves current history state to the JSON persistence file. Silently ignores file system errors.
        """
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2)
        except OSError:
            pass

    def add(self, item: str, item_type: str = "web") -> None:
        """
        Adds an item to history, promoting it to the top of the specified category, and enforces size limits.

        Args:
            item (str): The URL or file path.
            item_type (str): The category type, typically 'pdf' or 'web'. Defaults to 'web'.
        """
        if item_type not in self.history:
            self.history[item_type] = []
        if item in self.history[item_type]:
            self.history[item_type].remove(item)
        self.history[item_type].insert(0, item)
        self.history[item_type] = self.history[item_type][:500]
        self.save()

    def get_model_data(self) -> List[str]:
        """
        Combines historical web paths and popular default sites for autocomplete suggestions.

        Returns:
            List[str]: A combined list of unique historical URLs and predefined popular sites.
        """
        combined = list(self.history.get("web", []))
        for site in self.popular_sites:
            if site not in combined:
                combined.append(site)
        return combined

    def get_list(self, item_type: str) -> List[str]:
        """
        Retrieves the history list for a specific category.

        Args:
            item_type (str): The history category, such as 'pdf' or 'web'.

        Returns:
            List[str]: The list of historical items for the specified category.
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
        Initializes the Download Manager UI components and loads persistent history.

        Args:
            parent (Optional[QWidget]): The parent widget (usually the main window).
        """
        super().__init__(parent)
        self.setWindowTitle("Downloads")
        self.resize(700, 400)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)

        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self._persist_path = os.path.join(base, "downloads.json")

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Path", "Controls"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_clear = QPushButton("Clear Completed")
        btn_clear.clicked.connect(self._cleanup_completed)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_clear)
        layout.addLayout(btn_layout)

        self.downloads: List[Dict[str, Any]] = []
        self._load_persistent_entries()

    def add_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """
        Registers a new active download request, creates its UI row, and binds signal handlers.

        Args:
            download_item (QWebEngineDownloadRequest): The QtWebEngine download request.
        """
        row = self.table.rowCount()
        self.table.insertRow(row)

        file_name = download_item.downloadFileName()
        self.table.setItem(row, 0, QTableWidgetItem(file_name))
        self.table.setItem(row, 1, QTableWidgetItem("Starting..."))

        path_item = QTableWidgetItem(download_item.downloadDirectory())
        path_item.setToolTip(download_item.downloadDirectory())
        self.table.setItem(row, 2, path_item)

        ctrl_widget = QWidget()
        h_layout = QHBoxLayout(ctrl_widget)
        h_layout.setContentsMargins(2, 2, 2, 2)
        h_layout.setSpacing(4)

        btn_pause = QPushButton("⏸")
        btn_pause.setFixedWidth(30)
        btn_cancel = QPushButton("⏹")
        btn_cancel.setFixedWidth(30)

        h_layout.addWidget(btn_pause)
        h_layout.addWidget(btn_cancel)
        self.table.setCellWidget(row, 3, ctrl_widget)

        state_slot = self._make_state_slot(row, download_item)
        finished_slot = self._make_finished_slot(row, download_item)

        download_item.stateChanged.connect(state_slot)
        download_item.finished.connect(finished_slot)

        def toggle_pause() -> None:
            if download_item.isPaused():
                download_item.resume()
                btn_pause.setText("⏸")
            else:
                download_item.pause()
                btn_pause.setText("▶")

        btn_pause.clicked.connect(toggle_pause)
        btn_cancel.clicked.connect(download_item.cancel)

        self.downloads.append(
            {
                "item": download_item,
                "state_slot": state_slot,
                "finished_slot": finished_slot,
            }
        )
        self.show()

    def update_status(
        self,
        row: int,
        item: QWebEngineDownloadRequest,
        state: QWebEngineDownloadRequest.DownloadState,
    ) -> None:
        """
        Updates the table row based on the current state of the download request.

        Args:
            row (int): The table row index corresponding to the download.
            item (QWebEngineDownloadRequest): The active download request object.
            state (QWebEngineDownloadRequest.DownloadState): The current state of the download.
        """
        if row >= self.table.rowCount():
            return

        status_str = "Unknown"
        if state == QWebEngineDownloadRequest.DownloadState.DownloadInProgress:
            total = item.totalBytes()
            recv = item.receivedBytes()
            if total > 0:
                pct = int((recv / total) * 100)
                status_str = f"Downloading {pct}%"
            else:
                status_str = f"Downloading ({recv} B)"
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            status_str = "Completed"
            full_path = os.path.join(item.downloadDirectory(), item.downloadFileName())
            self._set_open_button(row, full_path)
            self._persist_entries()
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            status_str = "Cancelled"
            self.table.setCellWidget(row, 3, QWidget())
            self._persist_entries()
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
            status_str = "Failed"
            self.table.setCellWidget(row, 3, QWidget())
            self._persist_entries()

        status_item = self.table.item(row, 1)
        if status_item:
            status_item.setText(status_str)

    def _cleanup_completed(self) -> None:
        """
        Iterates through the table and removes any rows representing completed, cancelled, or failed downloads.
        """
        rows_to_remove: List[int] = []
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
        """
        Replaces the pause/cancel control buttons with an 'Open' button upon download completion.

        Args:
            row (int): The table row index.
            full_path (str): The absolute file system path to the downloaded file.
        """
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
        """
        Creates a closure to safely handle state change signals for a specific table row.

        Args:
            row (int): The associated table row index.
            item (QWebEngineDownloadRequest): The download request object.

        Returns:
            Callable[[Any], None]: The generated slot function.
        """

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
        """
        Creates a closure to safely handle completion signals for a specific table row.

        Args:
            row (int): The associated table row index.
            item (QWebEngineDownloadRequest): The download request object.

        Returns:
            Callable[[], None]: The generated slot function.
        """

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
        """
        Handles the final state update when a download finishes execution.

        Args:
            row (int): The table row index.
            item (QWebEngineDownloadRequest): The completed download request object.
        """
        try:
            self.update_status(row, item, item.state())
        except Exception:
            pass

    def _load_persistent_entries(self) -> None:
        """
        Reads download history from disk and populates the table with historical entries.
        """
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
        """
        Extracts current table state and writes the download history to disk.
        """
        out: List[Dict[str, str]] = []
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
        """
        Cleans up Qt signal connections and guarantees the current state is saved before closing the dialog.

        Args:
            event (Any): The window close event generated by the system.
        """
        for d in list(self.downloads):
            try:
                d["item"].stateChanged.disconnect(d.get("state_slot"))
                d["item"].finished.disconnect(d.get("finished_slot"))
            except Exception:
                pass

        self._persist_entries()
        super().closeEvent(event)
