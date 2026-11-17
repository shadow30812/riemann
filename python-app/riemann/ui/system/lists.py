"""
The History, Bookmarks, Downloads and Library tabs.

History and Bookmarks read the window's managers each time the tab is shown,
so they never go stale while open. Downloads hosts the window's two download
managers (files and videos): they are long-lived dialogs that keep track of
running downloads, so the tab borrows them while it is open and hands them
back to the window in release(). Library searches the metadata Riemann keeps
for papers it has opened, exports them as BibTeX or CSL-JSON, and can search
inside all of them by meaning through the AI engine (MeaningSearch, a worker
thread, since the first search reads every paper).
"""

import os
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import ai_engine
from ...core.bibliography import to_bibtex, to_csl_json
from ..threads import release as release_thread
from ..threads import track
from .base import SystemTab


def when(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return timestamp or "—"


def _toolbar(layout: QVBoxLayout, placeholder: str) -> tuple[QLineEdit, QHBoxLayout]:
    row = QHBoxLayout()
    search = QLineEdit()
    search.setPlaceholderText(placeholder)
    search.setClearButtonEnabled(True)
    row.addWidget(search, 1)
    layout.addLayout(row)
    return search, row


def _body_layout(body: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(body)
    layout.setContentsMargins(28, 18, 28, 24)
    layout.setSpacing(12)
    return layout


class HistoryTab(SystemTab):
    page = "history"
    title = "History"
    subtitle = "Web pages, documents and folders you opened, newest first."
    icon_name = "clock-history"

    KINDS = (("web", "Web pages"), ("pdf", "Documents"), ("folder", "Folders"))

    def build(self, body: QWidget) -> None:
        layout = _body_layout(body)
        self.search, row = _toolbar(layout, "Search history")
        self.btn_open = QPushButton("Open")
        self.btn_remove = QPushButton("Remove")
        self.btn_clear = QPushButton("Clear this list")
        for b in (self.btn_open, self.btn_remove, self.btn_clear):
            row.addWidget(b)
        self.tabs = QTabWidget()
        self.trees: dict[str, QTreeWidget] = {}
        for kind, label in self.KINDS:
            tree = QTreeWidget()
            tree.setRootIsDecorated(False)
            tree.setAlternatingRowColors(True)
            tree.setUniformRowHeights(True)
            tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
            tree.itemDoubleClicked.connect(lambda *_: self.open_selected())
            self.trees[kind] = tree
            self.tabs.addTab(tree, label)
        layout.addWidget(self.tabs, 1)
        self.search.textChanged.connect(self._filter)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear_current)
        self.refresh()

    def _kind(self) -> str:
        return self.KINDS[self.tabs.currentIndex()][0]

    def refresh(self) -> None:
        manager = getattr(self.window_ref, "history_manager", None)
        for kind, _ in self.KINDS:
            tree = self.trees[kind]
            headers = ["Address", "Last opened"] if kind == "web" else ["Name", "Folder", "Last opened"]
            tree.clear()
            tree.setHeaderLabels(headers)
            for entry in manager.get_entries(kind) if manager else []:
                path = entry["path"]
                if kind == "web":
                    columns = [path]
                else:
                    trimmed = path.rstrip("/\\") or path
                    columns = [os.path.basename(trimmed) or trimmed, os.path.dirname(trimmed)]
                item = QTreeWidgetItem(columns + [when(entry.get("timestamp", ""))])
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setToolTip(0, path)
                tree.addTopLevelItem(item)
            header = tree.header()
            header.setStretchLastSection(False)
            stretch = 0 if kind == "web" else 1
            for col in range(len(headers)):
                header.setSectionResizeMode(
                    col,
                    QHeaderView.ResizeMode.Stretch if col == stretch else QHeaderView.ResizeMode.ResizeToContents,
                )
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for tree in self.trees.values():
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                hay = str(item.data(0, Qt.ItemDataRole.UserRole)).lower()
                item.setHidden(bool(needle) and needle not in hay)

    def open_selected(self) -> None:
        kind = self._kind()
        win = self.window_ref
        for item in self.trees[kind].selectedItems():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if kind == "web":
                win.new_browser_tab(path)
            elif kind == "folder":
                win.open_folder(path)
            elif os.path.exists(path):
                win._open_document(path, win.tabs_main)
            else:
                win.show_toast("That file no longer exists.")

    def remove_selected(self) -> None:
        kind = self._kind()
        manager = self.window_ref.history_manager
        doomed = {item.data(0, Qt.ItemDataRole.UserRole) for item in self.trees[kind].selectedItems()}
        if not doomed:
            return
        manager.history[kind] = [e for e in manager.history.get(kind, []) if e.get("path") not in doomed]
        manager.save()
        self._sync_autocomplete()
        self.refresh()

    def clear_current(self) -> None:
        manager = self.window_ref.history_manager
        manager.history[self._kind()] = []
        manager.save()
        self._sync_autocomplete()
        self.refresh()

    def _sync_autocomplete(self) -> None:
        model = getattr(self.window_ref, "history_model", None)
        if model is not None:
            model.setStringList(self.window_ref.history_manager.get_model_data())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()


class BookmarksTab(SystemTab):
    page = "bookmarks"
    title = "Bookmarks"
    subtitle = "Pages you bookmarked from the address bar."
    icon_name = "bookmark"

    def build(self, body: QWidget) -> None:
        layout = _body_layout(body)
        self.search, row = _toolbar(layout, "Search bookmarks")
        self.btn_open = QPushButton("Open")
        self.btn_remove = QPushButton("Remove")
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_remove)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Title", "Address"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.table, 1)
        self.empty = QLabel("No bookmarks yet. Use the star in a web tab's address bar to add one.")
        self.empty.setObjectName("hint")
        layout.addWidget(self.empty)
        self.search.textChanged.connect(self._filter)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.refresh()

    def refresh(self) -> None:
        manager = getattr(self.window_ref, "bookmarks_manager", None)
        marks = list(getattr(manager, "bookmarks", []) or [])
        self.table.setRowCount(0)
        for bm in marks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            title = QTableWidgetItem(str(bm.get("title", "")))
            title.setData(Qt.ItemDataRole.UserRole, bm.get("url", ""))
            self.table.setItem(row, 0, title)
            self.table.setItem(row, 1, QTableWidgetItem(str(bm.get("url", ""))))
        self.empty.setVisible(not marks)
        self._filter(self.search.text())

    def _selected_urls(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows]

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            hay = (self.table.item(row, 0).text() + " " + self.table.item(row, 1).text()).lower()
            self.table.setRowHidden(row, bool(needle) and needle not in hay)

    def open_selected(self) -> None:
        for url in self._selected_urls():
            self.window_ref.new_browser_tab(url)

    def remove_selected(self) -> None:
        manager = self.window_ref.bookmarks_manager
        for url in self._selected_urls():
            manager.remove(url)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()


