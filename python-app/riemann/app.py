"""
Main Application Module.

This module defines the primary window manager (`RiemannWindow`) and the
application entry point. It orchestrates the UI layout, tab management
(split-view), global keyboard shortcuts, and session persistence.

Notes:

__init__: Opening, closing or switching tabs also saves soon after, so a crash
right after opening a document can still bring it back.

CHROMIUM_FLAGS: Chromium keeps both its sandboxes, and renderers are not
capped: one renderer per site (--process-per-site, which saves memory over
one per tab), so a crash or a compromised renderer stays within that site's
tabs. --disable-setuid-sandbox and --renderer-process-limit=2 were dropped on
16 November 2026; with the cap, every site shared one of two renderers.

run: CI starts the packaged build with this set to check that it gets as far as
an open window, then quits cleanly.
"""

import os
import sys

from .core.assets import bundle_dir
from .core.dependencies import mount_existing_venv

mount_existing_venv()

if bundle_dir():
    os.environ.setdefault("RIEMANN_PDFIUM_DIR", bundle_dir())

import importlib

import shiboken6
import time
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    QEvent,
    QObject,
    QStandardPaths,
    QStringListModel,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QCloseEvent,
    QIcon,
    QPixmapCache,
)
from PySide6.QtNetwork import QLocalServer

try:
    importlib.import_module("PySide6.QtDBus")
except ImportError:
    pass
from PySide6.QtWebEngineCore import (
    QWebEngineProfile,
)
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QMainWindow,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTreeWidget,
)

from .core.managers import (
    BookmarksManager,
    DownloadManager,
    HistoryManager,
    LibraryManager,
    YtDlpDownloadManager,
)
from .core.window_snap import (
    WindowSnapper,
    prefer_xwayland_for_snapping,
    snapping_default,
)
from .ui.components import (
    DraggableTabWidget,
    SplitPaneBadge,
)
from .ui.explorer import FileExplorerPanel
from .ui.palette import UI_FONT
from .ui.reader.render_worker import stop_all_render_workers
from .ui.reader.tab import ReaderTab
from .ui.threads import wait_for_released
from .ui.sidecar import AiSidecarPanel
from .ui.tooltip import TooltipSkin
from .core.log import log
from .core.log import setup as setup_logging
from .window.chrome import ChromeMixin
from .core.assets import asset_path, icon_path
from .window.desktop import install_linux_integration
from .window.dialogs import LibrarySearchDialog
from .window.documents import DocumentsMixin
from .window.folders import FoldersMixin
from .window.instance import (
    decode_ipc_message,
    encode_ipc_paths,
    hand_off_to_running_instance,
    handed_over_paths,
    listen_for_instances,
    single_instance_name,
)
from .window.menus import MenusMixin
from .window.panes import PanesMixin
from .window.session import SessionMixin
from .window.system import SystemMixin
from .window.tabs import TabsMixin
from .core.settings import (
    CLEAN_EXIT_KEY,
    DARK_MODE_KEY,
    DEFAULT_UI_SCALE,
    TABS_COLLAPSED_KEY,
    UI_SCALE_KEY,
    VERTICAL_TABS_KEY,
    app_settings,
)


__all__ = [
    "LibrarySearchDialog",
    "RiemannWindow",
    "decode_ipc_message",
    "encode_ipc_paths",
    "asset_path",
    "hand_off_to_running_instance",
    "listen_for_instances",
    "run",
    "single_instance_name",
]

