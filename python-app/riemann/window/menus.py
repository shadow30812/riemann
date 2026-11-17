"""
The menu bar, keyboard shortcuts, favourites menus and the windows opened from menus.

Part of RiemannWindow (riemann/app.py), split out by concern; the methods use
the window's attributes freely, as they did when they lived in one class.

Notes:

_init_shortcuts: Applied once, before the session's tabs exist: tabs added
later are polished as they appear, and setting it again would restyle every
widget.
"""

import os
import time
from typing import Optional
import psutil
from PySide6.QtCore import (
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)
from ..core.features import (
    trigger_local_caption_generation,
)
from ..core.doctypes import dialog_filter
from ..core.mini_player import MiniAudioPlayer
from ..ui.browser import BrowserTab, PreviewTextTab
from ..ui.viewers import VIEWER_TABS
from ..ui.components import (
    populate_open_with_menu,
)
from ..ui.favourites import ManageFavoritesDialog
from ..ui.reader.tab import ReaderTab
from ..ui.command_palette import CommandPalette
from ..ui.palette import tokens
from ..core.settings import FAVOURITE_FOLDERS_KEY, favourites_settings


class MenusMixin:
    def _init_shortcuts(self) -> None:
        """
        Initializes and binds global application keyboard shortcuts.
        """
        shortcuts = [
            ("Ctrl+Q", self.exit_application),
            ("Ctrl+W", self.close_active_tab),
            ("Ctrl+Shift+T", self.restore_last_closed_tab),
            ("Ctrl+\\", self.toggle_split_view),
            ("Ctrl+Alt+A", self.toggle_ai_sidecar),
            ("Ctrl+Shift+B", self.toggle_tabs_collapsed),
            ("Ctrl+Shift+F", self.toggle_folder_view),
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
            ("Ctrl+Shift+P", self.show_command_palette),
            ("F1", self.show_help),
        ]

        for seq, slot in shortcuts:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

        self.enforce_global_stylesheet()

    def show_command_palette(self) -> None:
        """Opens the command palette over the window (Ctrl+Shift+P)."""
        palette = getattr(self, "command_palette", None)
        if palette is None:
            palette = self.command_palette = CommandPalette(self)
        palette.open()

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
        action_manage_fav = self.fav_menu.addAction("Manage Favorites")
        action_manage_fav.triggered.connect(self.manage_favorites)

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
            ("Exit (Ctrl+Q)", None, self.exit_application),
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
            ("Command Palette (Ctrl+Shift+P)", None, self.show_command_palette),
            ("Bookmarks (Ctrl+K, K)", None, self.show_bookmarks),
            ("Downloads (Ctrl+J)", None, self.show_downloads),
            ("yt-dlp Downloads (Ctrl+Shift+J)", None, self.show_ytdlp_downloads),
            ("Search Library (Ctrl+L)", None, self.show_library_search),
            ("History (Ctrl+H)", None, self.show_history),
            ("Settings (Ctrl+,)", None, lambda: self.show_settings()),
            ("Site Permissions", None, self.show_site_permissions),
            ("Toggle UI Theme (Ctrl+D)", None, self.toggle_ui_theme),
            ("Toggle Split View (Ctrl+\\)", None, self.toggle_split_view),
            ("AI Assistant (Ctrl+Alt+A)", None, self.toggle_ai_sidecar),
            ("Show Signature Panel", None, self.force_show_signatures),
        ]

        for name, shortcut, slot in view_actions:
            action = view_menu.addAction(name)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)

        view_menu.addSeparator()
        self.action_toggle_folder = view_menu.addAction("Folder View (Ctrl+Shift+F)")
        self.action_toggle_folder.setCheckable(True)
        self.action_toggle_folder.triggered.connect(self.toggle_folder_view)
        self.action_vertical_tabs = view_menu.addAction("Vertical Tabs")
        self.action_vertical_tabs.setCheckable(True)
        self.action_vertical_tabs.setChecked(self.tabs_main.is_vertical_tabs())
        self.action_vertical_tabs.toggled.connect(self.set_vertical_tabs)
        self.action_collapse_tabs = view_menu.addAction("Collapse Tabs (Ctrl+Shift+B)")
        self.action_collapse_tabs.setCheckable(True)
        self.action_collapse_tabs.setChecked(self._tabs_collapsed)
        self.action_collapse_tabs.toggled.connect(self.set_tabs_collapsed)

        help_menu = menubar.addMenu("Help")
        help_menu.addAction("Help and Keyboard Shortcuts (F1)").triggered.connect(lambda: self.show_help())
        help_menu.addAction("Keyboard Shortcuts").triggered.connect(lambda: self.show_help("shortcuts"))
        help_menu.addAction("About Riemann").triggered.connect(lambda: self.show_settings("about"))

        if psutil:
            self._last_net_io = psutil.net_io_counters()
            self._last_net_time = time.time()

        self.metrics_label = QLabel(" Uptime: 00:00:00 ")
        self.metrics_label.setTextFormat(Qt.TextFormat.RichText)
        self.metrics_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.metrics_label.setMinimumWidth(580)

        self.menu_corner_widget = QWidget(self)
        self.menu_corner_layout = QHBoxLayout(self.menu_corner_widget)
        self.menu_corner_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_corner_layout.setSpacing(0)

        self.mini_player = MiniAudioPlayer(self)

        self.btn_toggle_folder = QToolButton(self)
        self.btn_toggle_folder.setCheckable(True)
        self.btn_toggle_folder.setAutoRaise(True)
        self.btn_toggle_folder.setToolTip(
            "Show or collapse the folder view; the folder session stays open (Ctrl+Shift+F)"
        )
        self.btn_toggle_folder.setText("Folder")
        self.btn_toggle_folder.clicked.connect(self.toggle_folder_view)
        self.btn_toggle_folder.setVisible(False)

        self.btn_ai_sidecar = QToolButton(self)
        self.btn_ai_sidecar.setCheckable(True)
        self.btn_ai_sidecar.setAutoRaise(True)
        self.btn_ai_sidecar.setText("AI Assistant")
        self.btn_ai_sidecar.setToolTip(
            "AI Assistant: ask Claude, GPT, Gemini, OpenRouter or local Ollama\n"
            "models about the open document (Ctrl+Alt+A)"
        )
        self.btn_ai_sidecar.clicked.connect(self.toggle_ai_sidecar)

        self.btn_split_view = QToolButton(self)
        self.btn_split_view.setCheckable(True)
        self.btn_split_view.setAutoRaise(True)
        self.btn_split_view.setToolTip(
            "Split view: show two tabs side by side (Ctrl+\\)\n"
            "Tip: right-click a tab and choose 'Move to Other Pane', "
            "or drag a tab onto the other pane."
        )
        self.btn_split_view.clicked.connect(self.toggle_split_view)

        self.btn_vertical_tabs = QToolButton(self)
        self.btn_vertical_tabs.setCheckable(True)
        self.btn_vertical_tabs.setAutoRaise(True)
        self.btn_vertical_tabs.setToolTip("Vertical tabs")
        self.btn_vertical_tabs.setChecked(self.tabs_main.is_vertical_tabs())
        self.btn_vertical_tabs.toggled.connect(self.set_vertical_tabs)

        self.btn_collapse_tabs = QToolButton(self)
        self.btn_collapse_tabs.setCheckable(True)
        self.btn_collapse_tabs.setAutoRaise(True)
        self.btn_collapse_tabs.setToolTip(
            "Collapse the tab row (or column, with vertical tabs) to give the "
            "page more room (Ctrl+Shift+B)"
        )
        self.btn_collapse_tabs.setChecked(self._tabs_collapsed)
        self.btn_collapse_tabs.toggled.connect(self.set_tabs_collapsed)

        self.btn_split_view.setText("Split View")
        self.btn_vertical_tabs.setText("Vertical Tabs")
        self.btn_collapse_tabs.setText("Collapse Tabs")
        for btn in (
            self.btn_toggle_folder,
            self.btn_ai_sidecar,
            self.btn_split_view,
            self.btn_vertical_tabs,
            self.btn_collapse_tabs,
        ):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setIconSize(QSize(15, 15))
            self.menu_corner_layout.addWidget(btn)
        self.menu_corner_layout.addSpacing(8)
        self._update_layout_button_icons()

        self.menu_corner_layout.addWidget(self.mini_player)
        self.menu_corner_layout.addWidget(self.metrics_label)
        self._style_menu_corner()

        self.menuBar().setCornerWidget(
            self.menu_corner_widget, Qt.Corner.TopRightCorner
        )
        self.metrics_timer = QTimer(self)
        self.metrics_timer.timeout.connect(self._update_global_metrics)
        self.metrics_timer.start(2000)

    def _style_menu_corner(self) -> None:
        t = tokens(self.dark_mode)
        self.metrics_label.setStyleSheet(
            f"color: {t['muted']}; font-size: 11px; padding-right: 15px; font-weight: bold;"
        )
        style = (
            "QToolButton { font-size: 11px; padding: 1px 6px; border-radius: 4px; }"
            f"QToolButton:checked {{ background: {t['accent_soft']}; }}"
        )
        for btn in (
            self.btn_toggle_folder,
            self.btn_ai_sidecar,
            self.btn_split_view,
            self.btn_vertical_tabs,
            self.btn_collapse_tabs,
        ):
            btn.setStyleSheet(style)

    def _populate_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent_pdfs = self.history_manager.get_list("pdf")[:10]
        if not recent_pdfs:
            action = self.recent_menu.addAction("No recent files")
            action.setEnabled(False)
            return

        for pdf_path in recent_pdfs:
            if os.path.exists(pdf_path):
                action = self.recent_menu.addAction(os.path.basename(pdf_path))
                action.triggered.connect(
                    lambda checked=False, p=pdf_path: self.new_pdf_tab(p)
                )

    @staticmethod
    def _external_target(widget: Optional[QWidget]) -> str:
        """The file, folder or web address a tab shows, for opening it in another app."""
        if widget is None:
            return ""
        path = getattr(widget, "current_path", None)
        if path:
            return path
        if isinstance(widget, BrowserTab):
            url = widget.web.url()
            if url.scheme() in ("http", "https", "ftp", "file"):
                return url.toString()
        return ""

    def _populate_external_app_menu(self) -> None:
        """Fills File > Open in External Application for the active tab and folder."""
        menu = self.external_app_menu
        menu.clear()
        target = self._external_target(self._active_pane_widget())
        folder = (
            self.explorer_panel.current_path
            if getattr(self, "is_folder_session_active", False)
            else ""
        )
        if target:
            populate_open_with_menu(menu, target, self)
        if folder:
            if target:
                menu.addSeparator()
            sub = menu.addMenu(f"Folder: {os.path.basename(folder) or folder}")
            populate_open_with_menu(sub, folder, self)
        if not target and not folder:
            menu.addAction("Nothing to open in this tab").setEnabled(False)

    def _switch_open_documents_mode(self, to_preview: bool) -> None:
        """
        Reopens every open document (PDF, Markdown, HTML preview) in preview
        or full mode, leaving browser pages and the explorer preview alone.
        """
        for tab_widget in [self.tabs_main, self.tabs_side]:
            to_convert = []
            for i in range(tab_widget.count()):
                widget = tab_widget.widget(i)
                if widget is getattr(self, "preview_tab_widget", None):
                    continue
                is_doc = isinstance(widget, (ReaderTab, PreviewTextTab) + VIEWER_TABS)
                if (
                    is_doc
                    and getattr(widget, "current_path", None)
                    and bool(getattr(widget, "is_preview", False)) != to_preview
                ):
                    to_convert.append(widget)

            for widget in to_convert:
                idx = tab_widget.indexOf(widget)
                if idx != -1:
                    self._replace_tab_document(tab_widget, idx, preview=to_preview)

    def _populate_fav_file_menu(self):
        self.fav_file_menu.clear()
        favs = favourites_settings().value(FAVOURITE_FOLDERS_KEY, [], type=list)
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
        favs = favourites_settings().value(FAVOURITE_FOLDERS_KEY, [], type=list)
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
            dialog_filter(),
        )
        if paths:
            for p in paths:
                self._open_single_file(p)
