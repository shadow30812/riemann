"""
Main Application Module.

This module defines the primary window manager (`RiemannWindow`) and the
application entry point. It orchestrates the UI layout, tab management
(split-view), global keyboard shortcuts, and session persistence.
"""

import os
import sys

from .core.dependencies import setup_and_get_venv

# os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")
# import gc
# import tracemalloc
# tracemalloc.start()

try:
    setup_and_get_venv()
except Exception as e:
    print(f"[Riemann Warning] Failed to mount local virtual environment: {e}")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    bundle_dir = getattr(sys, "_MEIPASS")
    os.environ["PDFIUM_DYNAMIC_LIB_PATH"] = bundle_dir

import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import psutil
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
    QDesktopServices,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPixmapCache,
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
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core.dependencies import DependenciesDialog
from .core.features import (
    CompressDialog,
    ConvertDialog,
    trigger_local_caption_generation,
)
from .core.managers import (
    BookmarksManager,
    DownloadManager,
    HistoryManager,
    LibraryManager,
    YtDlpDownloadManager,
)
from .core.mini_player import MiniAudioPlayer
from .ui.browser import BrowserTab, PreviewTextTab
from .ui.components import DraggableTabWidget
from .ui.explorer import FileExplorerPanel, FolderHomeTab
from .ui.favourites import ManageFavoritesDialog
from .ui.reader.tab import PreviewReaderTab, ReaderTab


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


def get_dialog_directory(settings: QSettings) -> str:
    """Calculates the optimal starting directory for file dialogs."""
    default_dir = settings.value("app/default_dir", "", type=str)
    if default_dir and os.path.exists(default_dir):
        return default_dir

    last_dir = settings.value("app/last_dir", "", type=str)
    if last_dir and os.path.exists(last_dir):
        return last_dir

    return QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )


