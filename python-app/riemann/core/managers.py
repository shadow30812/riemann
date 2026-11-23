"""
Core data managers for the Riemann application.

Provides persistent data handling for the library metadata, bookmarks,
browsing history, and active/historical downloads. Uses SQLite for
complex metadata querying and JSON for lightweight list persistence.

DownloadProgressBar draws its percentage itself, in two colours: accent_text
over the filled part and text over the empty part. A style sheet gives a
progress bar one text colour, and no colour reads well on both the accent fill
and the empty bar. The fill is accent_hover, the stronger accent shade (darker
in the light theme, lighter in the dark one): white on the light accent is
only 4.6:1, so antialiased 10px text dropped below 4.5:1 on some machines.
The yt-dlp window takes its theme from the main window's
dark_mode (the theme is the window's style sheet, not the application palette)
and is restyled by apply_theme when the interface theme changes.
"""

import hashlib
import json
import math
import os
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

from PySide6.QtCore import QRect, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ui.palette import app_is_dark, dialog_icon, tokens
from .annotation_store import pdf_identifier
from .log import log
from .settings import DEFAULT_POPULAR_SITES, POPULAR_SITES_KEY, string_list
from .storage import app_data_dir, write_json_atomic


class LibraryManager:
    """
    Manages persistent metadata for the local PDF library.
    Backed by a SQLite database stored in the user's application data directory.
    """

    def __init__(self) -> None:
        """
        Initializes the library manager and ensures the persistent database exists.
        """
        base = app_data_dir()
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
        The library key for a file: "id-<hex>" from a PDF's permanent /ID, which
        survives moves and renames, else a SHA-256 of the path (the only key
        versions up to 5.2 used).
        """
        ident = pdf_identifier(file_path) if file_path.lower().endswith(".pdf") else None
        if ident:
            return f"id-{ident}"
        return hashlib.sha256(file_path.encode("utf-8")).hexdigest()

    def get_metadata(self, file_path: str) -> dict[str, Any]:
        """
        The stored metadata for a file, or {} when there is none. A row saved
        under the old path key is moved to the file's /ID key on first read, and
        a row found by /ID after the file moved gets the new path.
        """
        file_hash = self.get_file_hash(file_path)
        legacy = hashlib.sha256(file_path.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM metadata WHERE file_hash = ?", (file_hash,))
            row = cur.fetchone()
            if row is None and file_hash != legacy:
                cur.execute("SELECT * FROM metadata WHERE file_hash = ?", (legacy,))
                row = cur.fetchone()
                if row is not None:
                    conn.execute("UPDATE metadata SET file_hash = ? WHERE file_hash = ?", (file_hash, legacy))
            if row is None:
                return {}
            data = dict(row)
            data["file_hash"] = file_hash
            if data.get("file_path") != file_path:
                conn.execute("UPDATE metadata SET file_path = ? WHERE file_hash = ?", (file_path, file_hash))
                data["file_path"] = file_path
            return data

    def save_metadata(self, file_path: str, data: dict[str, Any]) -> None:
        """Stores metadata for file_path; keys include title, authors, year, doi and arxiv_id."""
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

    def search_library(self, query: str) -> list[dict[str, Any]]:
        """Searches the library metadata. Supports author: and year: filters."""
        import re

        author_match = re.search(r'author:\s*"?([^"\s]+)"?', query, re.IGNORECASE)
        year_match = re.search(r"year:\s*(\d{4})", query, re.IGNORECASE)

        conditions: list[str] = []
        params: list[str] = []

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
        base = app_data_dir()
        self.path = os.path.join(base, "bookmarks.json")
        self.bookmarks: list[dict[str, str]] = []
        self.load()

    def load(self) -> None:
        """
        Loads bookmarks from the JSON persistence file. If the file is missing or corrupted, initializes an empty list.
        """
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.bookmarks = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.bookmarks = []

    def save(self) -> None:
        """
        Saves the current list of bookmarks to the JSON persistence file. Silently ignores file system errors.
        """
        try:
            write_json_atomic(self.path, self.bookmarks, indent=2)
        except OSError:
            pass

    def add(self, title: str, url: str) -> None:
        """Adds a bookmark unless the URL is already bookmarked."""
        if not any(b["url"] == url for b in self.bookmarks):
            self.bookmarks.append({"title": title, "url": url})
            self.save()

    def remove(self, url: str) -> None:
        self.bookmarks = [b for b in self.bookmarks if b["url"] != url]
        self.save()

    def is_bookmarked(self, url: str) -> bool:
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
        Initializes the manager, reads the autocomplete sites from settings, and loads history from disk.
        """
        base = app_data_dir()
        self.path = os.path.join(base, "history.json")
        self.history: dict[str, list[dict[str, str]]] = {"pdf": [], "web": [], "folder": []}
        self.popular_sites: list[str] = string_list(POPULAR_SITES_KEY, DEFAULT_POPULAR_SITES)
        self.load()

    def load(self) -> None:
        """
        Loads history from the JSON persistence file. Handles legacy format migration and corrupted states.
        """
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    data = {"web": data}
                if isinstance(data, dict):
                    self.history = {
                        kind: [e for e in map(self._entry, data.get(kind, [])) if e]
                        for kind in ("pdf", "web", "folder")
                    }
            except (json.JSONDecodeError, OSError):
                self.history = {"pdf": [], "web": [], "folder": []}

    @staticmethod
    def _entry(value) -> dict[str, str] | None:
        """Normalises a stored item; older versions kept bare paths without a time."""
        if isinstance(value, dict) and value.get("path"):
            return {"path": str(value["path"]), "timestamp": str(value.get("timestamp", ""))}
        if isinstance(value, str) and value:
            return {"path": value, "timestamp": ""}
        return None

    def save(self) -> None:
        """
        Saves current history state to the JSON persistence file. Silently ignores file system errors.
        """
        try:
            write_json_atomic(self.path, self.history, indent=2)
        except OSError:
            pass

    def add(self, item: str, item_type: str = "web") -> None:
        """
        Adds item (a URL or file path) to the top of its category ('pdf', 'web' or
        'folder') with the current time, and trims the category to its size limit.
        """
        entries = [e for e in self.history.setdefault(item_type, []) if e["path"] != item]
        entries.insert(0, {"path": item, "timestamp": datetime.now().isoformat(timespec="minutes")})
        self.history[item_type] = entries[:500]
        self.save()

    def get_model_data(self) -> list[str]:
        """Web history plus the default popular sites, for address-bar completion."""
        combined = self.get_list("web")
        for site in self.popular_sites:
            if site not in combined:
                combined.append(site)
        return combined

    def get_list(self, item_type: str) -> list[str]:
        """The paths or URLs of a category ('pdf', 'web' or 'folder'), newest first."""
        return [e["path"] for e in self.history.get(item_type, [])]

    def get_entries(self, item_type: str) -> list[dict[str, str]]:
        """
        Retrieves a category's entries, newest first, as dicts with 'path' and
        'timestamp' (ISO 8601, empty for items recorded by older versions).
        """
        return list(self.history.get(item_type, []))