class RiemannWindow(
    DocumentsMixin,
    SessionMixin,
    SystemMixin,
    MenusMixin,
    PanesMixin,
    TabsMixin,
    ChromeMixin,
    FoldersMixin,
    QMainWindow,
):
    """
    The Main Window Manager for the Riemann application.

    Handles global state, split-view tab management, history tracking,
    shortcuts, and session persistence.
    """

    _all_open_windows: List["RiemannWindow"] = []
    _recently_closed_windows: List[Tuple[float, dict]] = []

    def __init__(
        self,
        incognito: bool = False,
        restore_session: bool = True,
        external_files: Optional[List[str]] = None,
        is_secondary_restoration: bool = False,
    ) -> None:
        """
        With incognito, no history is recorded; restore_session reopens the last
        session's tabs.
        """
        super().__init__()
        self._start_time = time.time()
        self.incognito = incognito
        self.restore_session = restore_session
        self.external_files = external_files or []
        self.is_secondary_restoration = is_secondary_restoration

        if not self.incognito:
            RiemannWindow._all_open_windows.append(self)

        if self.incognito:
            self.setWindowTitle("Riemann (Incognito)")
            self.setProperty("incognito", True)
            self.web_profile = QWebEngineProfile()
        else:
            self.setWindowTitle("Riemann")
            self.web_profile = persistent_profile()

        self.resize(1200, 900)
        self.settings = app_settings()
        self.dark_mode: bool = self.settings.value(DARK_MODE_KEY, False, type=bool)
        self._was_unclean_exit: bool = not self.settings.value(
            CLEAN_EXIT_KEY, True, type=bool
        )
        self.settings.setValue(CLEAN_EXIT_KEY, False)
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
        self.explorer_panel.collapse_requested.connect(self.toggle_folder_view)
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
        self.tabs_main.tabBarDoubleClicked.connect(
            lambda idx: self._on_tab_bar_double_clicked(self.tabs_main, idx)
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
        self.tabs_side.tabBarDoubleClicked.connect(
            lambda idx: self._on_tab_bar_double_clicked(self.tabs_side, idx)
        )
        self.tabs_side.hide()
        self.splitter.addWidget(self.tabs_side)

        self.sidecar_panel = AiSidecarPanel(self, self.dark_mode)
        self.sidecar_panel.hide()
        self.sidecar_panel.page_jump_requested.connect(self._on_sidecar_page_jump)
        self.sidecar_panel.close_requested.connect(self.toggle_ai_sidecar)
        self.main_splitter.addWidget(self.sidecar_panel)
        self.main_splitter.setStretchFactor(1, 1)

        self.tabs_main.setObjectName("PaneMain")
        self.tabs_side.setObjectName("PaneSide")
        self._active_pane = self.tabs_main
        self.pane_badges = {
            self.tabs_main: SplitPaneBadge(self.tabs_main),
            self.tabs_side: SplitPaneBadge(self.tabs_side),
        }
        for pane, badge in self.pane_badges.items():
            badge.activate_requested.connect(lambda p=pane: self._focus_pane(p))
            badge.close_requested.connect(lambda p=pane: self._close_pane(p))
            pane.currentChanged.connect(lambda _=0: self._refresh_split_style())
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self.tabs_main.currentChanged.connect(self._sync_assistant_document)
        self.tabs_side.currentChanged.connect(self._sync_assistant_document)

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
        self._apply_vertical_tabs(
            self.settings.value(VERTICAL_TABS_KEY, False, type=bool)
        )
        self._tabs_collapsed = self.settings.value(
            TABS_COLLAPSED_KEY, False, type=bool
        )
        self._restore_tab_bars()

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

        self._session_save_soon = QTimer(self)
        self._session_save_soon.setSingleShot(True)
        self._session_save_soon.setInterval(2500)
        self._session_save_soon.timeout.connect(self._auto_save_session)
        for tab_widget in (self.tabs_main, self.tabs_side):
            tab_widget.currentChanged.connect(lambda _: self._session_save_soon.start())
            tab_widget.tabBar().tabMoved.connect(
                lambda *_: self._session_save_soon.start()
            )

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

        self.window_snapper = WindowSnapper(
            self,
            enabled=self.settings.value(
                "app/window_snapping", snapping_default(), type=bool
            ),
        )
        if TooltipSkin.instance is not None:
            TooltipSkin.instance.attach(self)


    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """Global keyboard shortcuts, and revealing hidden bars when the mouse reaches them."""
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            if (
                modifiers & Qt.KeyboardModifier.ControlModifier
                and key == Qt.Key.Key_Tab
            ):
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


    def changeEvent(self, event: QEvent) -> None:
        """On activation, gives keyboard focus back to the current tab."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            target = (
                self.tabs_side
                if (self.tabs_side.isVisible() and self.tabs_side.hasFocus())
                else self.tabs_main
            )
            if target.currentWidget():
                target.currentWidget().setFocus()


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




    def exit_application(self) -> None:
        """Saves all open windows and terminates the application."""
        RiemannWindow._save_all_windows_session()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Saves the session unless the window is incognito."""
        if self.incognito:
            if self in RiemannWindow._all_open_windows:
                RiemannWindow._all_open_windows.remove(self)
            self._kill_all_media_safely()
            super().closeEvent(event)
            return

        other_open = [
            w
            for w in RiemannWindow._all_open_windows
            if w != self and not getattr(w, "incognito", False)
        ]
        win_state = self._serialize_window_state()
        if other_open:
            if (
                win_state.get("main_tabs")
                or win_state.get("side_tabs")
                or win_state.get("active_folder")
            ):
                RiemannWindow._recently_closed_windows.append((time.time(), win_state))

        if self in RiemannWindow._all_open_windows:
            RiemannWindow._all_open_windows.remove(self)

        RiemannWindow._save_all_windows_session(
            clean_exit=True,
            closing_window=self if not other_open else None,
        )

        self._kill_all_media_safely()
        super().closeEvent(event)


PERSISTENT_PROFILE_NAME = "RiemannPersistentProfile"
_persistent = None


def persistent_profile() -> QWebEngineProfile:
    """
    The one persistent web profile, shared by every non-incognito window. Two
    profiles with the same name would open the same cookie and cache files
    twice, which Qt does not support. It belongs to the application, so it
    outlives every window and the pages in them.
    """
    global _persistent
    if isinstance(_persistent, QObject) and shiboken6.isValid(_persistent):
        return _persistent
    profile = _persistent = QWebEngineProfile(PERSISTENT_PROFILE_NAME, QApplication.instance())
    storage_path = os.path.join(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation), "web_profile"
    )
    os.makedirs(storage_path, exist_ok=True)
    profile.setPersistentStoragePath(storage_path)
    profile.setCachePath(storage_path)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    return profile


