import os
import sys

os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
    os.environ["PDFIUM_DYNAMIC_LIB_PATH"] = bundle_dir

from typing import Any, List, Optional

from PySide6.QtCore import QSettings, QStringListModel, Qt
from PySide6.QtGui import QKeySequence, QShortcut
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


class SettingsDialog(QDialog):
    def __init__(self, parent: "RiemannWindow") -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(400, 200)
        form_layout = QFormLayout(self)

        self.lay = form_layout
        self.cb_dark = QCheckBox()
        self.cb_dark.setChecked(parent.dark_mode)
        form_layout.addRow("Dark Mode:", self.cb_dark)

        self.cb_auto_pdf = QCheckBox()
        self.cb_auto_pdf.setChecked(
            parent.settings.value("browser/auto_open_pdf", False, type=bool)
        )
        form_layout.addRow("Auto-open Downloaded PDFs:", self.cb_auto_pdf)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        form_layout.addRow(self.button_box)


class RiemannWindow(QMainWindow):
    """
    The Main Window Manager.
    Handles global state, split-view tabs, history, and shortcuts.
    """

    def __init__(self, incognito: bool = False, restore_session: bool = True) -> None:
        """Initializes the main application window."""
        super().__init__()
        self.incognito = incognito
        self.restore_session = restore_session

        if self.incognito:
            self.setWindowTitle("Riemann Reader (Incognito)")
            self.setProperty("incognito", True)
        else:
            self.setWindowTitle("Riemann Reader")

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

        self.shortcut_exit = QShortcut(QKeySequence("Ctrl+Q"), self)
        self.shortcut_exit.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_exit.activated.connect(self.close)

        self.shortcut_close = QShortcut(QKeySequence("Ctrl+W"), self)
        self.shortcut_close.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_close.activated.connect(self.close_active_tab)

        self.shortcut_restore = QShortcut(QKeySequence("Ctrl+Shift+T"), self)
        self.shortcut_restore.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_restore.activated.connect(self.restore_last_closed_tab)

        self.shortcut_split = QShortcut(QKeySequence("Ctrl+\\"), self)
        self.shortcut_split.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_split.activated.connect(self.toggle_split_view)

        self.shortcut_fullscreen = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.shortcut_fullscreen.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_fullscreen.activated.connect(self.toggle_reader_fullscreen)

        self.shortcut_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.shortcut_escape.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_escape.activated.connect(self._handle_escape)

        self._restore_session()

    def add_to_history(self, item: str, item_type: str = "web") -> None:
        """
        Adds item to history and updates the shared autocomplete model.

        Args:
            item: The URL or file path to add.
        """
        if self.incognito:
            return

        self.history_manager.add(item, item_type)
        self.history_model.setStringList(self.history_manager.get_model_data())

    def _handle_escape(self) -> None:
        """Handles the Escape key to exit fullscreen."""
        if getattr(self, "_reader_fullscreen", False):
            self.toggle_reader_fullscreen()

    def _restore_session(self) -> None:
        """Restores the window geometry and open tabs from the previous session."""
        if self.incognito or not self.restore_session:
            self.new_browser_tab()
            self.resize(1200, 900)
            return

        if self.settings.value("window/geometry"):
            self.restoreGeometry(self.settings.value("window/geometry"))

        self._restore_tabs_from_settings("session/main_tabs", self.tabs_main)
        self._restore_tabs_from_settings("session/side_tabs", self.tabs_side)

        if self.tabs_side.count() > 0:
            self.tabs_side.show()
            if self.settings.value("splitter/state"):
                self.splitter.restoreState(self.settings.value("splitter/state"))
        else:
            self.tabs_side.hide()

        if self.tabs_main.count() == 0:
            self.new_pdf_tab()

    def _restore_tabs_from_settings(self, key: str, target_widget: QTabWidget) -> None:
        """
        Helper to restore tabs from QSettings.

        Args:
            key: The settings key to read from.
            target_widget: The QTabWidget to populate.
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

    def _add_pdf_tab(
        self, path: str, target_widget: QTabWidget, restore_state: bool = False
    ) -> None:
        """Adds a PDF Reader tab."""
        self.add_to_history(path, "pdf")
        reader = ReaderTab()
        reader.load_document(path, restore_state=restore_state)
        idx = target_widget.addTab(reader, os.path.basename(path))
        target_widget.setCurrentIndex(idx)

    def _add_browser_tab(self, url: str, target_widget: QTabWidget) -> None:
        """Adds a Web Browser tab."""
        browser = BrowserTab(url, dark_mode=self.dark_mode)
        browser.completer.setModel(self.history_model)
        target_widget.addTab(browser, "Loading...")

    def setup_menu(self) -> None:
        """Configures the main window menu bar."""
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        open_action = file_menu.addAction("Open PDF")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_pdf_smart)

        new_tab_action = file_menu.addAction("Open New PDF Tab")
        new_tab_action.setShortcut("Ctrl+T")
        new_tab_action.triggered.connect(self.open_pdf_smart)

        file_menu.addSeparator()

        browser_action = file_menu.addAction("New Browser Tab")
        browser_action.setShortcut("Ctrl+B")
        browser_action.triggered.connect(lambda: self.new_browser_tab())

        new_win_action = file_menu.addAction("New Window")
        new_win_action.setShortcut("Ctrl+N")
        new_win_action.triggered.connect(self.new_window)

        incog_action = file_menu.addAction("New Incognito Tab")
        incog_action.setShortcut("Ctrl+Shift+N")
        incog_action.triggered.connect(self.new_incognito_window)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("Exit (Ctrl+Q)")
        exit_action.triggered.connect(self.close)

        view_menu = menubar.addMenu("View")

        bm_action = view_menu.addAction("Bookmarks")
        bm_action.setShortcut("Ctrl+K")
        bm_action.triggered.connect(self.show_bookmarks)

        dl_action = view_menu.addAction("Downloads")
        dl_action.setShortcut("Ctrl+J")
        dl_action.triggered.connect(self.show_downloads)

        history_action = view_menu.addAction("History")
        history_action.setShortcut("Ctrl+H")
        history_action.triggered.connect(self.show_history)

        settings_action = view_menu.addAction("Settings")
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.show_settings)

        theme_action = view_menu.addAction("Toggle Theme")
        theme_action.setShortcut("Ctrl+D")
        theme_action.triggered.connect(self.toggle_theme)

    def show_settings(self) -> None:
        """Displays the configuration dialog."""
        dlg = SettingsDialog(self)
        if dlg.exec():
            if dlg.cb_dark.isChecked() != self.dark_mode:
                self.toggle_theme()

            self.toggle_auto_pdf(dlg.cb_auto_pdf.isChecked())

    def new_pdf_tab(
        self, path: Optional[str] = None, restore_state: bool = False
    ) -> None:
        """Creates a new PDF tab."""
        if path:
            self._add_pdf_tab(path, self.tabs_main, restore_state)
        else:
            reader = ReaderTab()
            self.tabs_main.addTab(reader, "New Tab")
            self.tabs_main.setCurrentWidget(reader)

    def new_browser_tab(
        self, url: str = "https://www.google.com", incognito: bool = False
    ) -> None:
        """Creates a new Browser tab."""
        is_incognito = self.incognito or incognito

        target = self.tabs_main
        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            target = self.tabs_side

        browser = BrowserTab(url, dark_mode=self.dark_mode, incognito=is_incognito)
        browser.completer.setModel(self.history_model)

        label = "Incognito" if incognito else "Loading..."
        target.addTab(browser, label)
        new_pdf_tab = target.widget(target.count() - 1)
        target.setCurrentWidget(new_pdf_tab)

        new_pdf_tab.txt_url.setFocus()
        new_pdf_tab.txt_url.selectAll()

    def new_window(self) -> None:
        """Spawns a new independent standard window."""
        self._new_window_ref = RiemannWindow(incognito=False, restore_session=False)
        self._new_window_ref.show()

    def new_incognito_window(self) -> None:
        """Spawns a new independent window in Incognito mode."""
        self.incognito_window = RiemannWindow(incognito=True)
        self.incognito_window.show()

    def open_pdf_smart(self) -> None:
        """Opens a Document (PDF/MD) in the current tab if empty, or a new tab otherwise."""
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

        # Process remaining files (always new tabs)
        for path in paths[1:]:
            self.add_to_history(path)
            self.new_pdf_tab(path)

    def toggle_split_view(self) -> None:
        """Toggles the split-screen view mode."""
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
        """Records a closed tab to the stack for restoration."""
        if isinstance(widget, ReaderTab) and widget.current_path:
            self.closed_tabs_stack.append({"type": "pdf", "data": widget.current_path})
        elif isinstance(widget, BrowserTab):
            self.closed_tabs_stack.append(
                {"type": "web", "data": widget.web.url().toString()}
            )

    def restore_last_closed_tab(self) -> None:
        """Restores the most recently closed tab."""
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
        """Closes a tab from the main tab widget."""
        widget = self.tabs_main.widget(index)
        if widget:
            self._record_closed_tab(widget)
            widget.deleteLater()
        self.tabs_main.removeTab(index)

        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()

    def close_side_tab(self, index: int) -> None:
        """Closes a tab from the side tab widget."""
        widget = self.tabs_side.widget(index)
        if widget:
            self._record_closed_tab(widget)
            widget.deleteLater()
        self.tabs_side.removeTab(index)
        if self.tabs_side.count() == 0:
            self.tabs_side.hide()

        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if getattr(self, "_reader_fullscreen", False):
                self.toggle_reader_fullscreen()

    def closeEvent(self, event: Any) -> None:
        """Saves session state before closing."""
        if self.incognito or not self.restore_session:
            super().closeEvent(event)
            return

        def get_files(w):
            l = []
            for i in range(w.count()):
                wid = w.widget(i)
                if isinstance(wid, ReaderTab) and wid.current_path:
                    l.append({"type": "pdf", "data": wid.current_path})
                elif isinstance(wid, BrowserTab):
                    if not wid.incognito:
                        l.append({"type": "web", "data": wid.web.url().toString()})
            return l

        self.settings.setValue("session/main_tabs", get_files(self.tabs_main))
        self.settings.setValue("session/side_tabs", get_files(self.tabs_side))
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        super().closeEvent(event)

    def toggle_reader_fullscreen(self) -> None:
        """Toggles fullscreen mode for the entire window."""
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
        """Helper to hide/show toolbars in tabs."""
        for i in range(self.tabs_main.count()):
            w = self.tabs_main.widget(i)
            if hasattr(w, "toolbar"):
                w.toolbar.setVisible(visible)
        for i in range(self.tabs_side.count()):
            w = self.tabs_side.widget(i)
            if hasattr(w, "toolbar"):
                w.toolbar.setVisible(visible)

    def toggle_theme(self) -> None:
        """Toggles Dark/Light mode globally."""
        self.dark_mode = not self.dark_mode
        self.settings.setValue("darkMode", self.dark_mode)

        def update(w):
            if hasattr(w, "dark_mode"):
                w.dark_mode = self.dark_mode
                w.apply_theme()
            if isinstance(w, ReaderTab):
                w.rendered_pages.clear()
                w.update_view()

        for i in range(self.tabs_main.count()):
            update(self.tabs_main.widget(i))
        for i in range(self.tabs_side.count()):
            update(self.tabs_side.widget(i))

    def close_active_tab(self) -> None:
        """Closes the currently focused tab."""
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
        """Displays a dialog with the unified history list."""
        dialog = QDialog(self)
        dialog.setWindowTitle("History")
        dialog.resize(600, 400)

        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()

        def create_list(data):
            lw = QListWidget()
            lw.addItems(data)
            return lw

        list_web = create_list(self.history_manager.get_list("web"))
        list_pdf = create_list(self.history_manager.get_list("pdf"))

        tabs.addTab(list_web, "Web History")
        tabs.addTab(list_pdf, "PDF History")
        layout.addWidget(tabs)

        button_box = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Close)
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
        """Displays a dialog with bookmarks."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Bookmarks")
        dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()

        for bm in self.bookmarks_manager.bookmarks:
            list_widget.addItem(f"{bm['title']} ({bm['url']})")

        layout.addWidget(list_widget)
        button_box = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Close)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        def open_bm() -> None:
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
        """Shows the download manager dialog."""
        self.download_manager_dialog.show()
        self.download_manager_dialog.raise_()

    def toggle_auto_pdf(self, checked: bool) -> None:
        """Updates the auto-open PDF setting."""
        self.settings.setValue("browser/auto_open_pdf", checked)

    def open_pdf_in_new_tab(self, path: str) -> None:
        """
        Opens a PDF in a new Reader Tab.
        Preferentially opens in the Side Split if active, otherwise Main.
        """
        target = self.tabs_side if (self.tabs_side.isVisible()) else self.tabs_main

        if target == self.tabs_main and self.tabs_side.isHidden():
            self.toggle_split_view()
            target = self.tabs_side

        self._add_pdf_tab(path, target)
        target.setCurrentIndex(target.count() - 1)


def run() -> None:
    """Application entry point."""
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--disable-web-security "
        "--autoplay-policy=no-user-gesture-required "
        "--disable-features=AudioServiceOutOfProcess"
    )
    sys.argv.append("--disable-web-security")
    sys.argv.append("--autoplay-policy=no-user-gesture-required")

    app = QApplication(sys.argv)
    app.setApplicationName("Riemann")
    window = RiemannWindow()
    window.show()
    sys.exit(app.exec())