def save_last_directory(settings: QSettings, file_path: str) -> None:
    """Saves the directory of the provided file path to settings."""
    if file_path:
        directory = (
            os.path.dirname(file_path) if os.path.isfile(file_path) else file_path
        )
        if os.path.exists(directory):
            settings.setValue("app/last_dir", directory)


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

        self.cb_floating_fs = QCheckBox()
        self.cb_floating_fs.setChecked(
            parent.settings.value("app/floating_fs_btn", True, type=bool)
        )

        self.cb_dark = QCheckBox()
        self.cb_dark.setChecked(parent.dark_mode)

        self.txt_custom_name = QLineEdit()
        self.txt_custom_name.setPlaceholderText("Leave empty for OS default")
        self.txt_custom_name.setText(
            parent.settings.value("homepage/custom_name", "", type=str)
        )

        self.txt_default_dir = QLineEdit()
        self.txt_default_dir.setPlaceholderText(
            "Leave empty to use last opened directory"
        )
        self.txt_default_dir.setText(
            parent.settings.value("app/default_dir", "", type=str)
        )

        self.btn_browse_dir = QPushButton("Browse...")
        self.btn_browse_dir.clicked.connect(self.browse_default_dir)

        dir_layout = QHBoxLayout()
        dir_layout.addWidget(self.txt_default_dir)
        dir_layout.addWidget(self.btn_browse_dir)

        self.spin_autoscroll = QSpinBox()
        self.spin_autoscroll.setRange(1, 100)
        self.spin_autoscroll.setValue(
            parent.settings.value("app/autoscroll_speed", 5, type=int)
        )
        self.spin_autoscroll.setSuffix(" px/tick")

        self.slider_scale = QSlider(Qt.Orientation.Horizontal)
        self.slider_scale.setRange(50, 250)
        self.slider_scale.setSingleStep(1)
        self.slider_scale.setPageStep(5)
        self.slider_scale.setMinimumWidth(220)
        current_scale = parent.settings.value("app/ui_scale", 100, type=int)
        self.slider_scale.setValue(current_scale)

        self.spin_scale = QSpinBox()
        self.spin_scale.setRange(50, 250)
        self.spin_scale.setSingleStep(1)
        self.spin_scale.setValue(current_scale)
        self.spin_scale.setSuffix("%")
        self.spin_scale.setFixedWidth(75)
        self.spin_scale.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.slider_scale.valueChanged.connect(self.spin_scale.setValue)
        self.spin_scale.valueChanged.connect(self.slider_scale.setValue)

        scale_layout = QHBoxLayout()
        scale_layout.addWidget(self.slider_scale)
        scale_layout.addWidget(self.spin_scale)
        btn_reset_scale = QPushButton("Reset")
        btn_reset_scale.setFixedWidth(60)
        btn_reset_scale.clicked.connect(lambda: self.slider_scale.setValue(100))
        scale_layout.addWidget(btn_reset_scale)

        form_layout.addRow("Display Size (UI Scale):", scale_layout)
        form_layout.addRow("Default Dialog Directory:", dir_layout)
        form_layout.addRow("Enable Dark Mode:", self.cb_dark)
        form_layout.addRow("Auto-open Downloaded PDFs:", self.cb_auto_pdf)
        form_layout.addRow("Show Floating Fullscreen Button:", self.cb_floating_fs)
        form_layout.addRow("Homepage Greeting Name:", self.txt_custom_name)
        form_layout.addRow("Auto-Scroll Speed:", self.spin_autoscroll)
        layout.addLayout(form_layout)

        group = QGroupBox("Data Management")
        group_layout = QVBoxLayout(group)

        btn_clear_history = QPushButton("Clear Browsing History")
        btn_clear_history.clicked.connect(self.clear_history)

        btn_clear_downloads = QPushButton("Clear Download History")
        btn_clear_downloads.clicked.connect(self.clear_downloads)

        btn_clear_cookies = QPushButton("Clear Cookies")
        btn_clear_cookies.clicked.connect(self.clear_cookies)

        btn_manage_cookies = QPushButton("Manage Individual Cookies")
        btn_manage_cookies.clicked.connect(self.manage_individual_cookies)

        btn_clear_cache = QPushButton("Clear Cache")
        btn_clear_cache.clicked.connect(self.clear_cache)

        btn_clear_all = QPushButton("Clear All Data")
        btn_clear_all.setStyleSheet("color: #d32f2f; font-weight: bold;")
        btn_clear_all.clicked.connect(self.clear_all_data)

        btn_manage_deps = QPushButton("Manage External Dependencies")
        btn_manage_deps.setStyleSheet(
            "background-color: #1976D2; color: white; font-weight: bold;"
        )
        btn_manage_deps.clicked.connect(self.open_dependency_manager)

        group_layout.addWidget(btn_clear_history)
        group_layout.addWidget(btn_clear_downloads)
        group_layout.addWidget(btn_clear_cookies)
        group_layout.addWidget(btn_manage_cookies)
        group_layout.addWidget(btn_clear_cache)
        group_layout.addWidget(btn_clear_all)
        layout.addSpacing(10)
        group_layout.addWidget(btn_manage_deps)
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

    def manage_individual_cookies(self) -> None:
        """
        Opens the individual cookie management dialog.
        """
        dlg = self.ManageCookiesDialog(self.parent_win)
        dlg.exec()

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

    def browse_default_dir(self) -> None:
        """Opens a dialog to select the default directory."""
        start_dir = self.txt_default_dir.text() or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        directory = QFileDialog.getExistingDirectory(
            self, "Select Default Directory", start_dir
        )
        if directory:
            self.txt_default_dir.setText(directory)

    def open_dependency_manager(self) -> None:
        """Opens the UI to install/uninstall heavy dynamically loaded dependencies."""
        dlg = DependenciesDialog(self)
        dlg.exec()

    class ManageCookiesDialog(QDialog):
        """
        A dialog for clearing cookies associated with specific individual websites.
        """

        def __init__(self, parent: "RiemannWindow") -> None:
            super().__init__(parent)
            self.setWindowTitle("Manage Cookies for Individual Sites")
            self.resize(450, 500)
            self.parent_win = parent
            layout = QVBoxLayout(self)

            layout.addWidget(QLabel("Select a website to clear its cookies:"))
            self.list_widget = QListWidget()
            layout.addWidget(self.list_widget)

            history = self.parent_win.history_manager.get_list("web")
            domains = set()
            for url in history:
                try:
                    host = QUrl(url).host()
                    if host:
                        domains.add(host)
                except Exception:
                    pass

            self.list_widget.addItems(sorted(list(domains)))

            btn_layout = QHBoxLayout()
            self.btn_clear = QPushButton("Clear Cookies for Selected")
            self.btn_clear.clicked.connect(self.clear_selected)
            self.btn_close = QPushButton("Close")
            self.btn_close.clicked.connect(self.accept)

            btn_layout.addWidget(self.btn_clear)
            btn_layout.addWidget(self.btn_close)
            layout.addLayout(btn_layout)

            self.store = self.parent_win.web_profile.cookieStore()
            self.cookies_to_delete = set()
            self.store.cookieAdded.connect(self._handle_cookie_added)

        def clear_selected(self) -> None:
            item = self.list_widget.currentItem()
            if not item:
                QMessageBox.warning(self, "Warning", "Please select a website first.")
                return
            domain = item.text()
            self.cookies_to_delete.add(domain)
            self.store.loadAllCookies()
            QMessageBox.information(self, "Success", f"Cookies cleared for {domain}.")

        def _handle_cookie_added(self, cookie) -> None:
            domain = cookie.domain()
            for d in self.cookies_to_delete:
                if d in domain:
                    self.store.deleteCookie(cookie)


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
        self._start_time = time.time()
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
        self.dark_mode: bool = self.settings.value("darkMode", False, type=bool)
        self._was_unclean_exit: bool = not self.settings.value(
            "session/clean_exit", True, type=bool
        )
        self.settings.setValue("session/clean_exit", False)
        self.settings.sync()

        self.download_manager_dialog = DownloadManager(self)
        self.ytdlp_manager_dialog = YtDlpDownloadManager(self)
        self.history_manager = HistoryManager()
        self.history_model = QStringListModel(self.history_manager.get_model_data())
        self.bookmarks_manager = BookmarksManager()

        self.closed_tabs_stack: List[dict] = []
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(8)
        self.setCentralWidget(self.main_splitter)

        self.explorer_panel = FileExplorerPanel(self, self.dark_mode)
        self.explorer_panel.hide()
        self.explorer_panel.file_single_clicked.connect(self._on_file_single_clicked)
        self.explorer_panel.file_double_clicked.connect(self._on_file_double_clicked)
        self.main_splitter.addWidget(self.explorer_panel)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.main_splitter.addWidget(self.splitter)
        self.main_splitter.setSizes([250, 950])

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
        self.tabs_side.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.tabs_side.setMinimumWidth(250)
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
        self.tree_signatures.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.tree_signatures.setMinimumWidth(250)
        self.tree_signatures.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )

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

        self._session_autosave_timer = QTimer(self)
        self._session_autosave_timer.setInterval(15000)
        self._session_autosave_timer.timeout.connect(self._auto_save_session)
        self._session_autosave_timer.start()

        self.floating_fs_btn = QPushButton(self)
        self.floating_fs_btn.setFixedSize(40, 40)
        self.floating_fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.floating_fs_btn.clicked.connect(self.toggle_reader_fullscreen)
        self.floating_fs_btn.hide()
        self._update_floating_btn_style()

        self.floating_fs_timer = QTimer(self)
        self.floating_fs_timer.setInterval(2000)
        self.floating_fs_timer.setSingleShot(True)
        self.floating_fs_timer.timeout.connect(self.floating_fs_btn.hide)

        self.fs_tracker_timer = QTimer(self)
        self.fs_tracker_timer.setInterval(100)
        self.fs_tracker_timer.timeout.connect(self._track_mouse_for_fs)

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
            ("Ctrl+Shift+E", self.new_folder_home_tab),
            ("Ctrl+N", self.new_window),
            ("Ctrl+Shift+N", self.new_incognito_window),
            ("Ctrl+O", self.open_file_smart),
            ("Ctrl+K, K", self.show_bookmarks),
            ("Ctrl+J", self.show_downloads),
            ("Ctrl+Shift+J", self.show_ytdlp_downloads),
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
        Controls the visibility of the application menu bar, tab bars, and toolbars/navigation bars.

        Args:
            show (bool): True to make UI controls visible, False to hide them.
        """
        state = getattr(self, "_fullscreen_state", 0)
        if show:
            self.menuBar().show()
            self.tabs_main.tabBar().show()
            if self.tabs_side.isVisible():
                self.tabs_side.tabBar().show()
            self._set_tabs_toolbar_visible(True)
        else:
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            if self.tabs_side.isVisible():
                self.tabs_side.tabBar().hide()
            if state == 2:
                self._set_tabs_toolbar_visible(False)
            elif state == 1:
                self._set_tabs_toolbar_visible(True)

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
        start_dir = get_dialog_directory(self.settings)

        if isinstance(current, ReaderTab) and current.current_path:
            source_path = current.current_path
        else:
            source_path, _ = QFileDialog.getOpenFileName(
                self, "Select PDF to Split", start_dir, "PDF Files (*.pdf)"
            )
        if not source_path:
            return

        pages_str, ok = QInputDialog.getText(
            self, "Split PDF", "Enter page ranges to extract (e.g., 1-5, 8, 11-15):"
        )
        if not ok or not pages_str.strip():
            return

        current_dir = get_dialog_directory(self.settings)
        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Split PDF As",
            os.path.join(current_dir, "split.pdf"),
            "PDF Files (*.pdf)",
        )
        if not dest_path:
            return

        save_last_directory(self.settings, dest_path)
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
        start_dir = get_dialog_directory(self.settings)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs to Merge", start_dir, "PDF Files (*.pdf)"
        )
        if not paths or len(paths) < 2:
            if paths:
                QMessageBox.warning(
                    self, "Merge PDFs", "Please select at least two PDF files."
                )
            return

        save_dir = os.path.dirname(paths[0])
        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Merged PDF As",
            os.path.join(save_dir, "merged.pdf"),
            "PDF Files (*.pdf)",
        )
        if not dest_path:
            return

        save_last_directory(self.settings, dest_path)
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
        if self.incognito or not item:
            return

        clean_item = item
        if clean_item.startswith("file://"):
            clean_item = QUrl(clean_item).toLocalFile()

        if clean_item.lower().endswith((".pdf", ".md")):
            item_type = "pdf"
            if os.path.exists(clean_item):
                clean_item = os.path.abspath(clean_item)
        elif clean_item.lower().endswith((".html", ".css", ".js")):
            item_type = "web"
            if os.path.exists(clean_item):
                clean_item = os.path.abspath(clean_item)

        self.history_manager.add(clean_item, item_type)
        self.history_model.setStringList(self.history_manager.get_model_data())

    def _restore_session(self) -> None:
        """
        Restores the window geometry and open tabs from the previous session.
        Defaults to opening both PDF and browser homepages if no session exists or incognito is active
        UNLESS app is opened externally.
        """
        if getattr(self, "_was_unclean_exit", False) and not self.external_files:
            main_saved = self.settings.value("session/main_tabs", [])
            side_saved = self.settings.value("session/side_tabs", [])
            if main_saved or side_saved:
                ret = QMessageBox.question(
                    self,
                    "Restore Last Opened Tabs",
                    "Riemann detected that the application did not close properly.\n\n"
                    "Would you like to restore your last opened tabs?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if ret == QMessageBox.StandardButton.Yes:
                    if self.settings.value("window/geometry"):
                        self.restoreGeometry(self.settings.value("window/geometry"))  # type: ignore
                    self._restore_tabs_from_settings("session/main_tabs", self.tabs_main)
                    self._restore_tabs_from_settings("session/side_tabs", self.tabs_side)
                    if self.tabs_side.count() > 0:
                        self.tabs_side.show()
                        if self.settings.value("splitter/state"):
                            self.splitter.restoreState(self.settings.value("splitter/state"))  # type: ignore
                    return

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

        main_idx = self.settings.value("session/main_active_idx", -1, type=int)
        if isinstance(main_idx, int):
            if main_idx >= 0 and main_idx < self.tabs_main.count():
                self.tabs_main.setCurrentIndex(main_idx)

        side_idx = self.settings.value("session/side_active_idx", -1, type=int)
        if isinstance(side_idx, int):
            if side_idx >= 0 and side_idx < self.tabs_side.count():
                self.tabs_side.setCurrentIndex(side_idx)

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

        active_folder = self.settings.value("session/active_folder", "", type=str)
        if active_folder and os.path.exists(active_folder):
            self.tabs_side.hide()
            self.is_folder_session_active = True
            if hasattr(self, "explorer_panel"):
                self.explorer_panel.set_path(active_folder)
                self.explorer_panel.show()
            self.history_manager.add(active_folder, "folder")

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
                elif item.get("type") == "folder_home":
                    self.new_folder_home_tab(target_widget)

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
            suffix = "-white" if self.dark_mode else ""

            for sig in signatures:
                item = QTreeWidgetItem(self.tree_signatures)
                item.setText(0, f"  {sig.get('subject', 'Unknown')}")

                if sig.get("valid"):
                    if sig.get("is_trusted"):
                        icon_name = "circle-check"
                    else:
                        icon_name = "circle-question-mark"
                else:
                    icon_name = "circle-slash"

                icon_path = get_resource_path(
                    os.path.join("assets", "icons", f"{icon_name}{suffix}.svg")
                )
                item.setIcon(0, QIcon(icon_path))
                item.setText(1, sig.get("field_name", "Unknown"))

                pen_path = get_resource_path(
                    os.path.join("assets", "icons", f"pen-line{suffix}.svg")
                )
                item.setIcon(1, QIcon(pen_path))

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
                self.tabs_side.addTab(self.tree_signatures, "Signatures")
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

        current_idx = target_widget.currentIndex()
        insert_idx = current_idx + 1 if current_idx != -1 else target_widget.count()

        target_widget.insertTab(insert_idx, reader, pdf_icon, os.path.basename(path))
        self._update_tab_tooltip(target_widget, insert_idx)
        target_widget.setCurrentIndex(insert_idx)

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

        icon_path = get_resource_path(os.path.join("assets", "icons", "browser.png"))
        default_icon = QIcon(icon_path)

        current_idx = target_widget.currentIndex()
        insert_idx = current_idx + 1 if current_idx != -1 else target_widget.count()

        target_widget.insertTab(insert_idx, browser, default_icon, "Loading...")
        self._update_tab_tooltip(target_widget, insert_idx)
        target_widget.setCurrentIndex(insert_idx)

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

        action_restore_session = file_menu.addAction("Restore Last Opened Tabs")
        action_restore_session.triggered.connect(self.restore_previous_session)

        self.external_app_menu = file_menu.addMenu("Open in External Application...")
        self.external_app_menu.aboutToShow.connect(self._populate_external_app_menu)

        file_menu.addSeparator()
        self.fav_menu = file_menu.addMenu("Favorites")
        self.fav_file_menu = self.fav_menu.addMenu("Open File from Favorite...")
        self.fav_folder_menu = self.fav_menu.addMenu("Open Favorite Folder...")

        self.fav_file_menu.aboutToShow.connect(self._populate_fav_file_menu)
        self.fav_folder_menu.aboutToShow.connect(self._populate_fav_folder_menu)

        self.fav_menu.addSeparator()
        action_manage_fav = self.fav_menu.addAction("Manage Favorites (Ctrl+Shift+F)")
        action_manage_fav.triggered.connect(self.manage_favorites)
        QShortcut(QKeySequence("Ctrl+Shift+F"), self).activated.connect(
            self.manage_favorites
        )

        action_open_folder = file_menu.addAction("Open Folder (Ctrl+K, Ctrl+O)")
        action_open_folder.triggered.connect(lambda: self.open_folder())

        action_close_folder = file_menu.addAction("Close Folder Session (Ctrl+K, F)")
        action_close_folder.triggered.connect(self.close_folder)

        QShortcut(QKeySequence("Ctrl+K, Ctrl+O"), self).activated.connect(
            self.open_folder
        )
        QShortcut(QKeySequence("Ctrl+K, F"), self).activated.connect(self.close_folder)

        file_menu.addSeparator()
        file_actions = [
            ("Open file (Ctrl+O)", None, self.open_file_smart),
            ("New PDF Tab (Ctrl+T)", None, lambda: self.new_pdf_tab()),
            ("New Browser Page (Ctrl+B)", None, lambda: self.new_browser_tab()),
            ("New Folder Dashboard (Ctrl+Shift+E)", None, self.new_folder_home_tab),
            (None, None, None),
            ("Split Current PDF", None, self.split_pdf),
            ("Merge PDFs", None, self.join_pdfs),
            (None, None, None),
            ("Convert Document...", None, self.show_convert_dialog),
            ("Compress Document...", None, self.show_compress_dialog),
            (
                "Generate Captions...",
                None,
                lambda: trigger_local_caption_generation(self),
            ),
            (None, None, None),
            ("New Window (Ctrl+N)", None, self.new_window),
            ("New Incognito Tab (Ctrl+Shift+N)", None, self.new_incognito_window),
            (None, None, None),
            ("Exit (Ctrl+Q)", None, self.close),
        ]

        for name, shortcut, slot in file_actions:
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
            ("Bookmarks (Ctrl+K, K)", None, self.show_bookmarks),
            ("Downloads (Ctrl+J)", None, self.show_downloads),
            ("yt-dlp Downloads (Ctrl+Shift+J)", None, self.show_ytdlp_downloads),
            ("Search Library (Ctrl+L)", None, self.show_library_search),
            ("History (Ctrl+H)", None, self.show_history),
            ("Settings (Ctrl+,)", None, self.show_settings),
            ("Toggle UI Theme (Ctrl+D)", None, self.toggle_ui_theme),
            ("Toggle Split View (Ctrl+\\)", None, self.toggle_split_view),
            ("Show Signature Panel", None, self.force_show_signatures),
        ]

        for name, shortcut, slot in view_actions:
            action = view_menu.addAction(name)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)

        if psutil:
            self._last_net_io = psutil.net_io_counters()
            self._last_net_time = time.time()

        self.metrics_label = QLabel(" Uptime: 00:00:00 ")
        self.metrics_label.setTextFormat(Qt.TextFormat.RichText)
        self.metrics_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.metrics_label.setStyleSheet(
            "color: #888; font-size: 11px; padding-right: 15px; font-weight: bold;"
        )

        self.metrics_label.setMinimumWidth(580)

        self.menu_corner_widget = QWidget(self)
        self.menu_corner_layout = QHBoxLayout(self.menu_corner_widget)
        self.menu_corner_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_corner_layout.setSpacing(0)

        self.mini_player = MiniAudioPlayer(self)

        self.menu_corner_layout.addWidget(self.mini_player)
        self.menu_corner_layout.addWidget(self.metrics_label)

        self.menuBar().setCornerWidget(
            self.menu_corner_widget, Qt.Corner.TopRightCorner
        )
        self.metrics_timer = QTimer(self)
        self.metrics_timer.timeout.connect(self._update_global_metrics)
        self.metrics_timer.start(2000)

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

    def _populate_external_app_menu(self) -> None:
        """Dynamically populates the File -> Open in External Application menu for the active tab."""
        self.external_app_menu.clear()
        widget = self.tabs_main.currentWidget()
        path = getattr(widget, "current_path", None) if widget else None
        if not path or not os.path.exists(path):
            action = self.external_app_menu.addAction("No active PDF open")
            action.setEnabled(False)
            return

        def _open_app(cmd):
            try:
                if cmd == "default":
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                else:
                    subprocess.Popen([cmd, path])
            except Exception as e:
                QMessageBox.warning(
                    self, "Launch Error", f"Could not launch {cmd}:\n{e}"
                )

        def _choose_app():
            app_path, _ = QFileDialog.getOpenFileName(
                self,
                "Choose Application to Open PDF",
                "/usr/bin",
                "Executables (*)",
            )
            if app_path:
                try:
                    subprocess.Popen([app_path, path])
                except Exception as e:
                    QMessageBox.warning(
                        self, "Launch Error", f"Could not launch {app_path}:\n{e}"
                    )

        act_default = self.external_app_menu.addAction("System Default Viewer")
        act_default.triggered.connect(lambda: _open_app("default"))
        self.external_app_menu.addSeparator()

        browsers = [
            ("Google Chrome", ["google-chrome-stable", "google-chrome"]),
            ("Mozilla Firefox", ["firefox"]),
            ("Chromium", ["chromium-browser", "chromium"]),
            ("Brave Browser", ["brave-browser", "brave"]),
            ("Microsoft Edge", ["microsoft-edge-stable", "microsoft-edge"]),
        ]
        import shutil

        for label, binaries in browsers:
            for b in binaries:
                if shutil.which(b):
                    act = self.external_app_menu.addAction(label)
                    act.triggered.connect(
                        lambda checked=False, cmd=b: _open_app(cmd)
                    )
                    break

        self.external_app_menu.addSeparator()
        act_choose = self.external_app_menu.addAction(
            "Choose Other Application..."
        )
        act_choose.triggered.connect(_choose_app)

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
            self.settings.setValue(
                "app/default_dir", dlg.txt_default_dir.text().strip()
            )
            self.settings.setValue("app/autoscroll_speed", dlg.spin_autoscroll.value())
            self.settings.setValue(
                "app/floating_fs_btn", dlg.cb_floating_fs.isChecked()
            )
            old_scale = self.settings.value("app/ui_scale", 100, type=int)
            new_scale = dlg.slider_scale.value()
            if old_scale != new_scale:
                self.settings.setValue("app/ui_scale", new_scale)
                QMessageBox.information(
                    self,
                    "Restart Required",
                    f"Display size changed to {new_scale}%.\n\nPlease restart Riemann for the new display scaling to take effect across the entire interface.",
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

            current_idx = self.tabs_main.currentIndex()
            insert_idx = (
                current_idx + 1 if current_idx != -1 else self.tabs_main.count()
            )

            self.tabs_main.insertTab(insert_idx, reader, pdf_icon, "New Tab")
            self._update_tab_tooltip(self.tabs_main, insert_idx)
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

        current_idx = target.currentIndex()
        insert_idx = current_idx + 1 if current_idx != -1 else target.count()

        target.insertTab(insert_idx, browser, label)
        self._update_tab_tooltip(target, insert_idx)
        new_tab = target.widget(insert_idx)

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

    def open_file_smart(self) -> None:
        """
        Handles the "Open Document" action.
        Routes PDFs/Markdown to ReaderTab and HTML/CSS/JS to BrowserTab.
        Opens in the current tab if it's an empty reader, otherwise opens a new tab.
        Supports selecting multiple files.
        """
        start_dir = get_dialog_directory(self.settings)
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Document",
            start_dir,
            "Supported Files (*.pdf *.PDF *.md *.html *.css *.js);;All Files (*)",
        )
        if not paths:
            return

        save_last_directory(self.settings, paths[0])

        for i, path in enumerate(paths):
            self.add_to_history(path)
            is_web_file = path.lower().endswith((".html", ".css", ".js"))
            self.add_to_history(path, "web" if is_web_file else "pdf")

            if i == 0:
                current = self.tabs_main.currentWidget()
                if (
                    not is_web_file
                    and isinstance(current, ReaderTab)
                    and not current.current_path
                ):
                    current.load_document(path)
                    self.tabs_main.setTabText(
                        self.tabs_main.currentIndex(), os.path.basename(path)
                    )
                elif is_web_file:
                    self.new_browser_tab(f"file:///{path.replace(chr(92), '/')}")
                else:
                    self.new_pdf_tab(path)

            else:
                if is_web_file:
                    self.new_browser_tab(f"file:///{path.replace(chr(92), '/')}")
                else:
                    self.new_pdf_tab(path)

    def show_toast(self, msg: str) -> None:
        """
        Displays a temporary UI toast notification to the user.

        Args:
            msg (str): The notification text to display.
        """
        self.lbl_toast = QLabel(self)
        self.lbl_toast.setStyleSheet(
            "background: #333; color: white; padding: 10px; border-radius: 5px;"
        )
        self.lbl_toast.setText(msg)
        self.lbl_toast.adjustSize()
        self.lbl_toast.move(
            (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
        )
        self.lbl_toast.show()
        QTimer.singleShot(4000, self.lbl_toast.hide)

    def toggle_split_view(self) -> None:
        """
        Toggles the horizontal split-screen view.
        Moves the current tab to the side view if opening, or hides it if empty.
        """
        if getattr(self, "is_folder_session_active", False):
            self.show_toast("Split view is disabled during an active folder session.")
            return

        current = self.tabs_main.currentWidget()
        if not current:
            return

        was_hidden = self.tabs_side.isHidden()
        idx = self.tabs_main.indexOf(current)
        text = self.tabs_main.tabText(idx)
        self.tabs_main.removeTab(idx)

        if was_hidden:
            self.tabs_side.show()
            QApplication.processEvents()

            total = self.splitter.width()
            self.splitter.setSizes([int(total * 0.7), int(total * 0.3)])

        new_idx = self.tabs_side.addTab(current, text)
        self.tabs_side.setCurrentIndex(new_idx)

        current.show()
        current.setFocus()

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
            elif isinstance(widget, ReaderTab):
                widget.cleanup()

        self.tabs_main.removeTab(index)
        try:
            import shiboken6

            if shiboken6.isValid(widget):
                widget.deleteLater()
        except RuntimeError:
            pass
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
            elif isinstance(widget, ReaderTab):
                widget.cleanup()

            if widget == getattr(self, "tree_signatures", None):
                active_main = self.tabs_main.currentWidget()
                if active_main:
                    active_main._sig_panel_dismissed = True
            else:
                try:
                    import shiboken6

                    if shiboken6.isValid(widget):
                        widget.deleteLater()
                except RuntimeError:
                    pass

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
                self.exit_fullscreen()

    def _handle_escape(self) -> None:
        """
        Handles the Escape key event to exit fullscreen.
        """
        if getattr(self, "_reader_fullscreen", False):
            self.exit_fullscreen()

    def _serialize_tab_state(self, tab_widget: QTabWidget) -> List[dict]:
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
            if isinstance(wid, FolderHomeTab):
                tabs_data.append({"type": "folder_home", "data": ""})
            elif isinstance(wid, ReaderTab) and getattr(wid, "current_path", None):
                tabs_data.append(
                    {"type": "pdf", "data": os.path.abspath(wid.current_path)}
                )

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

    def _auto_save_session(self) -> None:
        """
        Periodically writes the active session state to settings so an abrupt
        PC shutdown or application termination does not lose the session.
        """
        if self.incognito or not getattr(self, "restore_session", True):
            return

        main_tabs = self._serialize_tab_state(self.tabs_main)
        side_tabs = self._serialize_tab_state(self.tabs_side)

        if main_tabs or side_tabs:
            self.settings.setValue("session/main_tabs", main_tabs)
            self.settings.setValue("session/side_tabs", side_tabs)
            self.settings.setValue(
                "session/main_active_idx", self.tabs_main.currentIndex()
            )
            self.settings.setValue(
                "session/side_active_idx", self.tabs_side.currentIndex()
            )
            self.settings.sync()

    def restore_previous_session(self) -> None:
        """
        Explicitly restores the last opened tabs from history/settings.
        """
        main_saved = self.settings.value("session/main_tabs", [])
        side_saved = self.settings.value("session/side_tabs", [])

        if not main_saved and not side_saved:
            QMessageBox.information(
                self,
                "Restore Session",
                "No previous session tab data was found in history.",
            )
            return

        self._restore_tabs_from_settings("session/main_tabs", self.tabs_main)
        self._restore_tabs_from_settings("session/side_tabs", self.tabs_side)

        if self.tabs_side.count() > 0:
            self.tabs_side.show()
            if self.settings.value("splitter/state"):
                self.splitter.restoreState(self.settings.value("splitter/state"))  # type: ignore

        self.show_toast("Restored last session tabs 📂")

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

        self.settings.setValue("session/main_tabs", self._serialize_tab_state(self.tabs_main))
        self.settings.setValue("session/side_tabs", self._serialize_tab_state(self.tabs_side))

        if getattr(self, "is_folder_session_active", False) and hasattr(
            self, "explorer_panel"
        ):
            self.settings.setValue(
                "session/active_folder", self.explorer_panel.current_path
            )
        else:
            self.settings.setValue("session/active_folder", "")

        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())

        self.settings.setValue("session/main_active_idx", self.tabs_main.currentIndex())
        self.settings.setValue("session/side_active_idx", self.tabs_side.currentIndex())
        self.settings.setValue("session/clean_exit", True)

        self.settings.sync()
        self._kill_all_media_safely()
        super().closeEvent(event)

    def exit_fullscreen(self) -> None:
        """Helper to fully exit any fullscreen reading states."""
        self._fullscreen_state = 0
        self._reader_fullscreen = False

        self.fs_tracker_timer.stop()
        self.floating_fs_btn.hide()
        self.floating_fs_timer.stop()

        self.menuBar().show()
        self.tabs_main.tabBar().show()
        self.tabs_side.tabBar().show()
        self._set_tabs_toolbar_visible(True)

        if getattr(self, "_was_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()

        self._broadcast_fullscreen_icon(0)
        self._update_floating_fs_btn_icon()

    def toggle_reader_fullscreen(self) -> None:
        """
        Toggles global fullscreen mode cycling through 3 states:
        Normal -> Reading Mode (toolbar visible) -> Pure Fullscreen.
        """
        state = getattr(self, "_fullscreen_state", 0)

        if state == 0:
            self._fullscreen_state = 1
            self._was_maximized = self.isMaximized()
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            self.tabs_side.tabBar().hide()
            self._set_tabs_toolbar_visible(True)
            self._reader_fullscreen = True
            self.showFullScreen()
            self._broadcast_fullscreen_icon(1)
            self.fs_tracker_timer.start()

        elif state == 1:
            self._fullscreen_state = 2
            self._set_tabs_toolbar_visible(False)
            self._broadcast_fullscreen_icon(2)
            self.fs_tracker_timer.start()

        else:
            self.exit_fullscreen()
        self._update_floating_fs_btn_icon()

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
        self._update_floating_btn_style()

        if hasattr(self, "explorer_panel"):
            self.explorer_panel.update_theme(self.dark_mode)

        if hasattr(self, "_update_global_metrics"):
            self._update_global_metrics()

        for tab_widget in (self.tabs_main, self.tabs_side):
            for i in range(tab_widget.count()):
                w = tab_widget.widget(i)
                if hasattr(w, "_update_icons"):
                    w._update_icons()

        self.refresh_signature_panel()

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
        list_folder = create_list(self.history_manager.get_list("folder"))

        tabs.addTab(list_web, "Web History")
        tabs.addTab(list_pdf, "PDF History")
        tabs.addTab(list_folder, "Folder History")
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
            if tabs.currentIndex() == 0:
                current_list = list_web
            elif tabs.currentIndex() == 1:
                current_list = list_pdf
            else:
                current_list = list_folder

            item = current_list.currentItem()
            if not item:
                return

            data = item.text()
            dialog.accept()

            if tabs.currentIndex() == 2:
                self.open_folder(data)
            elif tabs.currentIndex() == 1:
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
            self._update_tab_tooltip(self.tabs_main, idx)
            self._update_window_title()
            return

        if self.tabs_side.isVisible():
            idx = self.tabs_side.indexOf(browser)
            if idx != -1:
                self.tabs_side.setTabText(idx, display_title)
                self._update_tab_tooltip(self.tabs_side, idx)
                self._update_window_title()

    def _update_tab_tooltip(self, tab_widget: QTabWidget, idx: int) -> None:
        """
        Updates the tab tooltip with full name and full file location for PDFs,
        or full title and top level subdomain for browser pages.
        """
        if idx < 0 or idx >= tab_widget.count():
            return
        widget = tab_widget.widget(idx)
        if not widget:
            return

        if isinstance(widget, ReaderTab) or hasattr(widget, "current_path"):
            path = getattr(widget, "current_path", None)
            if path:
                filename = os.path.basename(path)
                abs_path = os.path.abspath(path)
                doc_title = None
                if hasattr(widget, "document_metadata") and isinstance(
                    widget.document_metadata, dict
                ):
                    doc_title = widget.document_metadata.get("title")

                if doc_title and doc_title.strip() and doc_title.strip() != filename:
                    tooltip = f"{doc_title.strip()}\nFile: {filename}\nLocation: {abs_path}"
                else:
                    tooltip = f"{filename}\nLocation: {abs_path}"
            else:
                tooltip = "New PDF Document"

        elif isinstance(widget, BrowserTab) or hasattr(widget, "web"):
            title = widget.web.title() if hasattr(widget, "web") else ""
            url = widget.web.url() if hasattr(widget, "web") else QUrl()
            url_str = url.toString() if hasattr(url, "toString") else ""

            if "homepage.html" in url_str:
                subdomain = "Start Page"
                display_name = title or "Riemann Home"
            elif url.isLocalFile():
                subdomain = "Local File"
                display_name = title or os.path.basename(url.toLocalFile())
            else:
                host = url.host() if hasattr(url, "host") else ""
                subdomain = host or "Web"
                display_name = title or host or "Web Page"

            if display_name and subdomain and display_name != subdomain:
                tooltip = f"{display_name}\nDomain: {subdomain}"
            else:
                tooltip = display_name or subdomain

        elif isinstance(widget, FolderHomeTab) or hasattr(widget, "current_path"):
            path = getattr(widget, "current_path", "Workspace")
            tooltip = f"Workspace\nLocation: {path}"
        else:
            tooltip = tab_widget.tabText(idx)

        tab_widget.setTabToolTip(idx, tooltip)

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
                elif path.lower().endswith((".html", ".css", ".js")):
                    self.new_browser_tab(f"file:///{path.replace(chr(92), '/')}")
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

        if hasattr(widget, "get_open_with_menu") and getattr(widget, "current_path", None):
            open_with_sub = widget.get_open_with_menu(menu)
            open_with_sub.setTitle("Open in External Application ↗")
            menu.addMenu(open_with_sub)

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

    def force_show_signatures(self) -> None:
        """Manually un-dismisses and reopens the signature panel."""
        active_main = self.tabs_main.currentWidget()
        if active_main:
            active_main._sig_panel_dismissed = False
            self.refresh_signature_panel()

    def show_convert_dialog(self) -> None:
        """Triggers the new conversion interface."""
        dialog = ConvertDialog(self)
        if dialog.exec():
            out_path = dialog.txt_output.text()
            if out_path.endswith(".html") and os.path.exists(out_path):
                reply = QMessageBox.question(
                    self,
                    "Open Output",
                    "Conversion successful. Open the HTML file in a new tab now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.new_browser_tab(f"file:///{out_path.replace(chr(92), '/')}")

    def show_compress_dialog(self) -> None:
        """Triggers the new compression interface."""
        dialog = CompressDialog(self)
        if dialog.exec():
            out_path = dialog.txt_output.text()
            if out_path.endswith(".pdf") and os.path.exists(out_path):
                reply = QMessageBox.question(
                    self,
                    "Open Output",
                    "Compression successful. Open the compressed PDF in a new tab?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.new_pdf_tab(out_path)

    def _get_icon_img_tag(self, icon_base: str) -> str:
        """Generates an HTML img tag pointing to the correct local SVG based on dark mode."""
        suffix = "-white" if getattr(self, "dark_mode", False) else ""
        icon_name = f"{icon_base}{suffix}.svg"

        path = get_resource_path(os.path.join("assets", "icons", icon_name))
        if not os.path.exists(path) and suffix:
            path = get_resource_path(
                os.path.join("assets", "icons", f"{icon_base}.svg")
            )

        path = path.replace(os.sep, "/")
        return f'<img src="file:///{path}" width="14" height="14" style="vertical-align: middle;">'

    def _update_global_metrics(self) -> None:
        """Updates the global metrics ticker in the menu bar with icons and real speeds."""
        uptime_seconds = int(time.time() - getattr(self, "_start_time", time.time()))
        m, s = divmod(uptime_seconds, 60)
        h, m = divmod(m, 60)
        uptime_str = f"{h:02d}:{m:02d}:{s:02d}"

        mem_str = "--"
        net_str = f"{self._get_icon_img_tag('arrow-big-down-dash')} -- MB/s &nbsp; {self._get_icon_img_tag('arrow-big-up-dash')} -- MB/s"
        bat_str = f"{self._get_icon_img_tag('battery')} --%"

        if psutil:
            try:
                process = psutil.Process(os.getpid())
                mem_bytes = process.memory_info().rss
                for child in process.children(recursive=True):
                    mem_bytes += child.memory_info().rss
                mem_mb = mem_bytes / (1024 * 1024)
                mem_str = f"{mem_mb:.1f} MB"

                current_net_io = psutil.net_io_counters()
                current_time = time.time()

                if getattr(self, "_last_net_io", None):
                    dt = current_time - self._last_net_time
                    if dt > 0:
                        recv_speed = (
                            (current_net_io.bytes_recv - self._last_net_io.bytes_recv)
                            / dt
                            / (1024 * 1024)
                        )
                        sent_speed = (
                            (current_net_io.bytes_sent - self._last_net_io.bytes_sent)
                            / dt
                            / (1024 * 1024)
                        )
                        net_str = f"{self._get_icon_img_tag('arrow-big-down-dash')} {recv_speed:.2f} MB/s &nbsp; {self._get_icon_img_tag('arrow-big-up-dash')} {sent_speed:.2f} MB/s"

                self._last_net_io = current_net_io
                self._last_net_time = current_time

                battery = psutil.sensors_battery()
                if battery:
                    if battery.power_plugged:
                        bat_icon = "battery-charging"
                    elif battery.percent > 80:
                        bat_icon = "battery-full"
                    elif battery.percent > 30:
                        bat_icon = "battery-medium"
                    elif battery.percent > 10:
                        bat_icon = "battery-low"
                    else:
                        bat_icon = "battery"

                    bat_str = (
                        f"{self._get_icon_img_tag(bat_icon)} {battery.percent:.2f}%"
                    )
            except Exception:
                pass

        clock_str = time.strftime("%I:%M:%S %p")
        self.metrics_label.setText(
            f" <b>{clock_str}</b> &nbsp;|&nbsp; Mem: {mem_str} &nbsp;|&nbsp; Uptime: {uptime_str} &nbsp;|&nbsp; {net_str} &nbsp;|&nbsp; {bat_str} "
        )

    def show_ytdlp_downloads(self) -> None:
        """
        Shows the dedicated yt-dlp non-modal download manager dialog.
        """
        self.ytdlp_manager_dialog.show()
        self.ytdlp_manager_dialog.raise_()

    def _update_floating_btn_style(self) -> None:
        """Applies dynamic theme styling to the floating fullscreen button."""
        bg = (
            "rgba(40, 40, 40, 0.85)"
            if getattr(self, "dark_mode", False)
            else "rgba(220, 220, 220, 0.85)"
        )
        fg = "#fff" if getattr(self, "dark_mode", False) else "#000"
        border = "rgba(100, 100, 100, 0.5)"
        self.floating_fs_btn.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 8px;"
        )
        self._update_floating_fs_btn_icon()

    def _update_floating_fs_btn_icon(self) -> None:
        """Retrieves and applies the correct icon state to the floating button."""
        state = getattr(self, "_fullscreen_state", 0)
        icons = {0: "panel-top.svg", 1: "maximize.svg", 2: "minimize.svg"}
        icon_name = icons.get(state, "maximize.svg")

        suffix = "-white" if getattr(self, "dark_mode", False) else ""
        if suffix and not icon_name.endswith(f"{suffix}.svg"):
            icon_name = icon_name.replace(".svg", f"{suffix}.svg")

        path = get_resource_path(os.path.join("assets", "icons", icon_name))
        if not os.path.exists(path) and suffix:
            path = get_resource_path(
                os.path.join("assets", "icons", icon_name.replace(suffix, ""))
            )

        self.floating_fs_btn.setIcon(QIcon(path))

    def _broadcast_fullscreen_icon(self, state: int) -> None:
        """Helper to safely sync the fullscreen icon across all open tabs simultaneously."""
        for target in (self.tabs_main, self.tabs_side):
            for i in range(target.count()):
                widget = target.widget(i)
                if hasattr(widget, "update_fullscreen_icon"):
                    widget.update_fullscreen_icon(state)

    def _track_mouse_for_fs(self) -> None:
        """
        Polls the mouse position during fullscreen to reliably show UI elements,
        bypassing event-swallowing issues in QWebEngineView and QScrollArea.
        """
        if not getattr(self, "_reader_fullscreen", False):
            return

        local_pos = self.mapFromGlobal(QCursor.pos())

        if local_pos.y() < 40:
            if self.hover_timer.isActive():
                self.hover_timer.stop()
            self._reveal_controls(True)
        elif local_pos.y() > 100:
            if not self.hover_timer.isActive():
                self.hover_timer.start()

        if self.settings.value("app/floating_fs_btn", True, type=bool):
            if local_pos.x() > self.width() - 150 and local_pos.y() < 100:
                self._update_floating_fs_btn_icon()
                self.floating_fs_btn.move(
                    self.width() - self.floating_fs_btn.width() - 20, 20
                )
                if not self.floating_fs_btn.isVisible():
                    self.floating_fs_btn.show()
                    self.floating_fs_btn.raise_()
                self.floating_fs_timer.stop()
            else:
                if (
                    self.floating_fs_btn.isVisible()
                    and not self.floating_fs_timer.isActive()
                ):
                    self.floating_fs_timer.start()

    def _populate_fav_file_menu(self):
        self.fav_file_menu.clear()
        favs = QSettings("Riemann", "Favorites").value("folders", [], type=list)
        if not favs:
            self.fav_file_menu.addAction("No favorites yet").setEnabled(False)
            return
        for f in favs:
            action = self.fav_file_menu.addAction(os.path.basename(f))
            action.triggered.connect(
                lambda checked=False, path=f: self._open_file_dialog_in_fav(path)
            )

    def _populate_fav_folder_menu(self):
        self.fav_folder_menu.clear()
        favs = QSettings("Riemann", "Favorites").value("folders", [], type=list)
        if not favs:
            self.fav_folder_menu.addAction("No favorites yet").setEnabled(False)
            return
        for f in favs:
            action = self.fav_folder_menu.addAction(os.path.basename(f))
            action.triggered.connect(
                lambda checked=False, path=f: self.open_folder(path)
            )

    def manage_favorites(self):
        ManageFavoritesDialog(self).exec()

    def _open_file_dialog_in_fav(self, path):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Document",
            path,
            "Supported Files (*.pdf *.PDF *.md *.html *.css *.js);;All Files (*)",
        )
        if paths:
            for p in paths:
                self._open_single_file(p)

    def open_folder(self, path=None):
        if not path:
            start_dir = get_dialog_directory(self.settings)
            path = QFileDialog.getExistingDirectory(self, "Open Folder", start_dir)
            if not path:
                return

        save_last_directory(self.settings, path)
        self.history_manager.add(path, "folder")

        if self.tabs_side.count() > 0:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Active Side Tabs")
            msg_box.setText(
                "Opening a folder session disables split view. How do you want to handle existing side tabs?"
            )
            btn_move = msg_box.addButton(
                "Move to Main", QMessageBox.ButtonRole.AcceptRole
            )
            btn_new_win = msg_box.addButton(
                "New Window", QMessageBox.ButtonRole.AcceptRole
            )
            btn_close = msg_box.addButton(
                "Close Side Tabs", QMessageBox.ButtonRole.DestructiveRole
            )
            msg_box.exec()

            if msg_box.clickedButton() == btn_move:
                while self.tabs_side.count() > 0:
                    widget = self.tabs_side.widget(0)
                    text = self.tabs_side.tabText(0)
                    icon = self.tabs_side.tabIcon(0)
                    self.tabs_side.removeTab(0)
                    self.tabs_main.addTab(widget, icon, text)

            elif msg_box.clickedButton() == btn_new_win:
                self.new_window()
                urls = []

                for i in range(self.tabs_side.count()):
                    w = self.tabs_side.widget(i)
                    if hasattr(w, "current_path") and w.current_path:
                        urls.append(w.current_path)
                    elif hasattr(w, "web"):
                        urls.append(w.web.url().toString())

                for i in range(self.tabs_side.count() - 1, -1, -1):
                    self.close_side_tab(i)
                for u in urls:
                    if u.endswith(".pdf") or u.endswith(".md"):
                        self._new_window_ref.new_pdf_tab(u)
                    else:
                        self._new_window_ref.new_browser_tab(u)

            elif msg_box.clickedButton() == btn_close:
                for i in range(self.tabs_side.count() - 1, -1, -1):
                    self.close_side_tab(i)

        self.tabs_side.hide()
        self.is_folder_session_active = True
        self.explorer_panel.set_path(path)
        self.explorer_panel.show()
        if hasattr(self, "show_toast"):
            self.show_toast(f"Folder session connected: {os.path.basename(path)}")

    def close_folder(self):
        """Exits the folder session and restores normal app behavior."""
        if not getattr(self, "is_folder_session_active", False):
            return

        self.explorer_panel.hide()
        self.is_folder_session_active = False

        if getattr(self, "preview_tab_widget", None):
            idx = self.tabs_main.indexOf(self.preview_tab_widget)
            if idx != -1:
                self.tabs_main.removeTab(idx)
            if hasattr(self.preview_tab_widget, "cleanup"):
                self.preview_tab_widget.cleanup()
            self.preview_tab_widget.deleteLater()
            self.preview_tab_widget = None

        if hasattr(self, "show_toast"):
            self.show_toast("Folder session closed.")

    def _open_single_file(self, path: str, insert_idx: int = -1):
        file_type = self._detect_file_type(path)
        self.add_to_history(path, "web" if file_type == "text" else "pdf")

        target = self.tabs_main

        if file_type == "text":
            tab = self.new_browser_tab(
                f"file:///{path.replace(chr(92), '/')}", background=True
            )
            if insert_idx != -1:
                target.removeTab(target.count() - 1)
                target.insertTab(insert_idx, tab, os.path.basename(path))
                target.setCurrentIndex(insert_idx)
        else:
            reader = ReaderTab()
            reader.signatures_detected.connect(lambda _: self.refresh_signature_panel())
            reader.load_document(path)
            icon_path = get_resource_path(os.path.join("assets", "icons", "pdf.png"))
            idx = insert_idx if insert_idx != -1 else target.count()
            target.insertTab(idx, reader, QIcon(icon_path), os.path.basename(path))
            target.setCurrentIndex(idx)

    def _on_file_single_clicked(self, path: str):
        if not os.path.isfile(path):
            return

        file_type = self._detect_file_type(path)

        idx = -1
        if getattr(self, "preview_tab_widget", None):
            idx = self.tabs_main.indexOf(self.preview_tab_widget)

        if file_type == "text":
            new_preview = PreviewTextTab(path, getattr(self, "dark_mode", True))
        else:
            new_preview = PreviewReaderTab()
            new_preview.load_document(path)

        suffix = "-white.svg" if getattr(self, "dark_mode", False) else ".svg"
        icon_path = get_resource_path(
            os.path.join("assets", "icons", f"scan-eye{suffix}")
        )
        if not os.path.exists(icon_path):
            icon_path = get_resource_path(
                os.path.join("assets", "icons", "scan-eye.svg")
            )

        title = f"Preview: {os.path.basename(path)}"

        if idx != -1:
            self.tabs_main.removeTab(idx)
            if hasattr(self.preview_tab_widget, "cleanup"):
                self.preview_tab_widget.cleanup()

            self.preview_tab_widget.deleteLater()
            self.tabs_main.insertTab(idx, new_preview, QIcon(icon_path), title)
            self.tabs_main.setCurrentIndex(idx)
        else:
            new_idx = self.tabs_main.addTab(new_preview, QIcon(icon_path), title)
            self.tabs_main.setCurrentIndex(new_idx)

        self.preview_tab_widget = new_preview
        self._update_window_title()

    def _on_file_double_clicked(self, path: str):
        if not os.path.isfile(path):
            return

        if (
            getattr(self, "preview_tab_widget", None)
            and getattr(self.preview_tab_widget, "current_path", "") == path
        ):
            self.promote_preview_tab()
            return

        self._open_single_file(path)

    def promote_preview_tab(self):
        if not getattr(self, "preview_tab_widget", None):
            return

        idx = self.tabs_main.indexOf(self.preview_tab_widget)
        if idx == -1:
            return

        path = self.preview_tab_widget.current_path
        self.tabs_main.removeTab(idx)
        if hasattr(self.preview_tab_widget, "cleanup"):
            self.preview_tab_widget.cleanup()
        self.preview_tab_widget.deleteLater()
        self.preview_tab_widget = None

        self._open_single_file(path, insert_idx=idx)

    def _detect_file_type(self, path: str) -> str:
        """Determines if a file is a PDF (by extension or magic bytes) or generic text/web."""
        if path.lower().endswith(".pdf"):
            return "pdf"

        try:
            with open(path, "rb") as f:
                if f.read(5) == b"%PDF-":
                    return "pdf"
        except Exception:
            pass

        return "text"

    def new_folder_home_tab(self, target_widget=None):
        """Spawns the workspace/folder dashboard."""
        if target_widget is None:
            target_widget = self.tabs_main

        tab = FolderHomeTab(self, self.dark_mode)
        icon_path = get_resource_path(os.path.join("assets", "icons", "folder.png"))

        idx = target_widget.addTab(tab, QIcon(icon_path), "Workspaces")
        target_widget.setCurrentIndex(idx)


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

    init_settings = QSettings("Riemann", "PDFReader")
    ui_scale = init_settings.value("app/ui_scale", 100, type=int)
    if ui_scale and ui_scale != 100:
        os.environ["QT_SCALE_FACTOR"] = f"{ui_scale / 100.0:.2f}"

    app = QApplication(sys.argv)
    app.setApplicationName("Riemann")
    app.setDesktopFileName("Riemann")

    QPixmapCache.setCacheLimit(153600)

    args = app.arguments()
    is_new_window = "--new-window" in args
    files_to_open = []
    for arg in args[1:]:
        if not arg.startswith("-"):
            clean_arg = QUrl(arg).toLocalFile() if arg.startswith("file://") else arg
            if os.path.isfile(clean_arg):
                files_to_open.append(os.path.abspath(clean_arg))

    server_name = "RiemannSingleInstance"

    if not is_new_window:
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
            """
            Reads incoming file paths asynchronously from the local IPC socket connection.

            Called when a secondary application instance attempts to open new files. It parses
            the incoming pipe-delimited payload and opens the requested documents or URLs as
            new tabs within the existing primary window.
            """
            msg = client.readAll().data().decode("utf-8")
            if msg:
                for path in msg.split("|"):
                    clean_p = (
                        QUrl(path).toLocalFile()
                        if path.startswith("file://")
                        else path
                    )
                    if os.path.isfile(clean_p):
                        window.new_pdf_tab(os.path.abspath(clean_p))

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
    Detects if running as a Linux executable and installs/updates
    the .desktop shortcut and icon in the user's local share.
    """
    if not sys.platform.startswith("linux"):
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

        if not os.path.exists(internal_icon_path):
            print(f"Warning: Could not find internal icon at {internal_icon_path}")
            return

        persistent_icon_path = icons_dir / "riemann.png"
        shutil.copy2(internal_icon_path, persistent_icon_path)

        desktop_file_path = apps_dir / f"{app_name}.desktop"
        exe_path = os.path.abspath(sys.argv[0])

        desktop_entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={app_name}\n"
            "GenericName=PDF Reader\n"
            "Comment=A standalone PDF reader and manager\n"
            f'Exec="{exe_path}" %f\n'
            f"Icon={persistent_icon_path}\n"
            "Terminal=false\n"
            "Categories=Office;Viewer;Utility;\n"
            f"StartupWMClass={app_name}\n"
            "MimeType=application/pdf;\n"
            "Actions=NewWindow;\n\n"
            "[Desktop Action NewWindow]\n"
            "Name=New Window\n"
            f'Exec="{exe_path}" --new-window\n'
        )

        with open(desktop_file_path, "w") as f:
            f.write(desktop_entry)

        os.chmod(desktop_file_path, 0o755)
        os.system(f"update-desktop-database {apps_dir} > /dev/null 2>&1")
        print(f"[Riemann] Integrated to desktop menu: {desktop_file_path}")

    except Exception as e:
        print(f"Icon integration warning: {e}")
