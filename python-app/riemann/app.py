"""
Main Application Module.

This module defines the primary window manager (`RiemannWindow`) and the
application entry point. It orchestrates the UI layout, tab management
(split-view), global keyboard shortcuts, and session persistence.
"""

import os
import sys

os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    bundle_dir = getattr(sys, "_MEIPASS")
    os.environ["PDFIUM_DYNAMIC_LIB_PATH"] = bundle_dir

import shutil
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import (
    QEvent,
    QObject,
    QSettings,
    QStandardPaths,
    QStringListModel,
    Qt,
    QTimer,
)
from PySide6.QtGui import QCloseEvent, QCursor, QIcon, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QListWidget,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core.managers import BookmarksManager, DownloadManager, HistoryManager
from .ui.browser import BrowserTab
from .ui.components import DraggableTabWidget
from .ui.reader import ReaderTab


def get_resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller"""
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
            parent: The main window instance, used to retrieve current settings.
        """
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(400, 200)

        form_layout = QFormLayout(self)
        self.setLayout(form_layout)

        self.cb_dark = QCheckBox()
        self.cb_dark.setChecked(parent.dark_mode)
        form_layout.addRow("Dark Mode:", self.cb_dark)

        self.cb_auto_pdf = QCheckBox()
        self.cb_auto_pdf.setChecked(
            parent.settings.value("browser/auto_open_pdf", False, type=bool)
        )
        form_layout.addRow("Auto-open Downloaded PDFs:", self.cb_auto_pdf)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        form_layout.addRow(self.button_box)