class DownloadsTab(SystemTab):
    page = "downloads"
    title = "Downloads"
    subtitle = "Files saved from web pages, and videos downloaded with yt-dlp."
    icon_name = "download"

    def build(self, body: QWidget) -> None:
        layout = _body_layout(body)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._borrowed = []
        for attr, label in (("download_manager_dialog", "Files"), ("ytdlp_manager_dialog", "Videos")):
            dialog = getattr(self.window_ref, attr, None)
            holder = QWidget()
            hold = QVBoxLayout(holder)
            hold.setContentsMargins(0, 8, 0, 0)
            if dialog is not None:
                self._borrowed.append((dialog, dialog.parentWidget(), dialog.windowFlags()))
                dialog.setParent(holder, Qt.WindowType.Widget)
                hold.addWidget(dialog)
                dialog.show()
            self.tabs.addTab(holder, label)

    def show_section(self, key: str) -> None:
        self.tabs.setCurrentIndex(1 if key == "videos" else 0)

    def release(self) -> None:
        for dialog, parent, flags in self._borrowed:
            dialog.hide()
            dialog.setParent(parent, flags)
        self._borrowed = []


class MeaningSearch(QThread):
    """Runs a library-wide AI search off the UI thread."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, query: str, paths: list) -> None:
        super().__init__()
        self.query = query
        self.paths = paths

    def run(self) -> None:
        try:
            self.done.emit(ai_engine.search_library(self.query, self.paths))
        except Exception as e:
            self.failed.emit(str(e) or type(e).__name__)


class LibraryTab(SystemTab):
    page = "library"
    title = "Library"
    subtitle = "Papers Riemann has read metadata for. Search by words, or author:Smith year:2024."
    icon_name = "book-open"

    def build(self, body: QWidget) -> None:
        layout = _body_layout(body)
        self.search, row = _toolbar(layout, "Search the library")
        self.btn_open = QPushButton("Open")
        self.btn_export = QPushButton("Export…")
        self.btn_export.setToolTip("Save these papers as BibTeX or CSL-JSON (Zotero imports CSL-JSON)")
        self.cb_meaning = QCheckBox("Search inside papers (AI)")
        self.cb_meaning.setToolTip(
            "Find passages by meaning across every paper in the library.\n"
            "Each paper is read once the first time; later searches are quick."
        )
        row.addWidget(self.cb_meaning)
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_export)
        self.results: list = []
        self.passages: list = []
        self._worker = None
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Title", "Authors", "Year", "File"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.table, 1)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        layout.addWidget(self.status)
        self.search.returnPressed.connect(self._search)
        self.cb_meaning.toggled.connect(lambda _: self._search())
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_export.clicked.connect(self.export)
        self.run_search()

    def _search(self) -> None:
        if self.cb_meaning.isChecked():
            self.run_meaning_search()
        else:
            self.run_search()

    def library_pdfs(self) -> list:
        manager = getattr(self.window_ref, "library_manager", None)
        rows = manager.search_library("") if manager else []
        paths = []
        for row in rows:
            path = str(row.get("file_path") or "")
            if path.lower().endswith(".pdf") and os.path.exists(path) and path not in paths:
                paths.append(path)
        return paths

    def run_meaning_search(self) -> None:
        query = self.search.text().strip()
        self.btn_export.setEnabled(False)
        if not query:
            self.status.setText("Type what you are looking for, then press Enter.")
            return
        paths = self.library_pdfs()
        if not paths:
            self.status.setText("The library has no PDFs on disk yet.")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.status.setText(f"Searching {len(paths)} papers… The first search reads each paper, which takes a while.")
        self._worker = MeaningSearch(query, paths)
        self._worker.done.connect(self._show_passages)
        self._worker.failed.connect(self._show_search_failure)
        track(self._worker).start()

    def _show_search_failure(self, message: str) -> None:
        self.status.setText(f"AI search failed: {message}")

    def release(self) -> None:
        if self._worker is not None:
            release_thread(self._worker, self._worker.done, self._worker.failed)
            self._worker = None

    def _show_passages(self, data: dict) -> None:
        titles = {}
        manager = getattr(self.window_ref, "library_manager", None)
        for row in manager.search_library("") if manager else []:
            titles[str(row.get("file_path"))] = str(row.get("title") or "")
        self.passages = list(data.get("results", []))
        self.table.setRowCount(0)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Paper", "Page", "Passage", "Match"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for hit in self.passages:
            row = self.table.rowCount()
            self.table.insertRow(row)
            path = hit.get("pdf_path", "")
            values = (
                titles.get(path) or os.path.basename(path),
                str(hit.get("page", "")),
                " ".join(str(hit.get("text", "")).split())[:300],
                f"{round(float(hit.get('score', 0)) * 100)}%",
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, (path, int(hit.get("page", 1) or 1)))
                if col == 2:
                    item.setToolTip(str(hit.get("text", "")))
                self.table.setItem(row, col, item)
        skipped = len(data.get("skipped", []))
        note = f" ({skipped} could not be read)" if skipped else ""
        self.status.setText(f"{len(self.passages)} passages from {data.get('searched', 0)} papers{note}.")

    def run_search(self) -> None:
        manager = getattr(self.window_ref, "library_manager", None)
        results = manager.search_library(self.search.text().strip()) if manager else []
        self.results = results
        self.passages = []
        self.btn_export.setEnabled(True)
        self.table.setRowCount(0)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Title", "Authors", "Year", "File"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        for data in results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            path = str(data.get("file_path", ""))
            for col, value in enumerate(
                (data.get("title", ""), data.get("authors", ""), data.get("year", ""), os.path.basename(path))
            ):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.ItemDataRole.UserRole, path)
                if col == 3:
                    item.setToolTip(path)
                self.table.setItem(row, col, item)
        self.status.setText(f"{len(results)} papers" if results else "Nothing found.")

    def open_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if self.passages:
            for r in rows:
                path, page = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
                if os.path.exists(path):
                    self.window_ref._open_document(path, self.window_ref.tabs_main, page=page - 1)
            return
        for r in rows:
            path = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            if path and os.path.exists(path):
                self.window_ref._open_document(path, self.window_ref.tabs_main)
            else:
                self.window_ref.show_toast("That file is no longer there.")

    def export(self) -> None:
        """Saves the papers listed (or the selected ones) as BibTeX or CSL-JSON."""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        entries = [self.results[r] for r in rows] if rows else list(self.results)
        if not entries:
            self.status.setText("Nothing to export.")
            return
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export library", "library.bib", "BibTeX (*.bib);;CSL-JSON for Zotero (*.json)"
        )
        if not path:
            return
        as_json = path.lower().endswith(".json") or "json" in chosen.lower()
        if as_json and not path.lower().endswith(".json"):
            path += ".json"
        text = to_csl_json(entries) if as_json else to_bibtex(entries)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.status.setText(f"Exported {len(entries)} papers to {path}")