class ButtonIconFreeStyle(QProxyStyle):
    """The platform style without the stock icons (a red X, a green tick) that some
    desktop themes put on OK/Cancel/Yes/No buttons; they clash with the app theme."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        return super().styleHint(hint, option, widget, returnData)


CHROMIUM_FLAGS = (
    "--autoplay-policy=no-user-gesture-required "
    "--disable-features=AudioServiceOutOfProcess "
    "--referrer-policy=no-referrer-when-downgrade "
    "--enable-features=WebEngineProprietaryCodecs "
    "--process-per-site "
    "--disk-cache-size=52428800 "
)


def run() -> None:
    """Application Entry Point.

    Sets required Chromium flags for the QtWebEngine,
    initializes the QApplication,
    enforces a single-instance pattern via QLocalServer,
    and starts the main event loop.
    """

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = CHROMIUM_FLAGS
    sys.argv.append("--autoplay-policy=no-user-gesture-required")

    install_linux_integration()

    init_settings = app_settings()
    ui_scale = init_settings.value(UI_SCALE_KEY, DEFAULT_UI_SCALE, type=int)
    if ui_scale and ui_scale != 100:
        os.environ["QT_SCALE_FACTOR"] = f"{ui_scale / 100.0:.2f}"

    if init_settings.value("app/window_snapping", snapping_default(), type=bool):
        prefer_xwayland_for_snapping()

    app = QApplication(sys.argv)
    app.setStyle(ButtonIconFreeStyle())
    TooltipSkin.install(app)
    app_font = app.font()
    app_font.setFamilies([UI_FONT, "Segoe UI", "Noto Sans", "sans-serif"])
    app.setFont(app_font)
    app.setApplicationName("Riemann")
    app.setDesktopFileName("Riemann")
    setup_logging()

    QPixmapCache.setCacheLimit(153600)

    args = app.arguments()
    is_new_window = "--new-window" in args
    files_to_open = []
    folders_to_open = []

    for arg in args[1:]:
        if not arg.startswith("-"):
            clean_arg = QUrl(arg).toLocalFile() if arg.startswith("file://") else arg
            if os.path.isfile(clean_arg):
                files_to_open.append(os.path.abspath(clean_arg))
            elif os.path.isdir(clean_arg):
                folders_to_open.append(os.path.abspath(clean_arg))

    server_name = single_instance_name()

    if not is_new_window:
        if hand_off_to_running_instance(server_name, encode_ipc_paths(files_to_open + folders_to_open)):
            sys.exit(0)

    server = QLocalServer()
    if not listen_for_instances(server, server_name):
        log.warning("Single-instance socket unavailable: %s", server.errorString())

    app_icon = icon_path("Icon.png")
    if os.path.exists(app_icon):
        app.setWindowIcon(QIcon(app_icon))

    window = RiemannWindow(external_files=files_to_open + folders_to_open)

    def handle_connection():
        """
        Handles an IPC connection from a secondary instance. The message is
        collected until the sender disconnects, since a long list of paths
        can arrive in several reads.
        """
        client = server.nextPendingConnection()
        if client is None:
            return
        buffer = bytearray()

        def read_data():
            buffer.extend(client.readAll().data())

        def finish():
            """Opens the paths the secondary instance sent, then raises the window."""
            buffer.extend(client.readAll().data())
            client.deleteLater()
            if not buffer:
                return
            files, folders = handed_over_paths(bytes(buffer))
            for path in files:
                window.open_path(path)
            for folder in folders:
                window.open_folder(folder)
            window.activateWindow()
            window.raise_()

        client.readyRead.connect(read_data)
        client.disconnected.connect(finish)

    server.newConnection.connect(handle_connection)

    for path in files_to_open:
        window.open_path(path)
    for folder in folders_to_open:
        window.open_folder(folder)
    if folders_to_open and window.tabs_main.count() == 0:
        window.new_pdf_tab()

    app.aboutToQuit.connect(RiemannWindow._save_all_windows_session)
    app.aboutToQuit.connect(stop_all_render_workers)
    app.aboutToQuit.connect(wait_for_released)
    window.show()
    smoke_ms = os.environ.get("RIEMANN_SMOKE_TEST")

    if smoke_ms:
        print("[Riemann] Smoke test: window shown", flush=True)
        QTimer.singleShot(int(smoke_ms) if smoke_ms.isdigit() else 5000, app.quit)
    sys.exit(app.exec())