class DownloadManager(QDialog):
    """
    A non-modal dialog for managing file downloads.
    Displays active, completed, and failed downloads in a table.
    Supports pausing, resuming, and cancelling active downloads.
    Persists download history across sessions.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Downloads")
        self.resize(700, 400)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)

        base = app_data_dir()
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

        self.downloads: list[dict[str, Any]] = []
        self._load_persistent_entries()

    def add_download(self, download_item: QWebEngineDownloadRequest) -> None:
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
        rows_to_remove: list[int] = []
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
        """Replaces the pause and cancel buttons of a finished download with Open."""
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
        def _slot(state: QWebEngineDownloadRequest.DownloadState) -> None:
            try:
                self.update_status(row, item, state)
            except RuntimeError:
                try:
                    item.stateChanged.disconnect(_slot)
                except Exception:
                    log.debug("_slot failed", exc_info=True)

        return _slot

    def _make_finished_slot(
        self, row: int, item: QWebEngineDownloadRequest
    ) -> Callable[[], None]:
        def _slot() -> None:
            try:
                self.on_finished(row, item)
            except RuntimeError:
                try:
                    item.finished.disconnect(_slot)
                except Exception:
                    log.debug("_slot failed", exc_info=True)

        return _slot

    def on_finished(self, row: int, item: QWebEngineDownloadRequest) -> None:
        try:
            self.update_status(row, item, item.state())
        except Exception:
            log.debug("on_finished failed", exc_info=True)

    def _load_persistent_entries(self) -> None:
        """
        Reads download history from disk and populates the table with historical entries.
        """
        if not os.path.exists(self._persist_path):
            return

        try:
            with open(self._persist_path, encoding="utf-8") as f:
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
        out: list[dict[str, str]] = []
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
            write_json_atomic(self._persist_path, out, indent=2)
        except OSError:
            pass

    def closeEvent(self, event: Any) -> None:
        """Disconnects the download signals and saves the list."""
        for d in list(self.downloads):
            try:
                d["item"].stateChanged.disconnect(d.get("state_slot"))
                d["item"].finished.disconnect(d.get("finished_slot"))
            except Exception:
                log.debug("closeEvent failed", exc_info=True)

        self._persist_entries()
        super().closeEvent(event)


class PasswordDialog(QDialog):
    """
    A custom dialog for prompting the user to enter a password to unlock a secure PDF.

    This dialog features a modern dark-mode stylesheet, hides the default context
    help button, and supports displaying an optional inline error message if a
    previous password attempt failed.
    """

    def __init__(self, parent=None, error_msg: str | None = None):
        """
        error_msg, if given, is shown in red above the field (after a wrong password,
        for example).
        """
        super().__init__(parent)
        self.setWindowTitle("Unlock Secure PDF")

        self.setMinimumWidth(350)
        self.setMinimumHeight(160)

        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)
        dialog_icon(self, "lock-keyhole")
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        self.lbl_instruction = QLabel(
            "This document is encrypted.\nPlease enter the password to open it:"
        )
        self.lbl_instruction.setWordWrap(True)
        layout.addWidget(self.lbl_instruction)

        if error_msg:
            self.lbl_error = QLabel(error_msg)
            self.lbl_error.setStyleSheet(
                f"color: {tokens(app_is_dark())['danger']}; font-weight: bold; font-size: 13px;"
            )
            layout.addWidget(self.lbl_error)

        self.txt_password = QLineEdit(self)
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Enter password...")
        layout.addWidget(self.txt_password)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)


    def get_password(self) -> str:
        return self.txt_password.text()


class DownloadProgressBar(QProgressBar):
    def __init__(self, dark: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)
        self.set_dark(dark)

    def set_dark(self, dark: bool) -> None:
        self._tokens = tokens(dark)
        t = self._tokens
        self.setStyleSheet(
            f"QProgressBar {{ border: 1px solid {t['border_strong']}; border-radius: 3px;"
            f" background-color: {t['raised']}; }}"
            f"QProgressBar::chunk {{ background-color: {t['accent_hover']}; }}"
        )
        self.update()

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        span = self.maximum() - self.minimum()
        done = (self.value() - self.minimum()) / span if span > 0 else 0.0
        rect = self.rect()
        split = rect.left() + round(rect.width() * max(0.0, min(1.0, done)))
        painter = QPainter(self)
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        text = f"{self.value()}%"
        for clip, colour in (
            (QRect(rect.left(), rect.top(), split - rect.left(), rect.height()), self._tokens["accent_text"]),
            (QRect(split, rect.top(), rect.right() - split + 1, rect.height()), self._tokens["text"]),
        ):
            painter.setClipRect(clip)
            painter.setPen(QColor(colour))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class YtDlpDownloadManager(QDialog):
    """
    A dedicated dialog for managing yt-dlp downloads.
    Uses a dynamic QTreeWidget to nest Playlists -> Videos -> Individual File Stream Parts.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("yt-dlp Downloads")
        self.resize(900, 450)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            [
                "Title / Filename",
                "Progress",
                "Size",
                "Speed & ETA",
                "Status",
                "Controls",
            ]
        )

        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 150)
        self.tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.header().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.header().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )

        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree)

        btn_layout = QHBoxLayout()
        btn_clear = QPushButton("Clear Completed")
        btn_clear.clicked.connect(self._cleanup_completed)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_clear)
        layout.addLayout(btn_layout)

        self.tasks = []

    def format_size(self, bytes_val: float) -> str:
        if not bytes_val or bytes_val <= 0:
            return "0 B"
        sizes = ["B", "KB", "MB", "GB", "TB"]
        i = math.floor(math.log(bytes_val, 1024))
        p = math.pow(1024, i)
        s = round(bytes_val / p, 2)
        return f"{s} {sizes[i]}"

    def format_time(self, seconds: float) -> str:
        if not seconds or seconds < 0:
            return "00:00"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _create_progress_bar(self, height=14) -> QProgressBar:
        pb = DownloadProgressBar(getattr(self.parentWidget(), "dark_mode", app_is_dark()))
        pb.setValue(0)
        pb.setFixedHeight(height)
        return pb

    def apply_theme(self, dark: bool) -> None:
        for bar in self.findChildren(DownloadProgressBar):
            bar.set_dark(dark)

    def add_download(
        self, worker, title: str, dest_dir: str, is_playlist: bool
    ) -> None:
        root_item = QTreeWidgetItem(self.tree)
        root_item.setText(0, ("📂 Playlist: " if is_playlist else "🎬 Task: ") + title)
        root_item.setExpanded(True)

        root_prog = self._create_progress_bar(16)
        self.tree.setItemWidget(root_item, 1, root_prog)

        root_item.setText(2, "Calculating...")
        root_item.setText(4, "Initializing")

        ctrl_widget = QWidget()
        h_layout = QHBoxLayout(ctrl_widget)
        h_layout.setContentsMargins(2, 2, 2, 2)
        btn_cancel = QPushButton("⏹ Cancel")
        btn_cancel.clicked.connect(lambda: self.cancel(worker))
        h_layout.addWidget(btn_cancel)
        self.tree.setItemWidget(root_item, 5, ctrl_widget)

        task_info = {
            "worker": worker,
            "root_item": root_item,
            "root_prog": root_prog,
            "dest_dir": dest_dir,
            "videos": {},
            "is_finished": False,
        }
        self.tasks.append(task_info)

        worker.progress_details.connect(
            lambda data, w=worker: self.update_progress(w, data)
        )
        worker.done.connect(
            lambda success, msg, w=worker: self.on_finished(w, success, msg)
        )

        self.show()
        self.raise_()

    def cancel(self, worker) -> None:
        """Stops a download and shows at once that it is stopping; the worker ends at yt-dlp's next step."""
        worker.stop()
        task = next((t for t in self.tasks if t["worker"] == worker), None)
        if not task or task["is_finished"]:
            return
        task["root_item"].setText(4, "Cancelling…")
        ctrl_widget = self.tree.itemWidget(task["root_item"], 5)
        for btn in ctrl_widget.findChildren(QPushButton) if ctrl_widget else []:
            btn.setEnabled(False)

    def update_progress(self, worker, data: dict) -> None:
        task = next((t for t in self.tasks if t["worker"] == worker), None)
        if not task or getattr(worker, "is_cancelled", False):
            return

        vid_id = data.get("video_id", "unknown_id")
        vid_title = data.get("title", "Unknown Title")
        filename = data.get("filename", "Unknown_Part")
        percent = data.get("percentage", 0)

        if vid_id not in task["videos"]:
            vid_item = QTreeWidgetItem(task["root_item"])
            vid_item.setText(0, "↳ " + vid_title)
            vid_item.setExpanded(True)
            task["videos"][vid_id] = {"item": vid_item, "parts": {}}

        vid_data = task["videos"][vid_id]

        if filename not in vid_data["parts"]:
            part_item = QTreeWidgetItem(vid_data["item"])
            part_item.setText(0, "📄 " + os.path.basename(filename))
            part_prog = self._create_progress_bar(12)
            self.tree.setItemWidget(part_item, 1, part_prog)
            vid_data["parts"][filename] = {"item": part_item, "prog": part_prog}

        part_data = vid_data["parts"][filename]
        part_item = part_data["item"]
        part_prog = part_data["prog"]
        part_prog.setValue(percent)

        dl_bytes = data.get("downloaded_bytes", 0)
        tot_bytes = data.get("total_bytes", 0)
        size_str = (
            f"{self.format_size(dl_bytes)} / {self.format_size(tot_bytes)}"
            if tot_bytes
            else self.format_size(dl_bytes)
        )

        part_item.setText(2, size_str)
        speed = data.get("speed", 0)
        eta = data.get("eta", 0)
        part_item.setText(
            3,
            f"{self.format_size(speed)}/s - {self.format_time(eta)}"
            if speed
            else "Processing...",
        )

        status = data.get("status", "Unknown")
        p_idx = data.get("playlist_index")
        p_count = data.get("playlist_count")

        if status == "finished":
            part_item.setText(4, "Merged / Done")
            part_prog.setValue(100)
        else:
            part_item.setText(4, "Downloading")
            part_prog.setValue(percent)

        if p_idx and p_count and p_count > 0:
            task["root_item"].setText(4, f"Video {p_idx} of {p_count}")
            completed_fraction = (p_idx - 1) + (1.0 if status == "finished" else percent / 100.0)
            overall_pct = int((completed_fraction / p_count) * 100)
            task["root_prog"].setValue(min(100, max(0, overall_pct)))
        else:
            if status == "finished":
                task["root_prog"].setValue(100)
            else:
                task["root_prog"].setValue(percent)

    def on_finished(self, worker, success: bool, msg: str) -> None:
        task = next((t for t in self.tasks if t["worker"] == worker), None)
        if not task:
            return

        task["is_finished"] = True
        root_item = task["root_item"]

        if success:
            task["root_prog"].setValue(100)
            root_item.setText(3, "Completed")
            root_item.setText(4, "Done")
        else:
            task["root_prog"].setValue(0)
            root_item.setText(3, "Cancelled / Failed")
            root_item.setText(4, msg)

        ctrl_widget = self.tree.itemWidget(root_item, 5)
        if ctrl_widget:
            h_layout = ctrl_widget.layout()
            for i in reversed(range(h_layout.count())):
                item = h_layout.itemAt(i).widget()
                if item:
                    item.setParent(None)

            btn_open = QPushButton("📂 Open Folder")
            btn_open.clicked.connect(
                lambda _, path=task["dest_dir"]: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(path)
                )
            )
            h_layout.addWidget(btn_open)

    def _cleanup_completed(self) -> None:
        tasks_to_remove = [
            task for task in self.tasks if task.get("is_finished", False)
        ]
        for task in tasks_to_remove:
            idx = self.tree.indexOfTopLevelItem(task["root_item"])
            if idx != -1:
                self.tree.takeTopLevelItem(idx)

        self.tasks = [t for t in self.tasks if not t.get("is_finished", False)]