class RiemannWindow(QMainWindow):
    """
    The Main Window Manager for the Riemann application.

    Handles global state, split-view tab management, history tracking,
    shortcuts, and session persistence.
    """

    def __init__(self, incognito: bool = False, restore_session: bool = True) -> None:
        """
        Initializes the main application window.

        Args:
            incognito: If True, history will not be recorded.
            restore_session: If True, attempts to restore tabs from the last session.
        """
        super().__init__()
        self.incognito = incognito
        self.restore_session = restore_session

        if self.incognito:
            self.setWindowTitle("Riemann Reader (Incognito)")
            self.setProperty("incognito", True)
            self.web_profile = QWebEngineProfile()
        else:
            self.setWindowTitle("Riemann Reader")
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

        user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        self.web_profile.setHttpUserAgent(user_agent)

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
        self.splitter.addWidget(self.tabs_main)

        self.tabs_side = DraggableTabWidget()
        self.tabs_side.setTabsClosable(True)
        self.tabs_side.tabCloseRequested.connect(self.close_side_tab)
        self.tabs_side.hide()
        self.splitter.addWidget(self.tabs_side)

        self.setup_menu()
        self._init_shortcuts()
        self._restore_session()

        self.setMouseTracking(True)
        self.installEventFilter(self)

        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(500)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._check_auto_hide)

    def _init_shortcuts(self) -> None:
        """Initializes global keyboard shortcuts."""
        shortcuts = [
            ("Ctrl+Q", self.close),
            ("Ctrl+W", self.close_active_tab),
            ("Ctrl+Shift+T", self.restore_last_closed_tab),
            ("Ctrl+\\", self.toggle_split_view),
            ("N", self.toggle_theme),
            (Qt.Key.Key_F11, self.toggle_reader_fullscreen),
            (Qt.Key.Key_Escape, self._handle_escape),
        ]

        for seq, slot in shortcuts:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
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
            if event.pos().y() < 10:
                self._reveal_controls(True)
            elif event.pos().y() > 100:
                self.hover_timer.start()

        return super().eventFilter(source, event)

    def next_tab(self):
        idx = self.tabs_main.currentIndex()
        if idx < self.tabs_main.count() - 1:
            self.tabs_main.setCurrentIndex(idx + 1)
        else:
            self.tabs_main.setCurrentIndex(0)

    def prev_tab(self):
        idx = self.tabs_main.currentIndex()
        if idx > 0:
            self.tabs_main.setCurrentIndex(idx - 1)
        else:
            self.tabs_main.setCurrentIndex(self.tabs_main.count() - 1)

    def _reveal_controls(self, show: bool):
        """Helper to show/hide the tab bar and menu bar."""
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
        """Hides controls if mouse is not at the top."""
        mouse_pos = self.mapFromGlobal(QCursor.pos())
        if mouse_pos.y() > 100 and getattr(self, "_reader_fullscreen", False):
            self._reveal_controls(False)

    # --- History & Session Management ---

    def add_to_history(self, item: str, item_type: str = "web") -> None:
        """
        Adds an item to the history manager and updates the autocomplete model.

        Args:
            item: The URL or file path to add.
            item_type: The type of item ("web" or "pdf").
        """
        if self.incognito:
            return

        self.history_manager.add(item, item_type)
        self.history_model.setStringList(self.history_manager.get_model_data())

    def _restore_session(self) -> None:
        """
        Restores the window geometry and open tabs from the previous session.
        Defaults to a single browser tab if no session exists or incognito is active.
        """
        if self.incognito or not self.restore_session:
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

        if self.tabs_main.count() == 0:
            self.new_pdf_tab()

    def _restore_tabs_from_settings(self, key: str, target_widget: QTabWidget) -> None:
        """
        Parses settings data to recreate tabs.

        Args:
            key: The QSettings key to read from.
            target_widget: The QTabWidget to populate.
        """
        items = self.settings.value(key, [], type=list)
        # Ensure items is a list (QSettings can sometimes return a single item as non-list)
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

    # --- Tab Creation Helpers ---

    def _add_pdf_tab(
        self, path: str, target_widget: QTabWidget, restore_state: bool = False
    ) -> None:
        """
        Internal helper to instantiate and add a ReaderTab.

        Args:
            path: Path to the PDF file.
            target_widget: The tab widget to add the tab to.
            restore_state: Whether to restore scroll position/zoom.
        """
        self.add_to_history(path, "pdf")
        reader = ReaderTab()
        reader.load_document(path, restore_state=restore_state)
        idx = target_widget.addTab(reader, os.path.basename(path))
        target_widget.setCurrentIndex(idx)

    def _add_browser_tab(self, url: str, target_widget: QTabWidget) -> None:
        """
        Internal helper to instantiate and add a BrowserTab.
        Passing the shared profile to avoid database locking.

        Args:
            url: The URL to load.
            target_widget: The tab widget to add the tab to.
        """
        if self.incognito:
            use_profile = self.web_profile
        else:
            use_profile = self.web_profile

        browser = BrowserTab(url, profile=use_profile, dark_mode=self.dark_mode)
        browser.completer.setModel(self.history_model)

        target_widget.addTab(browser, "Loading...")
        i = target_widget.addTab(browser, "New Tab")
        target_widget.setCurrentIndex(i)

        browser.web.urlChanged.connect(
            lambda qurl: self._update_tab_title(browser, qurl)
        )
        browser.web.loadFinished.connect(lambda ok: self._update_tab_title(browser))

    # --- UI Setup ---

    def setup_menu(self) -> None:
        """Configures the main window menu bar actions."""
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("File")

        actions = [
            ("Open PDF", "Ctrl+O", self.open_pdf_smart),
            ("Open New PDF Tab", "Ctrl+T", self.open_pdf_smart),
            (None, None, None),  # Separator
            ("New Browser Tab", "Ctrl+B", lambda: self.new_browser_tab()),
            ("New Window", "Ctrl+N", self.new_window),
            ("New Incognito Tab", "Ctrl+Shift+N", self.new_incognito_window),
            (None, None, None),  # Separator
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

        # View Menu
        view_menu = menubar.addMenu("View")

        view_actions = [
            ("Bookmarks", "Ctrl+K", self.show_bookmarks),
            ("Downloads", "Ctrl+J", self.show_downloads),
            ("History", "Ctrl+H", self.show_history),
            ("Settings", "Ctrl+,", self.show_settings),
            ("Toggle Theme", "Ctrl+D", self.toggle_theme),
        ]

        for name, shortcut, slot in view_actions:
            action = view_menu.addAction(name)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)

    # --- Actions ---

    def show_settings(self) -> None:
        """Displays the configuration dialog and applies changes on acceptance."""
        dlg = SettingsDialog(self)
        if dlg.exec():
            if dlg.cb_dark.isChecked() != self.dark_mode:
                self.toggle_theme()
            self.toggle_auto_pdf(dlg.cb_auto_pdf.isChecked())

    def new_pdf_tab(
        self, path: Optional[str] = None, restore_state: bool = False
    ) -> None:
        """
        Creates a new PDF tab in the main tab widget.

        Args:
            path: Optional file path. If None, creates an empty tab.
            restore_state: Whether to restore reading state.
        """
        if path:
            self._add_pdf_tab(path, self.tabs_main, restore_state)
        else:
            reader = ReaderTab()
            self.tabs_main.addTab(reader, "New Tab")
            self.tabs_main.setCurrentWidget(reader)

    def new_browser_tab(
        self, url: str = "https://www.google.com", incognito: bool = False
    ) -> None:
        """
        Creates a new Web Browser tab.
        Prioritizes the focused side tab widget if active.

        Args:
            url: The URL to navigate to.
            incognito: Whether to enable incognito mode for this tab.
        """
        is_incognito = self.incognito or incognito

        target = self.tabs_main
        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            target = self.tabs_side

        if incognito and not self.incognito:
            tab_profile = QWebEngineProfile()
        else:
            tab_profile = self.web_profile

        browser = BrowserTab(
            url, profile=tab_profile, dark_mode=self.dark_mode, incognito=is_incognito
        )
        browser.completer.setModel(self.history_model)

        label = "Incognito" if incognito else "Loading..."
        target.addTab(browser, label)
        new_tab = target.widget(target.count() - 1)
        target.setCurrentWidget(new_tab)

        if hasattr(new_tab, "txt_url"):
            new_tab.txt_url.setFocus()
            new_tab.txt_url.selectAll()

    def new_window(self) -> None:
        """Spawns a new independent standard application window."""
        self._new_window_ref = RiemannWindow(incognito=False, restore_session=False)
        self._new_window_ref.show()

    def new_incognito_window(self) -> None:
        """Spawns a new independent window in Incognito mode."""
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

        # Handle the first file
        first_path = paths[0]
        self.add_to_history(first_path)
        current = self.tabs_main.currentWidget()

        if isinstance(current, ReaderTab) and not current.current_path:
            # Reuse empty tab
            current.load_document(first_path)
            self.tabs_main.setTabText(
                self.tabs_main.currentIndex(), os.path.basename(first_path)
            )
        else:
            # New tab
            self.new_pdf_tab(first_path)

        # Handle remaining files (always new tabs)
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
            widget: The tab widget being closed (ReaderTab or BrowserTab).
        """
        if isinstance(widget, ReaderTab) and widget.current_path:
            self.closed_tabs_stack.append({"type": "pdf", "data": widget.current_path})
        elif isinstance(widget, BrowserTab):
            self.closed_tabs_stack.append(
                {"type": "web", "data": widget.web.url().toString()}
            )

    def restore_last_closed_tab(self) -> None:
        """Restores the most recently closed tab from the stack."""
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
        Closes a tab in the main tab widget.

        Args:
            index: The index of the tab to close.
        """
        widget = self.tabs_main.widget(index)
        if widget:
            self._record_closed_tab(widget)
            widget.deleteLater()
        self.tabs_main.removeTab(index)

        self._check_all_tabs_closed()

    def close_side_tab(self, index: int) -> None:
        """
        Closes a tab in the side tab widget.

        Args:
            index: The index of the tab to close.
        """
        widget = self.tabs_side.widget(index)
        if widget:
            self._record_closed_tab(widget)
            widget.deleteLater()
        self.tabs_side.removeTab(index)

        if self.tabs_side.count() == 0:
            self.tabs_side.hide()

        self._check_all_tabs_closed()

    def _check_all_tabs_closed(self) -> None:
        """Checks if all tabs are closed and exits fullscreen if necessary."""
        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()

    def _handle_escape(self) -> None:
        """Handles the Escape key event to exit fullscreen."""
        if getattr(self, "_reader_fullscreen", False):
            self.toggle_reader_fullscreen()

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Handles the window close event.
        Saves session data unless in incognito mode.
        """
        if self.incognito or not self.restore_session:
            super().closeEvent(event)
            return

        def get_files(tab_widget: QTabWidget) -> List[dict]:
            tabs_data = []
            for i in range(tab_widget.count()):
                wid = tab_widget.widget(i)
                if isinstance(wid, ReaderTab) and wid.current_path:
                    tabs_data.append({"type": "pdf", "data": wid.current_path})
                elif isinstance(wid, BrowserTab):
                    if not wid.incognito:
                        tabs_data.append(
                            {"type": "web", "data": wid.web.url().toString()}
                        )
            return tabs_data

        self.settings.setValue("session/main_tabs", get_files(self.tabs_main))
        self.settings.setValue("session/side_tabs", get_files(self.tabs_side))
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        super().closeEvent(event)

    def toggle_reader_fullscreen(self) -> None:
        """
        Toggles global fullscreen mode.
        Hides UI elements (menu bar, tab bars, toolbars) for immersive reading.
        """
        if not getattr(self, "_reader_fullscreen", False):
            # Enter Fullscreen
            self._reader_fullscreen = True
            self._was_maximized = self.isMaximized()
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            self.tabs_side.tabBar().hide()
            self._set_tabs_toolbar_visible(False)
            self.showFullScreen()
        else:
            # Exit Fullscreen
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
        """
        for tab_widget in [self.tabs_main, self.tabs_side]:
            for i in range(tab_widget.count()):
                w = tab_widget.widget(i)
                if hasattr(w, "toolbar"):
                    w.toolbar.setVisible(visible)

    def toggle_theme(self) -> None:
        """Toggles the application-wide Dark/Light mode."""
        self.dark_mode = not self.dark_mode
        self.settings.setValue("darkMode", self.dark_mode)

        def update_widget_theme(w: QWidget) -> None:
            if hasattr(w, "dark_mode"):
                setattr(w, "dark_mode", self.dark_mode)
                if hasattr(w, "apply_theme"):
                    w.apply_theme()  # type: ignore
            if isinstance(w, ReaderTab):
                w.rendered_pages.clear()
                w.update_view()

        for tab_widget in [self.tabs_main, self.tabs_side]:
            for i in range(tab_widget.count()):
                update_widget_theme(tab_widget.widget(i))

    def close_active_tab(self) -> None:
        """Closes the tab currently holding focus."""
        focus_widget = QApplication.focusWidget()
        target = None
        curr = focus_widget

        # Traverse up to find the parent TabWidget
        while curr:
            if curr == self.tabs_main:
                target = self.tabs_main
                break
            elif curr == self.tabs_side:
                target = self.tabs_side
                break
            curr = curr.parent()

        # Default to main tabs if no specific parent found
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
        """Displays a dialog containing the unified web and PDF history."""
        dialog = QDialog(self)
        dialog.setWindowTitle("History")
        dialog.resize(600, 400)

        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()

        def create_list(data: List[str]) -> QListWidget:
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
            current_list = list_web if tabs.currentIndex() == 0 else list_pdf
            item = current_list.currentItem()
            if not item:
                return

            data = item.text()
            dialog.accept()

            if tabs.currentIndex() == 1:  # PDF Tab
                if os.path.exists(data):
                    current = self.tabs_main.currentWidget()
                    if isinstance(current, ReaderTab) and not current.current_path:
                        current.load_document(data)
                        self.tabs_main.setTabText(
                            self.tabs_main.currentIndex(), os.path.basename(data)
                        )
                else:
                    self._add_pdf_tab(data, self.tabs_main)
            else:  # Web Tab
                self.new_browser_tab(data)

        # Reconnect accepted to our custom handler
        button_box.accepted.disconnect()
        button_box.accepted.connect(open_item)

        list_web.itemDoubleClicked.connect(open_item)
        list_pdf.itemDoubleClicked.connect(open_item)

        dialog.exec()

    def show_bookmarks(self) -> None:
        """Displays a dialog containing saved bookmarks."""
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
            item = list_widget.currentItem()
            if not item:
                return
            # Rudimentary parsing: Title (URL)
            url = item.text().split(" (")[-1][:-1]
            dialog.accept()
            self.new_browser_tab(url)

        button_box.accepted.connect(open_bm)
        list_widget.itemDoubleClicked.connect(open_bm)
        dialog.exec()

    def show_downloads(self) -> None:
        """Shows the non-modal download manager dialog."""
        self.download_manager_dialog.show()
        self.download_manager_dialog.raise_()

    def toggle_auto_pdf(self, checked: bool) -> None:
        """Updates the auto-open PDF setting."""
        self.settings.setValue("browser/auto_open_pdf", checked)

    def open_pdf_in_new_tab(self, path: str) -> None:
        """
        Opens a PDF in a new Reader Tab.
        Preferentially opens in the Side Split if active, otherwise Main.

        Args:
            path: The file path to the PDF.
        """
        target = self.tabs_side if (self.tabs_side.isVisible()) else self.tabs_main

        if target == self.tabs_main and self.tabs_side.isHidden():
            self.toggle_split_view()
            target = self.tabs_side

        self._add_pdf_tab(path, target)
        target.setCurrentIndex(target.count() - 1)


def run() -> None:
    """
    Application Entry Point.

    Sets required Chromium flags for the QtWebEngine, initializes the QApplication,
    and starts the main event loop.
    """
    # Security flags required for local file access and autoplay in the browser component
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--disable-web-security "
        "--autoplay-policy=no-user-gesture-required "
        "--no-sandbox "
        "--disable-setuid-sandbox "
        "--disable-features=AudioServiceOutOfProcess"
        "--enable-features=WebEngineProprietaryCodecs"
    )
    sys.argv.append("--no-sandbox")
    sys.argv.append("--disable-web-security")
    sys.argv.append("--autoplay-policy=no-user-gesture-required")

    install_linux_integration()
    app = QApplication(sys.argv)
    app.setApplicationName("Riemann")

    icon_path = get_resource_path(os.path.join("assets", "icon.ico"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = RiemannWindow()
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
        internal_icon_path = os.path.join(base_path, "assets", "Icon.png")

        if not os.path.exists(internal_icon_path) and hasattr(sys, "_MEIPASS"):
            internal_icon_path = os.path.join(
                sys._MEIPASS,  # pyright: ignore[reportAttributeAccessIssue]
                "riemann",
                "assets",
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
