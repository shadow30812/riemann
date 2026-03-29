"""
Main Application Module.

This module defines the primary window manager (`RiemannWindow`) and the
application entry point. It orchestrates the UI layout, tab management
(split-view), global keyboard shortcuts, and session persistence.
"""

import os
import sys

# os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    bundle_dir = getattr(sys, "_MEIPASS")
    os.environ["PDFIUM_DYNAMIC_LIB_PATH"] = bundle_dir

import shutil
from pathlib import Path
from typing import List, Optional

from pypdf import PdfReader, PdfWriter
from PySide6.QtCore import (
    QEvent,
    QObject,
    QSettings,
    QStandardPaths,
    QStringListModel,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QCloseEvent,
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core.managers import (
    BookmarksManager,
    DownloadManager,
    HistoryManager,
    LibraryManager,
)
from .ui.browser import BrowserTab
from .ui.components import DraggableTabWidget
from .ui.reader import ReaderTab


def get_resource_path(relative_path: str) -> str:
    """
    Get absolute path to resource, works for dev and for PyInstaller.

    Args:
        relative_path (str): The relative path to the requested resource.

    Returns:
        str: The absolute path to the resource on the file system.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = getattr(sys, "_MEIPASS")
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


class SettingsDialog(QDialog):
    """
    A modal dialog for configuring application-wide settings.

    Attributes:
        cb_dark (QCheckBox): Checkbox to toggle dark mode.
        cb_auto_pdf (QCheckBox): Checkbox to toggle auto-opening of downloaded PDFs.
    """

    def __init__(self, parent: "RiemannWindow") -> None:
        """
        Initializes the settings dialog.

        Args:
            parent (RiemannWindow): The main window instance, used to retrieve current settings.
        """
        super().__init__(parent)
        self.parent_win = parent
        self.setWindowTitle("Settings")
        self.resize(400, 400)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.cb_auto_pdf = QCheckBox()
        self.cb_auto_pdf.setChecked(
            parent.settings.value("browser/auto_open_pdf", False, type=bool)
        )

        self.cb_dark = QCheckBox()
        self.cb_dark.setChecked(parent.dark_mode)

        self.txt_custom_name = QLineEdit()
        self.txt_custom_name.setPlaceholderText("Leave empty for OS default")
        self.txt_custom_name.setText(
            parent.settings.value("homepage/custom_name", "", type=str)
        )

        form_layout.addRow("Enable Dark Mode:", self.cb_dark)
        form_layout.addRow("Auto-open Downloaded PDFs:", self.cb_auto_pdf)
        form_layout.addRow("Homepage Greeting Name:", self.txt_custom_name)
        layout.addLayout(form_layout)

        group = QGroupBox("Data Management")
        group_layout = QVBoxLayout(group)

        btn_clear_history = QPushButton("Clear Browsing History")
        btn_clear_history.clicked.connect(self.clear_history)

        btn_clear_downloads = QPushButton("Clear Download History")
        btn_clear_downloads.clicked.connect(self.clear_downloads)

        btn_clear_cookies = QPushButton("Clear Cookies")
        btn_clear_cookies.clicked.connect(self.clear_cookies)

        btn_clear_cache = QPushButton("Clear Cache")
        btn_clear_cache.clicked.connect(self.clear_cache)

        btn_clear_all = QPushButton("Clear All Data")
        btn_clear_all.setStyleSheet("color: #d32f2f; font-weight: bold;")
        btn_clear_all.clicked.connect(self.clear_all_data)

        group_layout.addWidget(btn_clear_history)
        group_layout.addWidget(btn_clear_downloads)
        group_layout.addWidget(btn_clear_cookies)
        group_layout.addWidget(btn_clear_cache)
        group_layout.addWidget(btn_clear_all)
        layout.addWidget(group)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def clear_history(self) -> None:
        """
        Clears the web browsing history from the history manager and updates the autocomplete model.
        """
        self.parent_win.history_manager.history["web"] = []
        self.parent_win.history_manager.save()
        self.parent_win.history_model.setStringList(
            self.parent_win.history_manager.get_model_data()
        )
        QMessageBox.information(self, "Success", "Browsing history cleared.")

    def clear_downloads(self) -> None:
        """
        Clears all entries from the download manager's history interface and persists the empty state.
        """
        dl_manager = self.parent_win.download_manager_dialog
        dl_manager.table.setRowCount(0)
        dl_manager.downloads.clear()
        dl_manager._persist_entries()
        QMessageBox.information(self, "Success", "Download history cleared.")

    def clear_cookies(self) -> None:
        """
        Deletes all persistent cookies from the application's global web profile.
        """
        if not self.parent_win.incognito:
            self.parent_win.web_profile.cookieStore().deleteAllCookies()
        QMessageBox.information(self, "Success", "Cookies cleared.")

    def clear_cache(self) -> None:
        """
        Clears the HTTP network cache from the application's global web profile.
        """
        if not self.parent_win.incognito:
            self.parent_win.web_profile.clearHttpCache()
        QMessageBox.information(self, "Success", "Cache cleared.")

    def clear_all_data(self) -> None:
        """
        Prompts for user confirmation, then clears browsing history, downloads, cookies, and cache in bulk.
        """
        reply = QMessageBox.question(
            self,
            "Confirm",
            "Are you sure you want to clear all browsing data, downloads, cookies, and cache?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.parent_win.history_manager.history["web"] = []
            self.parent_win.history_manager.save()
            self.parent_win.history_model.setStringList(
                self.parent_win.history_manager.get_model_data()
            )

            dl_manager = self.parent_win.download_manager_dialog
            dl_manager.table.setRowCount(0)
            dl_manager.downloads.clear()
            dl_manager._persist_entries()

            if not self.parent_win.incognito:
                self.parent_win.web_profile.cookieStore().deleteAllCookies()
                self.parent_win.web_profile.clearHttpCache()

            QMessageBox.information(self, "Success", "All data has been cleared.")


class LibrarySearchDialog(QDialog):
    """
    A modal dialog for searching the local PDF library using keywords or tags.
    """

    def __init__(self, parent):
        """
        Initializes the library search dialog interface and layout.

        Args:
            parent (QWidget): The parent widget, expected to provide library management functionality.
        """
        super().__init__(parent)
        self.setWindowTitle("Search Library")
        self.resize(800, 500)

        layout = QVBoxLayout(self)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Search by keyword, or use tags like author:Smith year:2024"
        )
        self.search_input.returnPressed.connect(self.execute_search)
        layout.addWidget(self.search_input)

        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(
            ["Title", "Authors", "Year", "Path"]
        )
        self.results_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.results_table.itemDoubleClicked.connect(self.open_selected_pdf)
        layout.addWidget(self.results_table)

    def execute_search(self):
        """
        Executes a query against the library manager and populates the results table.
        """
        query = self.search_input.text().strip()
        results = self.parent().library_manager.search_library(query)

        self.results_table.setRowCount(0)
        for row_idx, row_data in enumerate(results):
            self.results_table.insertRow(row_idx)
            self.results_table.setItem(
                row_idx, 0, QTableWidgetItem(row_data.get("title", ""))
            )
            self.results_table.setItem(
                row_idx, 1, QTableWidgetItem(row_data.get("authors", ""))
            )
            self.results_table.setItem(
                row_idx, 2, QTableWidgetItem(row_data.get("year", ""))
            )

            path_item = QTableWidgetItem(row_data.get("file_path", ""))
            path_item.setFlags(path_item.flags() ^ Qt.ItemFlag.ItemIsEditable)
            self.results_table.setItem(row_idx, 3, path_item)

    def open_selected_pdf(self, item):
        """
        Retrieves the file path from the selected table item and triggers the parent window to open it.

        Args:
            item (QTableWidgetItem): The table item double-clicked by the user.
        """
        row = item.row()
        file_path = self.results_table.item(row, 3).text()

        if file_path:
            self.accept()
            self.parent().new_pdf_tab(file_path)


class RiemannWindow(QMainWindow):
    """
    The Main Window Manager for the Riemann application.

    Handles global state, split-view tab management, history tracking,
    shortcuts, and session persistence.
    """

    def __init__(
        self,
        incognito: bool = False,
        restore_session: bool = True,
        external_files: Optional[List[str]] = None,
    ) -> None:
        """
        Initializes the main application window.

        Args:
            incognito (bool): If True, history will not be recorded.
            restore_session (bool): If True, attempts to restore tabs from the last session.
        """
        super().__init__()
        self.incognito = incognito
        self.restore_session = restore_session
        self.external_files = external_files or []

        if self.incognito:
            self.setWindowTitle("Riemann (Incognito)")
            self.setProperty("incognito", True)
            self.web_profile = QWebEngineProfile()
        else:
            self.setWindowTitle("Riemann")
            self.web_profile = QWebEngineProfile("RiemannPersistentProfile", self)

            base_path = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            storage_path = os.path.join(base_path, "web_profile")
            os.makedirs(storage_path, exist_ok=True)

            self.web_profile.setPersistentStoragePath(storage_path)
            self.web_profile.setCachePath(storage_path)
            self.web_profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )

        self.resize(1200, 900)
        self.settings = QSettings("Riemann", "PDFReader")
        self.dark_mode: bool = self.settings.value("darkMode", True, type=bool)

        self.download_manager_dialog = DownloadManager(self)
        self.history_manager = HistoryManager()
        self.history_model = QStringListModel(self.history_manager.get_model_data())
        self.bookmarks_manager = BookmarksManager()

        self.closed_tabs_stack: List[dict] = []
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self.splitter)

        self.tabs_main = DraggableTabWidget()
        self.tabs_main.setTabsClosable(True)
        self.tabs_main.tabCloseRequested.connect(self.close_tab)
        self.tabs_main.currentChanged.connect(self._update_window_title)
        self.tabs_main.tabBar().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.tabs_main.tabBar().customContextMenuRequested.connect(
            lambda pos: self._show_tab_context_menu(pos, self.tabs_main)
        )
        self.splitter.addWidget(self.tabs_main)

        self.tabs_side = DraggableTabWidget()
        self.tabs_side.setTabsClosable(True)
        self.tabs_side.tabCloseRequested.connect(self.close_side_tab)
        self.tabs_side.currentChanged.connect(self._update_window_title)
        self.tabs_side.tabBar().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.tabs_side.tabBar().customContextMenuRequested.connect(
            lambda pos: self._show_tab_context_menu(pos, self.tabs_side)
        )
        self.tabs_side.hide()
        self.splitter.addWidget(self.tabs_side)

        self.tree_signatures = QTreeWidget()
        self.tree_signatures.setHeaderLabels(["Identity", "Details"])

        self.library_manager = LibraryManager()

        self.setup_menu()
        self._init_shortcuts()
        self._restore_session()

        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.installEventFilter(self)

        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(500)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._check_auto_hide)

        self.enforce_global_stylesheet()

    def _init_shortcuts(self) -> None:
        """
        Initializes and binds global application keyboard shortcuts.
        """
        shortcuts = [
            ("Ctrl+Q", self.close),
            ("Ctrl+W", self.close_active_tab),
            ("Ctrl+Shift+T", self.restore_last_closed_tab),
            ("Ctrl+\\", self.toggle_split_view),
            ("N", self.toggle_active_tab_theme),
            (Qt.Key.Key_F11, self.toggle_reader_fullscreen),
            (Qt.Key.Key_Escape, self._handle_escape),
            ("Ctrl+T", self.new_pdf_tab),
            ("Ctrl+B", self.new_browser_tab),
            ("Ctrl+N", self.new_window),
            ("Ctrl+Shift+N", self.new_incognito_window),
            ("Ctrl+O", self.open_pdf_smart),
            ("Ctrl+K", self.show_bookmarks),
            ("Ctrl+J", self.show_downloads),
            ("Ctrl+L", self.show_library_search),
            ("Ctrl+H", self.show_history),
            ("Ctrl+,", self.show_settings),
            ("Ctrl+D", self.toggle_ui_theme),
        ]

        for seq, slot in shortcuts:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

        self.enforce_global_stylesheet()

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """
        Filters events to handle global keyboard shortcuts and mouse hover reveals.

        Args:
            source (QObject): The object that generated the event.
            event (QEvent): The event instance.

        Returns:
            bool: True if the event was handled and should be stopped, False otherwise.
        """
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            if modifiers & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_Tab:
                    if modifiers & Qt.KeyboardModifier.ShiftModifier:
                        self.prev_tab()
                    else:
                        self.next_tab()
                    return True

            if key == Qt.Key.Key_Escape:
                if self.isFullScreen():
                    self.toggle_fullscreen()
                    return True

                return False

        if (
            getattr(self, "_reader_fullscreen", False)
            and event.type() == QEvent.Type.MouseMove
        ):
            local_pos = self.mapFromGlobal(QCursor.pos())
            if local_pos.y() < 10:
                self._reveal_controls(True)
            elif local_pos.y() > 100:
                self.hover_timer.start()

        return super().eventFilter(source, event)

    def next_tab(self):
        """
        Advances the focus to the next tab in the main tab widget, wrapping around if necessary.
        """
        idx = self.tabs_main.currentIndex()
        if idx < self.tabs_main.count() - 1:
            self.tabs_main.setCurrentIndex(idx + 1)
        else:
            self.tabs_main.setCurrentIndex(0)

    def prev_tab(self):
        """
        Reverts the focus to the previous tab in the main tab widget, wrapping around if necessary.
        """
        idx = self.tabs_main.currentIndex()
        if idx > 0:
            self.tabs_main.setCurrentIndex(idx - 1)
        else:
            self.tabs_main.setCurrentIndex(self.tabs_main.count() - 1)

    def _reveal_controls(self, show: bool):
        """
        Controls the visibility of the application menu bar and tab bars.

        Args:
            show (bool): True to make UI controls visible, False to hide them.
        """
        if show:
            self.menuBar().show()
            self.tabs_main.tabBar().show()
            if self.tabs_side.isVisible():
                self.tabs_side.tabBar().show()
        else:
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            if self.tabs_side.isVisible():
                self.tabs_side.tabBar().hide()

    def _check_auto_hide(self):
        """
        Checks the mouse cursor position to automatically hide UI controls in fullscreen reading mode.
        """
        mouse_pos = self.mapFromGlobal(QCursor.pos())
        if mouse_pos.y() > 100 and getattr(self, "_reader_fullscreen", False):
            self._reveal_controls(False)

    def _update_window_title(self, index: int = -1) -> None:
        """
        Updates the main window title to reflect the active tab.

        Args:
            index (int): The index of the active tab. Defaults to -1.
        """
        target = (
            self.tabs_side
            if (self.tabs_side.isVisible() and self.tabs_side.hasFocus())
            else self.tabs_main
        )
        idx = target.currentIndex()
        prefix = "Riemann (Incognito)" if self.incognito else "Riemann"

        if idx != -1:
            tab_title = target.tabText(idx)
            self.setWindowTitle(f"{prefix} - {tab_title}")
        else:
            self.setWindowTitle(prefix)

        target_widget = target.currentWidget()
        if target_widget:
            target_widget.setFocus()

        self.refresh_signature_panel()

    def changeEvent(self, event: QEvent) -> None:
        """
        Handles state changes, such as window activation, to restore focus to the correct tab.

        Args:
            event (QEvent): The state change event triggered by the Qt framework.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            target = (
                self.tabs_side
                if (self.tabs_side.isVisible() and self.tabs_side.hasFocus())
                else self.tabs_main
            )
            if target.currentWidget():
                target.currentWidget().setFocus()

    def split_pdf(self) -> None:
        """
        Utility to extract specific pages into a new PDF using pypdf.
        """
        current = self.tabs_main.currentWidget()
        source_path = ""
        if isinstance(current, ReaderTab) and current.current_path:
            source_path = current.current_path
        else:
            source_path, _ = QFileDialog.getOpenFileName(
                self, "Select PDF to Split", "", "PDF Files (*.pdf)"
            )
        if not source_path:
            return

        pages_str, ok = QInputDialog.getText(
            self, "Split PDF", "Enter page ranges to extract (e.g., 1-5, 8, 11-15):"
        )
        if not ok or not pages_str.strip():
            return

        dest_path, _ = QFileDialog.getSaveFileName(
            self, "Save Split PDF As", "split.pdf", "PDF Files (*.pdf)"
        )
        if not dest_path:
            return

        try:
            reader = PdfReader(source_path)
            writer = PdfWriter()
            max_idx = len(reader.pages) - 1

            for part in pages_str.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    start_idx = min(max(0, start - 1), max_idx)
                    end_idx = min(max(0, end - 1), max_idx)
                    if start_idx <= end_idx:
                        for p_idx in range(start_idx, end_idx + 1):
                            writer.add_page(reader.pages[p_idx])
                else:
                    page_idx = int(part) - 1
                    if 0 <= page_idx <= max_idx:
                        writer.add_page(reader.pages[page_idx])

            with open(dest_path, "wb") as f_out:
                writer.write(f_out)

            if (
                QMessageBox.question(
                    self, "Success", f"Saved to {dest_path}.\nOpen in new tab?"
                )
                == QMessageBox.StandardButton.Yes
            ):
                self.new_pdf_tab(dest_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to split PDF: {e}")

    def join_pdfs(self) -> None:
        """
        Utility to merge multiple PDFs into one using pypdf.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs to Merge", "", "PDF Files (*.pdf)"
        )
        if not paths or len(paths) < 2:
            if paths:
                QMessageBox.warning(
                    self, "Merge PDFs", "Please select at least two PDF files."
                )
            return

        dest_path, _ = QFileDialog.getSaveFileName(
            self, "Save Merged PDF As", "merged.pdf", "PDF Files (*.pdf)"
        )
        if not dest_path:
            return

        paths.sort()
        try:
            writer = PdfWriter()
            for path in paths:
                reader = PdfReader(path)
                for page in reader.pages:
                    writer.add_page(page)

            with open(dest_path, "wb") as f_out:
                writer.write(f_out)

            if (
                QMessageBox.question(
                    self,
                    "Success",
                    f"Merged PDF saved to {dest_path}.\nOpen in new tab?",
                )
                == QMessageBox.StandardButton.Yes
            ):
                self.new_pdf_tab(dest_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to merge PDFs: {e}")

    def add_to_history(self, item: str, item_type: str = "web") -> None:
        """
        Adds an item to the history manager and updates the autocomplete model.

        Args:
            item (str): The URL or file path to add.
            item_type (str): The type of item ("web" or "pdf").
        """
        if self.incognito:
            return

        self.history_manager.add(item, item_type)
        self.history_model.setStringList(self.history_manager.get_model_data())

    def _restore_session(self) -> None:
        """
        Restores the window geometry and open tabs from the previous session.
        Defaults to opening both PDF and browser homepages if no session exists or incognito is active
        UNLESS app is opened externally.
        """
        if self.incognito or not self.restore_session:
            if not self.external_files:
                self.new_pdf_tab()
                self.new_browser_tab()
            self.resize(1200, 900)
            return

        if self.settings.value("window/geometry"):
            self.restoreGeometry(self.settings.value("window/geometry"))  # type: ignore

        self._restore_tabs_from_settings("session/main_tabs", self.tabs_main)
        self._restore_tabs_from_settings("session/side_tabs", self.tabs_side)

        if self.tabs_side.count() > 0:
            self.tabs_side.show()
            if self.settings.value("splitter/state"):
                self.splitter.restoreState(self.settings.value("splitter/state"))  # type: ignore
        else:
            self.tabs_side.hide()

        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if not self.external_files:
                self.new_pdf_tab()
                self.new_browser_tab()

    def _restore_tabs_from_settings(self, key: str, target_widget: QTabWidget) -> None:
        """
        Parses settings data to recreate tabs.

        Args:
            key (str): The QSettings key to read from.
            target_widget (QTabWidget): The QTabWidget to populate.
        """
        items = self.settings.value(key, [], type=list)
        if isinstance(items, str):
            items = [items]

        for item in items:
            if isinstance(item, str) and os.path.exists(item):
                self._add_pdf_tab(item, target_widget, True)
            elif isinstance(item, dict):
                if (
                    item.get("type") == "pdf"
                    and item.get("data")
                    and os.path.exists(item.get("data"))
                ):
                    self._add_pdf_tab(item["data"], target_widget, True)
                elif item.get("type") == "web" and item.get("data"):
                    self._add_browser_tab(item["data"], target_widget)

    def refresh_signature_panel(self) -> None:
        """
        Dynamically shows or hides the signature dock based on the active tab.
        """
        active_widget = self.tabs_main.currentWidget()

        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            side_widget = self.tabs_side.currentWidget()
            if side_widget and side_widget != self.tree_signatures:
                active_widget = side_widget

        dismissed = getattr(active_widget, "_sig_panel_dismissed", False)
        signatures = (
            getattr(active_widget, "current_signatures", [])
            if hasattr(active_widget, "current_signatures")
            else []
        )

        sig_idx = self.tabs_side.indexOf(self.tree_signatures)
        if signatures and not dismissed:
            self.tree_signatures.clear()
            for sig in signatures:
                item = QTreeWidgetItem(self.tree_signatures)
                item.setText(0, f"  {sig.get('subject', 'Unknown')}")

                if sig.get("valid"):
                    if sig.get("is_trusted"):
                        icon_name = "circle-check.svg"
                    else:
                        icon_name = "circle-question-mark.svg"
                else:
                    icon_name = "circle-slash.svg"

                icon_path = get_resource_path(
                    os.path.join("assets", "icons", icon_name)
                )
                item.setIcon(0, QIcon(icon_path))
                item.setText(1, sig.get("field_name", "Unknown"))

                child_cert = QTreeWidgetItem(item)
                child_cert.setText(0, f"Cert Hash: {sig.get('cert_hash', '')[:15]}...")

                if not sig.get("is_trusted") and sig.get("valid"):
                    child_warn = QTreeWidgetItem(item)
                    child_warn.setText(0, "Identity Unknown (Not in Trust Store)")
                if not sig.get("valid"):
                    child_err = QTreeWidgetItem(item)
                    child_err.setText(0, "CRITICAL: Document Altered!")

            self.tree_signatures.expandAll()
            if sig_idx == -1:
                self.tabs_side.addTab(self.tree_signatures, "🖊️ Signatures")
            if self.tabs_side.isHidden():
                self.tabs_side.show()

        else:
            if sig_idx != -1:
                self.tabs_side.removeTab(sig_idx)
            if self.tabs_side.count() == 0:
                self.tabs_side.hide()

    def _add_pdf_tab(
        self, path: str, target_widget: QTabWidget, restore_state: bool = False
    ) -> None:
        """
        Internal helper to instantiate and add a ReaderTab.

        Args:
            path (str): Path to the PDF file.
            target_widget (QTabWidget): The tab widget to add the tab to.
            restore_state (bool): Whether to restore scroll position/zoom.
        """
        self.add_to_history(path, "pdf")
        reader = ReaderTab()
        reader.signatures_detected.connect(lambda _: self.refresh_signature_panel())
        reader.load_document(path, restore_state=restore_state)

        icon_path = get_resource_path(os.path.join("assets", "icons", "pdf.png"))
        pdf_icon = QIcon(icon_path)

        idx = target_widget.addTab(reader, pdf_icon, os.path.basename(path))
        target_widget.setCurrentIndex(idx)

    def _add_browser_tab(self, url: str, target_widget: QTabWidget) -> None:
        """
        Internal helper to instantiate and add a BrowserTab.
        Passing the shared profile to avoid database locking.

        Args:
            url (str): The URL to load.
            target_widget (QTabWidget): The tab widget to add the tab to.
        """
        if self.incognito:
            use_profile = self.web_profile
        else:
            use_profile = self.web_profile

        browser = BrowserTab(url, profile=use_profile, dark_mode=self.dark_mode)
        browser.completer.setModel(self.history_model)

        idx = target_widget.addTab(browser, "Loading...")
        target_widget.setCurrentIndex(idx)

        browser.web.urlChanged.connect(lambda qurl: self._update_tab_title(browser))
        browser.web.loadFinished.connect(lambda ok: self._update_tab_title(browser))
        browser.web.titleChanged.connect(lambda title: self._update_tab_title(browser))

    def setup_menu(self) -> None:
        """
        Configures the main window menu bar actions.
        """
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)

        file_menu.addSeparator()

        actions = [
            ("Open PDF (Ctrl+O)", None, self.open_pdf_smart),
            ("Open New PDF Tab (Ctrl+T)", None, lambda: self.new_pdf_tab()),
            (None, None, None),
            ("Split Current PDF", None, self.split_pdf),
            ("Merge PDFs", None, self.join_pdfs),
            (None, None, None),
            ("New Browser Tab (Ctrl+B)", None, lambda: self.new_browser_tab()),
            ("New Window (Ctrl+N)", None, self.new_window),
            ("New Incognito Tab (Ctrl+Shift+N)", None, self.new_incognito_window),
            (None, None, None),
            ("Exit (Ctrl+Q)", None, self.close),
        ]

        for name, shortcut, slot in actions:
            if name is None:
                file_menu.addSeparator()
            else:
                action = file_menu.addAction(name)
                if shortcut:
                    action.setShortcut(shortcut)
                if slot:
                    action.triggered.connect(slot)

        view_menu = menubar.addMenu("View")

        view_actions = [
            ("Bookmarks (Ctrl+K)", None, self.show_bookmarks),
            ("Downloads (Ctrl+J)", None, self.show_downloads),
            ("Search Library (Ctrl+L)", None, self.show_library_search),
            ("History (Ctrl+H)", None, self.show_history),
            ("Settings (Ctrl+,)", None, self.show_settings),
            ("Toggle UI Theme (Ctrl+D)", None, self.toggle_ui_theme),
        ]

        for name, shortcut, slot in view_actions:
            action = view_menu.addAction(name)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)

    def _populate_recent_menu(self) -> None:
        """Dynamically populates the Recent menu with the latest PDFs."""
        self.recent_menu.clear()
        recent_pdfs = self.history_manager.get_list("pdf")[-10:]
        if not recent_pdfs:
            action = self.recent_menu.addAction("No recent files")
            action.setEnabled(False)
            return

        for pdf_path in reversed(recent_pdfs):
            if os.path.exists(pdf_path):
                action = self.recent_menu.addAction(os.path.basename(pdf_path))
                action.triggered.connect(
                    lambda checked=False, p=pdf_path: self.new_pdf_tab(p)
                )

    def show_settings(self) -> None:
        """
        Displays the configuration dialog and applies changes on acceptance.
        """
        dlg = SettingsDialog(self)
        if dlg.exec():
            if dlg.cb_dark.isChecked() != self.dark_mode:
                self.toggle_ui_theme()
            self.toggle_auto_pdf(dlg.cb_auto_pdf.isChecked())
            self.settings.setValue(
                "homepage/custom_name", dlg.txt_custom_name.text().strip()
            )

    def new_pdf_tab(
        self, path: Optional[str] = None, restore_state: bool = False
    ) -> None:
        """
        Creates a new PDF tab in the main tab widget.

        Args:
            path (Optional[str]): Optional file path. If None, creates an empty tab.
            restore_state (bool): Whether to restore reading state.
        """
        if path:
            self._add_pdf_tab(path, self.tabs_main, restore_state)
        else:
            reader = ReaderTab()
            reader.signatures_detected.connect(lambda _: self.refresh_signature_panel())

            icon_path = get_resource_path(os.path.join("assets", "icons", "pdf.png"))
            pdf_icon = QIcon(icon_path)

            self.tabs_main.addTab(reader, pdf_icon, "New Tab")
            self.tabs_main.setCurrentWidget(reader)

    def new_browser_tab(
        self, url: str = "", incognito: bool = False, background: bool = False
    ) -> BrowserTab:
        """
        Creates a new Web Browser tab.
        Prioritizes the focused side tab widget if active.

        Args:
            url (str): The URL to navigate to.
            incognito (bool): Whether to enable incognito mode for this tab.
            background (bool): Open tab in background or foreground.

        Returns:
            BrowserTab: The newly created browser tab instance.
        """
        is_incognito = self.incognito or incognito
        target = self.tabs_main

        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            target = self.tabs_side

        tab_profile = (
            QWebEngineProfile()
            if (incognito and not self.incognito)
            else self.web_profile
        )

        browser = BrowserTab(
            url, profile=tab_profile, dark_mode=self.dark_mode, incognito=is_incognito
        )
        browser.completer.setModel(self.history_model)

        label = "Incognito" if incognito else "Loading..."
        target.addTab(browser, label)
        new_tab = target.widget(target.count() - 1)

        browser.web.urlChanged.connect(lambda qurl: self._update_tab_title(browser))
        browser.web.loadFinished.connect(lambda ok: self._update_tab_title(browser))
        browser.web.titleChanged.connect(lambda title: self._update_tab_title(browser))

        if not background:
            target.setCurrentWidget(new_tab)
            if hasattr(new_tab, "txt_url") and not url:
                new_tab.txt_url.setFocus()
                new_tab.txt_url.selectAll()

        return browser

    def new_window(self) -> None:
        """
        Spawns a new independent standard application window.
        """
        self._new_window_ref = RiemannWindow(incognito=False, restore_session=False)
        self._new_window_ref.show()

    def new_incognito_window(self) -> None:
        """
        Spawns a new independent window in Incognito mode.
        """
        self.incognito_window = RiemannWindow(incognito=True)
        self.incognito_window.show()

    def open_pdf_smart(self) -> None:
        """
        Handles the "Open PDF" action.
        Opens in the current tab if it's an empty reader, otherwise opens a new tab.
        Supports selecting multiple files.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Document",
            "",
            "Documents (*.pdf *.md);;PDF Files (*.pdf);;Markdown (*.md)",
        )
        if not paths:
            return

        first_path = paths[0]
        self.add_to_history(first_path)
        current = self.tabs_main.currentWidget()

        if isinstance(current, ReaderTab) and not current.current_path:
            current.load_document(first_path)
            self.tabs_main.setTabText(
                self.tabs_main.currentIndex(), os.path.basename(first_path)
            )
        else:
            self.new_pdf_tab(first_path)

        for path in paths[1:]:
            self.add_to_history(path)
            self.new_pdf_tab(path)

    def toggle_split_view(self) -> None:
        """
        Toggles the horizontal split-screen view.
        Moves the current tab to the side view if opening, or hides it if empty.
        """
        if self.tabs_side.isHidden():
            self.tabs_side.show()

        current = self.tabs_main.currentWidget()
        if current:
            idx = self.tabs_main.indexOf(current)
            text = self.tabs_main.tabText(idx)
            self.tabs_main.removeTab(idx)
            self.tabs_side.addTab(current, text)
            self.tabs_side.setCurrentWidget(current)

    def _record_closed_tab(self, widget: QWidget) -> None:
        """
        Saves the state of a closed tab to the stack for restoration.

        Args:
            widget (QWidget): The tab widget being closed (ReaderTab or BrowserTab).
        """
        if isinstance(widget, ReaderTab) and widget.current_path:
            self.closed_tabs_stack.append({"type": "pdf", "data": widget.current_path})
        elif isinstance(widget, BrowserTab):
            url_str = getattr(
                widget, "_original_url_before_close", widget.web.url().toString()
            )
            if url_str and url_str != "about:blank":
                self.closed_tabs_stack.append({"type": "web", "data": url_str})

    def restore_last_closed_tab(self) -> None:
        """
        Restores the most recently closed tab from the stack.
        """
        if not self.closed_tabs_stack:
            return

        last = self.closed_tabs_stack.pop()
        target = (
            self.tabs_side
            if self.tabs_side.isVisible() and self.tabs_side.hasFocus()
            else self.tabs_main
        )

        if last["type"] == "pdf":
            self._add_pdf_tab(last["data"], target, True)
        elif last["type"] == "web":
            self._add_browser_tab(last["data"], target)

        target.setCurrentIndex(target.count() - 1)

    def close_tab(self, index: int) -> None:
        """
        Closes a tab in the main tab widget with safety checks for web dialogs.

        Args:
            index (int): The index of the tab to close.
        """
        widget = self.tabs_main.widget(index)
        if widget:
            self._record_closed_tab(widget)

            if isinstance(widget, BrowserTab):
                widget.web.triggerPageAction(QWebEnginePage.WebAction.Stop)
                widget.web.setHtml("")
                if widget.web.page():
                    widget.web.page().deleteLater()
                widget.web.deleteLater()
            widget.deleteLater()

        self.tabs_main.removeTab(index)
        self._check_all_tabs_closed()

    def close_side_tab(self, index: int) -> None:
        """
        Closes a tab in the side tab widget with safety checks for web dialogs.

        Args:
            index (int): The index of the tab to close.
        """
        widget = self.tabs_side.widget(index)
        if widget:
            self._record_closed_tab(widget)

            if isinstance(widget, BrowserTab):
                widget.web.triggerPageAction(QWebEnginePage.WebAction.Stop)
                widget.web.setHtml("")
                if widget.web.page():
                    widget.web.page().deleteLater()

            if widget == getattr(self, "tree_signatures", None):
                active_main = self.tabs_main.currentWidget()
                if active_main:
                    active_main._sig_panel_dismissed = True
            else:
                widget.deleteLater()

        self.tabs_side.removeTab(index)
        if self.tabs_side.count() == 0:
            self.tabs_side.hide()
        self._check_all_tabs_closed()

    def _check_all_tabs_closed(self) -> None:
        """
        Checks if all tabs are closed and exits fullscreen if necessary.
        """
        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()

    def _handle_escape(self) -> None:
        """
        Handles the Escape key event to exit fullscreen.
        """
        if getattr(self, "_reader_fullscreen", False):
            self.toggle_reader_fullscreen()

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Handles the window close event.
        Saves session data unless in incognito mode.

        Args:
            event (QCloseEvent): The close event triggered by the system.
        """
        if self.incognito or not self.restore_session:
            self._kill_all_media_safely()
            super().closeEvent(event)
            return

        def get_files(tab_widget: QTabWidget) -> List[dict]:
            """
            Extracts serializable session data from the provided tab widget.
            Omits empty PDF tabs and browser homepages to ensure clean session restoration.

            Args:
                tab_widget (QTabWidget): The tab widget containing open browser or PDF tabs.

            Returns:
                List[dict]: A list of dictionaries representing the state of each tab.
            """
            tabs_data = []
            media_domains = [
                "youtube.com",
                "dailymotion.com",
                "reddit.com",
                "vimeo.com",
                "twitch.tv",
                "spotify.com",
                "netflix.com",
            ]

            for i in range(tab_widget.count()):
                wid = tab_widget.widget(i)
                if isinstance(wid, ReaderTab) and getattr(wid, "current_path", None):
                    tabs_data.append({"type": "pdf", "data": wid.current_path})
                elif isinstance(wid, BrowserTab):
                    if not getattr(wid, "incognito", False):
                        url_str = wid.web.url().toString()

                        for domain in media_domains:
                            if domain in url_str.lower():
                                url_obj = wid.web.url()
                                url_str = f"{url_obj.scheme()}://{url_obj.host()}"
                                break

                        if (
                            "homepage.html" not in url_str
                            and url_str != "about:blank"
                            and url_str
                        ):
                            tabs_data.append({"type": "web", "data": url_str})
            return tabs_data

        self.settings.setValue("session/main_tabs", get_files(self.tabs_main))
        self.settings.setValue("session/side_tabs", get_files(self.tabs_side))
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())

        self.settings.sync()
        self._kill_all_media_safely()
        super().closeEvent(event)

    def toggle_reader_fullscreen(self) -> None:
        """
        Toggles global fullscreen mode.
        Hides UI elements (menu bar, tab bars, toolbars) for immersive reading.
        """
        if not getattr(self, "_reader_fullscreen", False):
            self._reader_fullscreen = True
            self._was_maximized = self.isMaximized()
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            self.tabs_side.tabBar().hide()
            self._set_tabs_toolbar_visible(False)
            self.showFullScreen()
        else:
            self._reader_fullscreen = False
            self.menuBar().show()
            self.tabs_main.tabBar().show()
            self.tabs_side.tabBar().show()
            self._set_tabs_toolbar_visible(True)
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()

    def _set_tabs_toolbar_visible(self, visible: bool) -> None:
        """
        Helper to recursively hide/show toolbars in tabs.
        Assumes tabs have a 'toolbar' attribute.

        Args:
            visible (bool): True to make toolbars visible, False to hide them.
        """
        for tab_widget in [self.tabs_main, self.tabs_side]:
            for i in range(tab_widget.count()):
                w = tab_widget.widget(i)
                if hasattr(w, "toolbar"):
                    w.toolbar.setVisible(visible)

    def toggle_ui_theme(self) -> None:
        """
        Toggles the global UI dark mode without altering the content of the active tabs.
        """
        self.dark_mode = not self.dark_mode
        self.settings.setValue("darkMode", self.dark_mode)
        self.enforce_global_stylesheet()

        for tab_widget in (self.tabs_main, self.tabs_side):
            for i in range(tab_widget.count()):
                w = tab_widget.widget(i)
                if hasattr(w, "_update_icons"):
                    w._update_icons()

    def toggle_active_tab_theme(self) -> None:
        """
        Toggles the local content theme (PDF canvas or web page)
        of the currently focused tab without affecting the global UI.
        """
        target_widget = self.tabs_main.currentWidget()
        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            target_widget = self.tabs_side.currentWidget()

        if hasattr(target_widget, "toggle_theme"):
            target_widget.toggle_theme()

    def close_active_tab(self) -> None:
        """
        Closes the tab currently holding focus.
        """
        focus_widget = QApplication.focusWidget()
        target = None
        curr = focus_widget

        while curr:
            if curr == self.tabs_main:
                target = self.tabs_main
                break
            elif curr == self.tabs_side:
                target = self.tabs_side
                break
            curr = curr.parent()

        if not target:
            target = (
                self.tabs_main
                if not (self.tabs_side.isVisible() and self.tabs_side.count() > 0)
                else self.tabs_main
            )

        idx = target.currentIndex()
        if idx != -1:
            if target == self.tabs_main:
                self.close_tab(idx)
            else:
                self.close_side_tab(idx)

    def show_history(self) -> None:
        """
        Displays a dialog containing the unified web and PDF history.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("History")
        dialog.resize(600, 400)

        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()

        def create_list(data: List[str]) -> QListWidget:
            """
            Creates and populates a QListWidget with the provided string data.

            Args:
                data (List[str]): List of strings to populate the widget with.

            Returns:
                QListWidget: The populated list widget.
            """
            lw = QListWidget()
            lw.addItems(data)
            return lw

        list_web = create_list(self.history_manager.get_list("web"))
        list_pdf = create_list(self.history_manager.get_list("pdf"))

        tabs.addTab(list_web, "Web History")
        tabs.addTab(list_pdf, "PDF History")
        layout.addWidget(tabs)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Close
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        def open_item() -> None:
            """
            Handles double-click or accept events to open the selected history item in a new tab.
            """
            current_list = list_web if tabs.currentIndex() == 0 else list_pdf
            item = current_list.currentItem()
            if not item:
                return

            data = item.text()
            dialog.accept()

            if tabs.currentIndex() == 1:
                if os.path.exists(data):
                    current = self.tabs_main.currentWidget()
                    if isinstance(current, ReaderTab) and not current.current_path:
                        current.load_document(data)
                        self.tabs_main.setTabText(
                            self.tabs_main.currentIndex(), os.path.basename(data)
                        )
                else:
                    self._add_pdf_tab(data, self.tabs_main)
            else:
                self.new_browser_tab(data)

        button_box.accepted.disconnect()
        button_box.accepted.connect(open_item)

        list_web.itemDoubleClicked.connect(open_item)
        list_pdf.itemDoubleClicked.connect(open_item)

        dialog.exec()

    def show_bookmarks(self) -> None:
        """
        Displays a dialog containing saved bookmarks.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Bookmarks")
        dialog.resize(600, 400)

        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()

        for bm in self.bookmarks_manager.bookmarks:
            list_widget.addItem(f"{bm['title']} ({bm['url']})")

        layout.addWidget(list_widget)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Close
        )
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        def open_bm() -> None:
            """
            Parses the selected bookmark and opens the corresponding URL in a new browser tab.
            """
            item = list_widget.currentItem()
            if not item:
                return
            url = item.text().split(" (")[-1][:-1]
            dialog.accept()
            self.new_browser_tab(url)

        button_box.accepted.connect(open_bm)
        list_widget.itemDoubleClicked.connect(open_bm)
        dialog.exec()

    def show_downloads(self) -> None:
        """
        Shows the non-modal download manager dialog.
        """
        self.download_manager_dialog.show()
        self.download_manager_dialog.raise_()

    def show_library_search(self) -> None:
        """
        Instantiates and displays the modal library search dialog.
        """
        dialog = LibrarySearchDialog(self)
        dialog.exec()

    def toggle_auto_pdf(self, checked: bool) -> None:
        """
        Updates the auto-open PDF setting.

        Args:
            checked (bool): True to enable auto-opening of PDFs, False to disable.
        """
        self.settings.setValue("browser/auto_open_pdf", checked)

    def open_pdf_in_new_tab(self, path: str) -> None:
        """
        Opens a PDF in a new Reader Tab.
        Preferentially opens in the Side Split if active, otherwise Main.

        Args:
            path (str): The file path to the PDF.
        """
        target = self.tabs_side if (self.tabs_side.isVisible()) else self.tabs_main

        if target == self.tabs_main and self.tabs_side.isHidden():
            self.toggle_split_view()
            target = self.tabs_side

        self._add_pdf_tab(path, target)
        target.setCurrentIndex(target.count() - 1)

    def _update_tab_title(self, browser, *args):
        """
        Updates the tab title when the web page title changes.

        Args:
            browser (BrowserTab): The browser tab instance triggering the title update.
            args: Variable arguments corresponding to the underlying Qt signal payload.
        """
        title = browser.web.title()
        if not title:
            url_str = browser.web.url().toString()
            if url_str and url_str != "about:blank":
                title = QUrl(url_str).host()
            else:
                title = "New Tab"

        display_title = (title[:20] + "...") if len(title) > 25 else title

        idx = self.tabs_main.indexOf(browser)
        if idx != -1:
            self.tabs_main.setTabText(idx, display_title)
            self._update_window_title()
            return

        if self.tabs_side.isVisible():
            idx = self.tabs_side.indexOf(browser)
            if idx != -1:
                self.tabs_side.setTabText(idx, display_title)
                self._update_window_title()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """
        Accepts the drag event if it contains local files.

        Args:
            event (QDragEnterEvent): The drag enter event containing MIME data."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """
        Continuously accepts the drag action as it moves over child widgets.

        Args:
            event (QDragMoveEvent): The drag move event payload.
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        """
        Handles dropped files by opening supported types in a new tab.

        Args:
            event (QDropEvent): The drop event containing the file path payload.
        """
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path.lower().endswith(".pdf") or path.lower().endswith(".md"):
                    self.new_pdf_tab(path)
        event.acceptProposedAction()

    def _show_tab_context_menu(self, pos, tab_widget: QTabWidget) -> None:
        """
        Displays the advanced tab management context menu.

        Args:
            pos: The local cursor position where the context menu was requested.
            tab_widget (QTabWidget): The tab widget interacting with the context menu.
        """
        idx = tab_widget.tabBar().tabAt(pos)
        if idx == -1:
            return

        widget = tab_widget.widget(idx)
        menu = QMenu(self)

        rename_action = menu.addAction("Rename Tab (Custom)")
        revert_action = menu.addAction("Revert to Original Name")
        meta_action = None

        if hasattr(widget, "document_metadata") and widget.document_metadata.get(
            "title"
        ):
            meta_title = widget.document_metadata["title"]
            meta_action = menu.addAction(f"Rename to '{meta_title[:30]}...'")

        menu.addSeparator()
        action_duplicate = menu.addAction("Duplicate Tab")
        menu.addSeparator()
        action_close = menu.addAction("Close Tab")
        action_close_other = menu.addAction("Close Other Tabs")
        action_close_right = menu.addAction("Close Tabs to the Right")

        action = menu.exec(tab_widget.tabBar().mapToGlobal(pos))

        if not action:
            return

        if action == rename_action:
            current_name = tab_widget.tabText(idx)
            new_name, ok = QInputDialog.getText(
                self, "Rename Tab", "Enter new tab name:", text=current_name
            )
            if ok and new_name.strip():
                tab_widget.setTabText(idx, new_name.strip())
                self._update_window_title()

        elif action == revert_action:
            if hasattr(widget, "current_path") and widget.current_path:
                original_name = os.path.basename(widget.current_path)
                tab_widget.setTabText(idx, original_name)
                self._update_window_title()
            elif hasattr(widget, "web") and hasattr(widget.web, "title"):
                original_name = widget.web.title()
                if not original_name:
                    original_name = "New Tab"
                tab_widget.setTabText(idx, original_name)
                self._update_window_title()
            elif hasattr(widget, "view") and hasattr(widget.view, "title"):
                original_name = widget.view.title()
                if not original_name:
                    original_name = "New Tab"
                tab_widget.setTabText(idx, original_name)
                self._update_window_title()

        elif meta_action and action == meta_action:
            title = widget.document_metadata["title"]
            display_title = (title[:25] + "..") if len(title) > 25 else title
            tab_widget.setTabText(idx, display_title)
            self._update_window_title()

        elif action == action_close:
            if tab_widget == self.tabs_main:
                self.close_tab(idx)
            else:
                self.close_side_tab(idx)

        elif action == action_close_other:
            for i in range(tab_widget.count() - 1, -1, -1):
                if i != idx:
                    if tab_widget == self.tabs_main:
                        self.close_tab(i)
                    else:
                        self.close_side_tab(i)

        elif action == action_close_right:
            for i in range(tab_widget.count() - 1, idx, -1):
                if tab_widget == self.tabs_main:
                    self.close_tab(i)
                else:
                    self.close_side_tab(i)

        elif action == action_duplicate:
            if hasattr(widget, "current_path") and widget.current_path:
                self._add_pdf_tab(widget.current_path, tab_widget)
            elif hasattr(widget, "web"):
                self._add_browser_tab(widget.web.url().toString(), tab_widget)

    def _kill_all_media_safely(self) -> None:
        """
        Navigates active media tabs to their root domain before exit
        to sever the media pipeline without destroying C++ objects prematurely.
        """
        media_domains = [
            "youtube.com",
            "dailymotion.com",
            "reddit.com",
            "vimeo.com",
            "twitch.tv",
            "spotify.com",
            "netflix.com",
        ]

        for target in (self.tabs_main, self.tabs_side):
            for i in range(target.count()):
                wid = target.widget(i)
                if (
                    isinstance(wid, BrowserTab)
                    and hasattr(wid, "web")
                    and wid.web.page()
                ):
                    wid.web.page().setAudioMuted(True)
                    url_str = wid.web.url().toString().lower()
                    is_media = False

                    for domain in media_domains:
                        if domain in url_str:
                            is_media = True
                            wid.web.settings().setAttribute(
                                QWebEngineSettings.WebAttribute.JavascriptEnabled, False
                            )
                            wid.web.load(QUrl("about:blank"))
                            break

                    if not is_media:
                        wid.web.triggerPageAction(QWebEnginePage.WebAction.Stop)

    def enforce_global_stylesheet(self) -> None:
        """
        Forces the application to read and apply the appropriate QSS file,
        preventing Qt from reverting to native generic tab styles.
        """
        css_file = (
            "modern_dark.css"
            if getattr(self, "dark_mode", False)
            else "modern_light.css"
        )

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_p = getattr(sys, "_MEIPASS")
            css_path = os.path.join(base_p, "riemann", "assets", "theme", css_file)
            theme_dir = os.path.join(base_p, "riemann", "assets", "theme")
        else:
            base_p = os.path.dirname(os.path.abspath(__file__))
            css_path = os.path.join(base_p, "assets", "theme", css_file)
            theme_dir = os.path.join(base_p, "assets", "theme")

        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()

            theme_dir_css = theme_dir.replace("\\", "/")
            stylesheet = stylesheet.replace("url('", f"url('{theme_dir_css}/")
            stylesheet = stylesheet.replace('url("', f'url("{theme_dir_css}/')

            self.setStyleSheet(stylesheet)
        else:
            self.setStyleSheet("")


def run() -> None:
    """
    Application Entry Point.

    Sets required Chromium flags for the QtWebEngine,
    initializes the QApplication,
    enforces a single-instance pattern via QLocalServer,
    and starts the main event loop.
    """

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--autoplay-policy=no-user-gesture-required "
        "--disable-setuid-sandbox "
        "--disable-features=AudioServiceOutOfProcess "
        "--referrer-policy=no-referrer-when-downgrade "
        "--enable-features=WebEngineProprietaryCodecs "
        "--renderer-process-limit=2 "
        "--process-per-site "
        "--disk-cache-size=52428800 "
    )
    sys.argv.append("--autoplay-policy=no-user-gesture-required")

    install_linux_integration()
    app = QApplication(sys.argv)
    app.setApplicationName("Riemann")

    window = RiemannWindow()
    args = app.arguments()
    files_to_open = [
        arg for arg in args[1:] if not arg.startswith("-") and os.path.isfile(arg)
    ]

    server_name = "RiemannSingleInstance"
    socket = QLocalSocket()
    socket.connectToServer(server_name)

    if socket.waitForConnected(500):
        if files_to_open:
            msg = "|".join(files_to_open)
            socket.write(msg.encode("utf-8"))
            socket.waitForBytesWritten(500)
        sys.exit(0)

    server = QLocalServer()
    server.removeServer(server_name)
    server.listen(server_name)

    icon_path = get_resource_path(os.path.join("assets", "icons", "Icon.png"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = RiemannWindow(external_files=files_to_open)

    def handle_connection():
        """Handles incoming IPC connections from secondary instances."""
        client = server.nextPendingConnection()

        def read_data():
            msg = client.readAll().data().decode("utf-8")
            if msg:
                for path in msg.split("|"):
                    if os.path.isfile(path):
                        window.new_pdf_tab(path)
            window.activateWindow()
            window.raise_()
            client.disconnectFromServer()

        client.readyRead.connect(read_data)

    server.newConnection.connect(handle_connection)

    for path in files_to_open:
        window.new_pdf_tab(path)

    window.show()
    sys.exit(app.exec())


def install_linux_integration():
    """
    Detects if running as a frozen Linux executable and installs/updates
    the .desktop shortcut and icon in the user's local share.
    """
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("linux"):
        return

    try:
        app_name = "Riemann"

        home = Path.home()
        apps_dir = home / ".local" / "share" / "applications"
        icons_dir = home / ".local" / "share" / "icons"

        apps_dir.mkdir(parents=True, exist_ok=True)
        icons_dir.mkdir(parents=True, exist_ok=True)

        base_path = os.path.dirname(os.path.abspath(__file__))
        internal_icon_path = os.path.join(base_path, "assets", "icons", "Icon.png")

        if not os.path.exists(internal_icon_path) and hasattr(sys, "_MEIPASS"):
            internal_icon_path = os.path.join(
                sys._MEIPASS,  # pyright: ignore[reportAttributeAccessIssue]
                "riemann",
                "assets",
                "icons",
                "Icon.png",
            )

        if not os.path.exists(internal_icon_path):
            print(f"Warning: Could not find internal icon at {internal_icon_path}")
            return

        persistent_icon_path = icons_dir / "riemann.png"
        shutil.copy2(internal_icon_path, persistent_icon_path)

        desktop_file_path = apps_dir / f"{app_name}.desktop"
        exe_path = sys.executable

        desktop_entry = f"""[Desktop Entry]
Type=Application
Name={app_name}
GenericName=PDF Reader
Comment=A standalone PDF reader and manager
Exec="{exe_path}" %f
Icon={persistent_icon_path}
Terminal=false
Categories=Office;Viewer;Utility;
StartupWMClass={app_name}
MimeType=application/pdf;
"""

        with open(desktop_file_path, "w") as f:
            f.write(desktop_entry)

        os.system(f"update-desktop-database {apps_dir} > /dev/null 2>&1")
        print(f"[Riemann] Integrated to desktop menu: {desktop_file_path}")

    except Exception as e:
        print(f"Icon integration warning: {e}")
