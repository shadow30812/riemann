"""
Web Browser Component.

This module implements a full-featured web browser tab based on QWebEngineView.
It includes support for persistent profiles, ad-blocking, dark mode injection,
audio processing injection (Riemann Audio), and download management.
"""

import os
import pwd
import re
import subprocess
import sys
import urllib.parse
from typing import Any, Optional

from PySide6.QtCore import (
    QEvent,
    QObject,
    QSettings,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .browser_handlers import ScriptInjector


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


class YtDlpWorker(QThread):
    """
    Background worker thread for executing yt-dlp media downloads.
    Reports progress and completion status asynchronously to the main thread.
    """

    progress = Signal(int)
    finished = Signal(bool, str)

    def __init__(self, url: str, download_dir: str) -> None:
        """
        Initializes the yt-dlp download worker.

        Args:
            url (str): The target media URL to download.
            download_dir (str): The local directory path to save the downloaded file.
        """
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.process: Optional[subprocess.Popen] = None
        self.is_cancelled = False

    def run(self) -> None:
        """
        Executes the yt-dlp subprocess, parses stdout for progress metrics,
        and emits signals reflecting the operation's state.
        """
        try:
            cmd = [
                "yt-dlp",
                "--newline",
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                "en.*",
                "--embed-subs",
                "--merge-output-format",
                "mp4",
                "-o",
                os.path.join(self.download_dir, "%(title)s.%(ext)s"),
                self.url,
            ]
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if self.process.stdout is None:
                self.finished.emit(False, "Failed to start yt-dlp")
                return

            for line in self.process.stdout:
                if self.is_cancelled:
                    break
                match = re.search(r"\[download\]\s+([\d\.]+)%", line)
                if match:
                    val = float(match.group(1))
                    self.progress.emit(int(val))

            self.process.wait()

            if self.is_cancelled:
                self.finished.emit(False, "Download cancelled.")
            elif self.process.returncode == 0:
                self.finished.emit(True, "Download complete!")
            else:
                self.finished.emit(False, "Download failed.")
        except FileNotFoundError:
            self.finished.emit(False, "Error: yt-dlp is not installed or not in PATH.")
        except Exception as e:
            self.finished.emit(False, str(e))

    def stop(self) -> None:
        """
        Terminates the active yt-dlp subprocess safely.
        """
        self.is_cancelled = True
        if self.process:
            self.process.terminate()


class WebPage(QWebEnginePage):
    """
    Custom QWebEnginePage subclass implementing specific behaviors for
    window creation, JavaScript console logging, and custom scheme handling.
    """

    def __init__(
        self, profile: QWebEngineProfile, parent: Optional[QObject] = None
    ) -> None:
        """
        Initializes the custom web page instance.

        Args:
            profile (QWebEngineProfile): The web engine profile handling session data.
            parent (Optional[QObject]): The parent object managing object lifecycle.
        """
        super().__init__(profile, parent)
        self._popups = []
        self.app_settings = QSettings("Riemann", "PDFReader")

    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEnginePage:
        """
        Handles background tab opening and popups (like Google Login) by
        creating a temporary view that shares the same profile/session.

        Args:
            _type (QWebEnginePage.WebWindowType): The requested type of the new window.

        Returns:
            QWebEnginePage: The newly instantiated web page object to host the content.
        """
        view = self.parent()
        main_win = view.window() if view else None

        if _type == QWebEnginePage.WebWindowType.WebBrowserBackgroundTab:
            if hasattr(main_win, "new_browser_tab"):
                new_tab = main_win.new_browser_tab(url="", background=True)
                return new_tab.web.page()

        elif _type == QWebEnginePage.WebWindowType.WebBrowserTab:
            if hasattr(main_win, "new_browser_tab"):
                new_tab = main_win.new_browser_tab(url="", background=False)
                return new_tab.web.page()

        popup_view = QWebEngineView()
        popup_view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup_view.resize(800, 600)

        self._popups.append(popup_view)
        popup_view.destroyed.connect(lambda: self._cleanup_popup(popup_view))

        page = WebPage(self.profile(), popup_view)
        popup_view.setPage(page)
        popup_view.show()
        return page

    def _cleanup_popup(self, popup: QWebEngineView) -> None:
        """
        Removes a destroyed popup view from the internal tracking list.

        Args:
            popup (QWebEngineView): The popup instance being destroyed.
        """
        if popup in self._popups:
            self._popups.remove(popup)

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        """
        Intercepts JavaScript console messages and routes them to standard output.

        Args:
            level (QWebEnginePage.JavaScriptConsoleMessageLevel): The severity level of the message.
            message (str): The text payload of the log.
            line (int): The line number where the log originated.
            source (str): The source file or script identifier.
        """
        self.level = level
        print(f"[JS] {message} (Line {line} in {source})\n\nlevel- {level}")

    def acceptNavigationRequest(
        self, url: QUrl, _type: QWebEnginePage.NavigationType, isMainFrame: bool
    ) -> bool:
        """
        Intercepts navigation requests to handle custom application URL schemes,
        such as data synchronization from internal pages.

        Args:
            url (QUrl): The target URL of the navigation request.
            _type (QWebEnginePage.NavigationType): The type of navigation.
            isMainFrame (bool): True if the navigation occurs in the main frame.

        Returns:
            bool: True if the navigation should proceed natively, False if intercepted.
        """
        if url.host() == "riemann-save.local":
            query = url.query()
            if query.startswith("data="):
                payload = urllib.parse.unquote(query[5:])
                self.app_settings.setValue("homepage_links", payload)
            return False

        if url.scheme() == "riemann-save":
            payload = urllib.parse.unquote(
                url.toString().replace("riemann-save://", "")
            )
            self.app_settings.setValue("homepage_links", payload)
            return False

        return super().acceptNavigationRequest(url, _type, isMainFrame)


class RequestInterceptor(QWebEngineUrlRequestInterceptor):
    """
    Handles AdBlocking by intercepting network requests to known advertising
    and tracking domains, User Agent spoofing for WhatsApp,
    and surgical Header injection for Monkeytype/Firebase auth.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        """
        Initializes the interceptor with a predefined list of blocked domains.

        Args:
            parent (Optional[QObject]): The parent object for memory management.
        """
        super().__init__(parent)
        self.blocked_domains = [
            "doubleclick.net",
            "googleadservices.com",
            "googlesyndication.com",
            "adservice.google.com",
            "pagead2.googlesyndication.com",
            "tpc.googlesyndication.com",
            "youtube.com/api/stats/ads",
            "youtube.com/ptracking",
            "youtube.com/pagead",
            "google-analytics.com",
            "dmxleo.com",
            "geo.dailymotion.com",
        ]
        self.spoofed_ua = b"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.7559.59 Safari/537.36"

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        """
        Evaluates an outbound network request, blocking it if it matches blacklists,
        or modifying headers for specific compatibility rules.

        Args:
            info (QWebEngineUrlRequestInfo): Mutable information about the URL request.
        """
        if (
            info.resourceType()
            == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeServiceWorker
        ):
            info.block(True)
            return

        url = info.requestUrl().toString().lower()
        if any(domain in url for domain in self.blocked_domains):
            info.block(True)
            return

        if "whatsapp.com" in url:
            info.setHttpHeader(b"User-Agent", self.spoofed_ua)

        should_inject_referer = (
            "monkeytype.com" in url or "googleapis.com" in url
        ) and "accounts.google.com" not in url

        if should_inject_referer:
            r_type = info.resourceType()

            target_types = [
                QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame,
                QWebEngineUrlRequestInfo.ResourceType.ResourceTypeXhr,
                QWebEngineUrlRequestInfo.ResourceType.ResourceTypeSubFrame,
            ]

            if r_type in target_types:
                info.setHttpHeader(b"Referer", b"https://monkeytype.com/")
                info.setHttpHeader(b"Origin", b"https://monkeytype.com")


class BrowserTab(QWidget):
    """
    A comprehensive web browser widget.

    Features:
    - Persistent or Incognito profiles.
    - Custom Ad-Blocking and script injection.
    - Integrated 'Riemann Audio' engine.
    - Smart Dark Mode for web content.
    - Fullscreen video handling.
    """

    def __init__(
        self,
        start_url: str = "https://www.google.com",
        parent: Optional[QWidget] = None,
        profile: Optional[QWebEngineProfile] = None,
        dark_mode: bool = True,
        incognito: bool = False,
    ) -> None:
        """
        Initializes the BrowserTab layout and core components.

        Args:
            start_url (str): The initial URL to load upon creation.
            parent (Optional[QWidget]): The parent widget container.
            profile (Optional[QWebEngineProfile]): Specific profile context, if any.
            dark_mode (bool): Initial theme state flag (True for dark mode).
            incognito (bool): Whether to use an ephemeral, in-memory profile session.
        """
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.incognito = incognito

        if profile:
            self.profile = profile
        else:
            self.profile = QWebEngineProfile(self)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.request_interceptor = RequestInterceptor(self)
        self.profile.setUrlRequestInterceptor(self.request_interceptor)
        self.profile.downloadRequested.connect(self._handle_download)

        self.script_injector = ScriptInjector(self.profile)
        self.script_injector.inject_ad_skipper()
        self.script_injector.inject_backspace_handler()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(40)
        tb_layout = QHBoxLayout(self.toolbar)
        tb_layout.setContentsMargins(5, 0, 5, 0)

        self.btn_back = QPushButton("◀")
        self.btn_back.setFixedWidth(30)
        self.btn_fwd = QPushButton("▶")
        self.btn_fwd.setFixedWidth(30)
        self.btn_reload = QPushButton("↻")
        self.btn_reload.setFixedWidth(30)

        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("Enter URL or Search...")
        self.txt_url.returnPressed.connect(self.navigate_to_url)

        self.completer = QCompleter()
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.txt_url.setCompleter(self.completer)

        if self.incognito:
            self.btn_incognito_icon = QPushButton("🙈")
            self.btn_incognito_icon.setFlat(True)
            self.btn_incognito_icon.setFixedWidth(30)
            self.btn_incognito_icon.setToolTip(
                "Incognito Mode: History will not be saved."
            )
            self.txt_url.setStyleSheet("""
                QLineEdit { 
                    border: 2px solid #6A0DAD; 
                    background-color: #2D2D2D; 
                    color: white; 
                    border-radius: 4px;
                    padding: 4px;
                }
            """)

        self.btn_theme_toggle = QPushButton("🌗")
        self.btn_theme_toggle.setFixedWidth(30)
        self.btn_theme_toggle.setToolTip("Toggle Browser Dark Mode")
        self.btn_theme_toggle.clicked.connect(self.toggle_theme)

        self.btn_bookmark = QPushButton("☆")
        self.btn_bookmark.setObjectName("bookmarkBtn")
        self.btn_bookmark.setFixedWidth(30)
        self.btn_bookmark.setCheckable(True)
        self.btn_bookmark.setToolTip("Bookmark this page")
        self.btn_bookmark.clicked.connect(self.toggle_bookmark)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFixedWidth(30)
        self.btn_mute.setCheckable(True)
        self.btn_mute.setToolTip("Mute/Unmute Tab")
        self.btn_mute.clicked.connect(self.toggle_mute)

        self.btn_music = QPushButton("♫")
        self.btn_music.setFixedWidth(30)
        self.btn_music.setCheckable(True)
        self.btn_music.setToolTip("Toggle Audiophile Music Mode")
        self.btn_music.clicked.connect(self.toggle_music_mode)
        self.btn_music.setStyleSheet("""
            QPushButton:checked {
                background-color: #FF4500;
                color: white;
                border: 1px solid #CC3700;
            }
        """)

        self.btn_download = QPushButton("⬇")
        self.btn_download.setFixedWidth(30)
        self.btn_download.setToolTip("Download Video via yt-dlp")
        self.btn_download.clicked.connect(self.download_video)

        self.btn_print_pdf = QPushButton("🖨")
        self.btn_print_pdf.setFixedWidth(30)
        self.btn_print_pdf.setToolTip("Save Webpage to PDF")
        self.btn_print_pdf.clicked.connect(self.print_to_pdf)

        self.btn_zoom = QPushButton("100%")
        self.btn_zoom.setFixedWidth(65)
        self.btn_zoom.setToolTip("Zoom Controls")

        zoom_menu = QMenu(self.btn_zoom)
        action_zoom_in = QAction("➕ Zoom In", self)
        action_zoom_in.triggered.connect(lambda: self.modify_zoom(0.1))

        action_zoom_out = QAction("➖ Zoom Out", self)
        action_zoom_out.triggered.connect(lambda: self.modify_zoom(-0.1))

        action_zoom_reset = QAction("🔄 Reset (100%)", self)
        action_zoom_reset.triggered.connect(self.reset_zoom)

        zoom_menu.addAction(action_zoom_in)
        zoom_menu.addAction(action_zoom_out)
        zoom_menu.addAction(action_zoom_reset)
        self.btn_zoom.setMenu(zoom_menu)

        tb_layout.addWidget(self.btn_back)
        tb_layout.addWidget(self.btn_fwd)
        tb_layout.addWidget(self.btn_reload)

        if self.incognito:
            tb_layout.addWidget(self.btn_incognito_icon)

        tb_layout.addWidget(self.txt_url)
        tb_layout.addWidget(self.btn_bookmark)
        tb_layout.addWidget(self.btn_mute)
        tb_layout.addWidget(self.btn_music)
        tb_layout.addWidget(self.btn_theme_toggle)
        tb_layout.addWidget(self.btn_download)
        tb_layout.addWidget(self.btn_print_pdf)
        tb_layout.addWidget(self.btn_zoom)

        layout.addWidget(self.toolbar)

        self.search_bar = QWidget()
        self.search_bar.setFixedHeight(40)
        self.search_bar.setVisible(False)
        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(5, 0, 5, 0)

        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("Find in page...")
        self.txt_find.returnPressed.connect(self.find_next)

        self.btn_find_next = QPushButton("▼")
        self.btn_find_next.clicked.connect(self.find_next)
        self.btn_find_prev = QPushButton("▲")
        self.btn_find_prev.clicked.connect(self.find_prev)
        self.btn_close_find = QPushButton("✕")
        self.btn_close_find.clicked.connect(self.toggle_search)

        sb_layout.addWidget(QLabel("Find:"))
        sb_layout.addWidget(self.txt_find)
        sb_layout.addWidget(self.btn_find_next)
        sb_layout.addWidget(self.btn_find_prev)
        sb_layout.addWidget(self.btn_close_find)
        layout.addWidget(self.search_bar)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: transparent;
            }
            QProgressBar::chunk {
                background-color: #FF4500;
                border-radius: 2px;
            }
        """)
        layout.addWidget(self.progress)

        self.lbl_toast = QLabel(self)
        self.lbl_toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_toast.setStyleSheet(
            "background-color: #333; color: white; padding: 10px; border-radius: 5px; font-weight: bold;"
        )
        self.lbl_toast.hide()

        if self.incognito:
            self.txt_url.setStyleSheet("border: 1px solid #50a0ff;")
            self.txt_url.setPlaceholderText("Incognito Mode")

        self.web = QWebEngineView()
        page = WebPage(self.profile, self.web)
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PdfViewerEnabled, False
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        page.featurePermissionRequested.connect(self._on_feature_permission_requested)
        page.fullScreenRequested.connect(self._handle_fullscreen_request)
        self.web.setPage(page)
        layout.addWidget(self.web)

        self.btn_back.clicked.connect(self.web.back)
        self.btn_fwd.clicked.connect(self.web.forward)
        self.btn_reload.clicked.connect(self.web.reload)

        self.web.urlChanged.connect(self._update_url_bar)
        self.web.loadProgress.connect(self.progress.setValue)
        self.web.iconChanged.connect(self._update_tab_icon)

        self.web.loadFinished.connect(lambda: self.progress.setValue(0))
        self.web.loadFinished.connect(self._restore_music_mode)
        self.web.loadFinished.connect(self._on_homepage_load_finished)
        self.web.titleChanged.connect(self._update_tab_title)

        self.shortcut_reload = QShortcut(QKeySequence("F5"), self)
        self.shortcut_reload.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_reload.activated.connect(self.web.reload)

        self.shortcut_reload_ctrl = QShortcut(QKeySequence("Ctrl+R"), self)
        self.shortcut_reload_ctrl.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_reload_ctrl.activated.connect(self.web.reload)

        self.shortcut_hard_reload = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.shortcut_hard_reload.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_hard_reload.activated.connect(self.hard_reload)

        self.shortcut_f6 = QShortcut(QKeySequence("F6"), self)
        self.shortcut_f6.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_f6.activated.connect(self.focus_url_bar)

        self.shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_find.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_find.activated.connect(self.toggle_search)

        self.shortcut_zoom_in = QShortcut(QKeySequence("Ctrl+="), self)
        self.shortcut_zoom_in.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_zoom_in.activated.connect(lambda: self.modify_zoom(0.1))

        self.shortcut_zoom_in_alt = QShortcut(QKeySequence("Ctrl++"), self)
        self.shortcut_zoom_in_alt.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_zoom_in_alt.activated.connect(lambda: self.modify_zoom(0.1))

        self.shortcut_zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        self.shortcut_zoom_out.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_zoom_out.activated.connect(lambda: self.modify_zoom(-0.1))

        self.shortcut_zoom_out_alt = QShortcut(QKeySequence("Ctrl+_"), self)
        self.shortcut_zoom_out_alt.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_zoom_out_alt.activated.connect(lambda: self.modify_zoom(-0.1))

        self.shortcut_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        self.shortcut_zoom_reset.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_zoom_reset.activated.connect(self.reset_zoom)

        self.shortcut_back_alt = QShortcut(QKeySequence("Alt+Left"), self)
        self.shortcut_back_alt.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_back_alt.activated.connect(self.web.back)

        self.shortcut_fwd_alt = QShortcut(QKeySequence("Alt+Right"), self)
        self.shortcut_fwd_alt.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_fwd_alt.activated.connect(self.web.forward)

        self.shortcut_music = QShortcut(QKeySequence("Ctrl+M"), self)
        self.shortcut_music.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_music.activated.connect(self.btn_music.click)

        self.shortcut_devtools_func = QShortcut(QKeySequence("F12"), self)
        self.shortcut_devtools_func.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_devtools_func.activated.connect(self.open_devtools)

        self.shortcut_devtools = QShortcut(QKeySequence("Ctrl+Shift+I"), self)
        self.shortcut_devtools.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_devtools.activated.connect(self.open_devtools)

        self.apply_theme()
        self.web.installEventFilter(self)

        for child in self.web.children():
            child.installEventFilter(self)

        if self.incognito:
            self.txt_url.setStyleSheet("""
                QLineEdit { 
                    border: 2px solid #6A0DAD; 
                    background-color: #2D2D2D; 
                    color: white; 
                    border-radius: 4px;
                    padding: 4px;
                }
            """)

        if self.window() and hasattr(self.window(), "history_model"):
            self.completer.setModel(self.window().history_model)

        if start_url == "https://www.google.com" or not start_url:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            homepage_path = os.path.abspath(
                os.path.join(base_dir, "..", "assets", "homepage.html")
            )

            if os.path.exists(homepage_path):
                self.web.load(QUrl.fromLocalFile(homepage_path))
            else:
                self.web.load(QUrl("https://www.google.com"))
        else:
            self.web.load(QUrl(start_url))

    def focusInEvent(self, event: Any) -> None:
        """
        Handles the event when the Tab widget itself receives focus.
        Immediately forwards focus to the web view to enable page shortcuts.

        Args:
            event (Any): The underlying Qt focus event.
        """
        self.web.setFocus()
        super().focusInEvent(event)

    def _on_homepage_load_finished(self, ok: bool) -> None:
        """
        Injects system data securely into the local homepage once rendering is complete.

        Args:
            ok (bool): True if the page loaded successfully.
        """
        if ok and "homepage.html" in self.web.url().toString():
            try:
                name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
                if not name:
                    name = os.getlogin()
            except Exception:
                name = os.getlogin()

            settings = QSettings("Riemann", "PDFReader")
            links = settings.value("homepage_links", "null")
            js_code = f"window.initHomepage('{name}', {links});"
            self.web.page().runJavaScript(js_code)

    def _on_feature_permission_requested(
        self, url: QUrl, feature: QWebEnginePage.Feature
    ) -> None:
        """
        Auto-grants permissions for Clipboard access so web application 'Copy' buttons function.

        Args:
            url (QUrl): The URL requesting the permission.
            feature (QWebEnginePage.Feature): The specific feature being requested.
        """
        if feature in (
            QWebEnginePage.Feature.ClipboardReadWrite,
            QWebEnginePage.Feature.ClipboardWrite,
        ):
            self.web.page().setFeaturePermission(
                url, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            )
        else:
            self.web.page().setFeaturePermission(
                url, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
            )

    def open_devtools(self) -> None:
        """
        Opens the Web Inspector for the current page in an independent detached window.
        """
        if not hasattr(self, "_devtools_window"):
            self._devtools_window = QWidget()
            self._devtools_window.setWindowTitle("Inspector")
            self._devtools_window.resize(800, 600)

            layout = QVBoxLayout(self._devtools_window)
            layout.setContentsMargins(0, 0, 0, 0)

            self._devtools_view = QWebEngineView()
            layout.addWidget(self._devtools_view)

            self._devtools_view.page().setInspectedPage(self.web.page())

        self._devtools_window.show()
        self._devtools_window.raise_()

    def hard_reload(self) -> None:
        """
        Clears the HTTP cache and forces a full network reload of the current page.
        """
        self.profile.clearHttpCache()
        self.web.reload()

    def focus_url_bar(self) -> None:
        """
        Grabs input focus and selects all text within the navigation address bar.
        """
        self.txt_url.setFocus()
        self.txt_url.selectAll()

    def _handle_fullscreen_request(self, request: QWebEngineDownloadRequest) -> None:
        """
        Handles fullscreen requests from web content by toggling the main application state.

        Args:
            request (QWebEngineDownloadRequest): The fullscreen authorization request object.
        """
        request.accept()
        main_win = self.window()

        if not hasattr(main_win, "toggle_reader_fullscreen"):
            return

        current_app_fs = getattr(main_win, "_reader_fullscreen", False)

        if request.toggleOn():
            self._was_app_fs_before_video = current_app_fs

            if not current_app_fs:
                main_win.toggle_reader_fullscreen()
        else:
            target_state_fs = getattr(self, "_was_app_fs_before_video", False)

            if current_app_fs and not target_state_fs:
                main_win.toggle_reader_fullscreen()
            elif not current_app_fs and target_state_fs:
                main_win.toggle_reader_fullscreen()

    def apply_theme(self) -> None:
        """
        Applies aesthetic color changes to the UI based on the active dark/light mode setting.
        """
        settings = self.web.page().settings()
        if self.dark_mode:
            bg, fg, inp_bg, border = "#333", "#ddd", "#444", "#555"
            settings.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, False)
            self.web.page().setBackgroundColor(QColor("#333"))
        else:
            bg, fg, inp_bg, border = "#f0f0f0", "#222", "#fff", "#ccc"
            settings.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, False)
            self.web.page().setBackgroundColor(QColor("#fff"))

        self.script_injector.inject_smart_dark_mode(self.web.page(), self.dark_mode)
        style = f"QWidget {{ background: {bg}; color: {fg}; }} QLineEdit {{ background: {inp_bg}; border: 1px solid {border}; border-radius: 4px; padding: 4px; }}"
        self.toolbar.setStyleSheet(style)
        self.search_bar.setStyleSheet(style)

        if self.incognito:
            self.txt_url.setStyleSheet("""
                QLineEdit { 
                    border: 2px solid #6A0DAD; 
                    background-color: #2D2D2D; 
                    color: white; 
                    border-radius: 4px;
                    padding: 4px;
                }
            """)

    def toggle_theme(self) -> None:
        """
        Reverses the active display theme state, applying updates to the local rendering pipeline.
        """
        self.dark_mode = not self.dark_mode
        if hasattr(self.window(), "settings"):
            self.window().settings.setValue("darkMode", self.dark_mode)
        if hasattr(self.window(), "dark_mode"):
            self.window().dark_mode = self.dark_mode

        self.apply_theme()

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """
        Filters input events prioritizing crucial native shortcuts before WebEngine consumption.

        Args:
            source (QObject): The event origin object.
            event (QEvent): The triggering Qt framework event payload.

        Returns:
            bool: Indication of whether the event was successfully consumed.
        """
        if source == self.web and event.type() == QEvent.Type.ChildAdded:
            event.child().installEventFilter(self)
            return False

        is_web_source = source == self.web or source in self.web.children()

        if is_web_source:
            if event.type() == QEvent.Type.Wheel:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta != 0:
                        self.modify_zoom(delta / 1200.0)
                    return True
                if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                    delta = event.angleDelta().y()
                    if delta != 0:
                        js = f"window.scrollBy({{top: {-delta * 3}, behavior: 'instant'}});"
                        self.web.page().runJavaScript(js)
                        return True

            elif event.type() == QEvent.Type.NativeGesture:
                if (
                    hasattr(event, "gestureType")
                    and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
                ):
                    self.modify_zoom(event.value())
                    return True

            elif event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_F11:
                    if self.window() and hasattr(
                        self.window(), "toggle_reader_fullscreen"
                    ):
                        self.window().toggle_reader_fullscreen()
                    return True

                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    key = event.key()
                    if key == Qt.Key.Key_T:
                        if self.window() and hasattr(self.window(), "new_pdf_tab"):
                            self.window().new_pdf_tab()
                        return True
                    if key == Qt.Key.Key_M:
                        self.btn_music.click()
                        return True

                    if key == Qt.Key.Key_Tab:
                        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                            if hasattr(self.window(), "prev_tab"):
                                self.window().prev_tab()
                        else:
                            if hasattr(self.window(), "next_tab"):
                                self.window().next_tab()
                        return True

        return super().eventFilter(source, event)

    def modify_zoom(self, delta: float) -> None:
        """
        Adjusts the visual zoom scaling layout property of the rendered web page.

        Args:
            delta (float): Incremental adjustment value to modify the zoom factor.
        """
        new_factor = max(0.1, min(self.web.zoomFactor() + delta, 5.0))
        self.web.setZoomFactor(new_factor)
        self.btn_zoom.setText(f"{int(new_factor * 100)}%")

    def reset_zoom(self) -> None:
        """
        Resets the rendering layout zoom factor uniformly to absolute normal metrics.
        """
        self.web.setZoomFactor(1.0)
        self.btn_zoom.setText("100%")

    def toggle_search(self) -> None:
        """
        Alternates the user-facing visibility properties for the page search utility bar.
        """
        self.search_bar.setVisible(not self.search_bar.isVisible())
        if self.search_bar.isVisible():
            self.txt_find.setFocus()

    def find_next(self) -> None:
        """
        Triggers a progressive forward search utilizing the web engine document parser.
        """
        self.web.findText(self.txt_find.text())

    def find_prev(self) -> None:
        """
        Executes a backwards search matching string constants against rendered documents.
        """
        self.web.findText(self.txt_find.text(), QWebEngineView.FindFlag.FindBackward)

    def navigate_to_url(self) -> None:
        """
        Resolves input text resolving either a target URL schema or a search query string.
        """
        text = self.txt_url.text().strip()
        if not text:
            return
        url = QUrl(
            text
            if text.startswith("http") or ("." in text and " " not in text)
            else f"https://www.google.com/search?q={text}"
        )
        if not url.scheme():
            url.setScheme("https")
        self.web.load(url)

    def resizeEvent(self, event: Any) -> None:
        """
        Recalculates specific UI overlay positions consistently anchoring elements cleanly.

        Args:
            event (Any): Fired geometry update system event.
        """
        super().resizeEvent(event)
        if self.lbl_toast.isVisible():
            self.lbl_toast.move(
                (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
            )

    def show_toast(self, message: str) -> None:
        """
        Draws an informative notification overlay layer dismissing itself systematically.

        Args:
            message (str): Text body to visually render.
        """
        self.lbl_toast.setText(message)
        self.lbl_toast.adjustSize()
        self.lbl_toast.move(
            (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
        )
        self.lbl_toast.show()
        self.lbl_toast.raise_()
        QTimer.singleShot(3000, self.lbl_toast.hide)

    def _update_url_bar(self, url: QUrl) -> None:
        """
        Refreshes navigation string attributes appropriately adjusting historical states concurrently.

        Args:
            url (QUrl): Native object tracking active site addressing correctly.
        """
        s_url = url.toString()

        if "homepage.html" in s_url:
            self.txt_url.setText("")
            self.txt_url.setPlaceholderText("Search the web or enter URL...")
        else:
            self.txt_url.setText(s_url)
            self.txt_url.setPlaceholderText("Enter URL or Search...")

        self.txt_url.setCursorPosition(0)

        if (
            not self.incognito
            and "homepage.html" not in s_url
            and self.window()
            and hasattr(self.window(), "add_to_history")
        ):
            self.window().add_to_history(s_url, "web")

        self._update_bookmark_icon(s_url)

    def _update_bookmark_icon(self, url: str) -> None:
        """
        Polls internal bookmark managers adjusting interactive visual properties representing status realistically.

        Args:
            url (str): String identifier for validation querying properly.
        """
        if self.window() and hasattr(self.window(), "bookmarks_manager"):
            is_bm = self.window().bookmarks_manager.is_bookmarked(url)
            self.btn_bookmark.setChecked(is_bm)
            self.btn_bookmark.setText("★" if is_bm else "☆")

    def toggle_bookmark(self) -> None:
        """
        Commits structural changes generating or discarding favorite states natively managing database objects explicitly.
        """
        if not self.window() or not hasattr(self.window(), "bookmarks_manager"):
            return

        url = self.web.url().toString()
        title = self.web.title()
        bm = self.window().bookmarks_manager

        if bm.is_bookmarked(url):
            bm.remove(url)
            self.show_toast("Bookmark Removed")
        else:
            bm.add(title, url)
            self.show_toast("Bookmark Added")
        self._update_bookmark_icon(url)

    def _update_tab_title(self, title: str) -> None:
        """
        Injects layout naming overrides referencing document names appropriately truncating extensive text correctly.

        Args:
            title (str): Full title string extracted smoothly directly.
        """
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                idx = parent.indexOf(self)
                if idx != -1:
                    parent.setTabText(
                        idx, (title[:20] + "..") if len(title) > 20 else title
                    )
                break
            parent = parent.parent()

    def _update_tab_icon(self, icon) -> None:
        """
        Catches the website's favicon and updates the parent tab's icon.
        """

        parent = self.parent()
        icon_path = get_resource_path(
            os.path.join("..", "assets", "icons", "browser.svg")
        )
        page_icon = QIcon(icon_path)
        while parent:
            if isinstance(parent, QTabWidget):
                idx = parent.indexOf(self)
                if idx != -1 and not icon.isNull():
                    parent.setTabIcon(idx, icon)
                else:
                    parent.setTabIcon(idx, page_icon)
                break

            parent = parent.parent()

    def get_audio_script(self) -> str:
        """
        Extracts foundational Javascript processing payloads natively bundled into application assets reliably.

        Returns:
            str: Resolved Javascript textual components gracefully mapped dynamically.
        """
        try:
            candidate_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "assets",
                "audio_engine.js",
            )

            if not os.path.exists(candidate_path):
                candidate_path = os.path.join(
                    os.path.dirname(sys.executable),
                    "riemann",
                    "assets",
                    "audio_engine.js",
                )

            if not os.path.exists(candidate_path):
                print(f"[Riemann Error] Audio Engine not found at: {candidate_path}")
                self.show_toast("Error: Missing audio_engine.js")
                return ""

            with open(candidate_path, "r", encoding="utf-8") as f:
                return f.read()

        except Exception as e:
            print(f"[ERROR] Failed to load audio script: {e}")
            return ""

    def toggle_music_mode(self) -> None:
        """
        Coordinates client-side audio injection enabling graphical DSP environments dynamically correctly logically.
        """
        is_active = self.btn_music.isChecked()

        base_js = self.get_audio_script()
        if not base_js:
            print("[ERROR] Aborting injection: Script content is empty.")
            self.btn_music.setChecked(False)
            return

        if is_active:
            command = "if(window.RiemannAudio) window.RiemannAudio.enable();"
            self.show_toast("Music Mode ON")
        else:
            command = "if(window.RiemannAudio) window.RiemannAudio.disable();"
            self.show_toast("Music Mode OFF")

        full_script = base_js + "\n" + command
        self.web.page().runJavaScript(full_script)

    def _restore_music_mode(self) -> None:
        """
        Reinitializes musical DSP properties securely restoring previous toggles accurately consistently implicitly.
        """
        if self.btn_music.isChecked():
            QTimer.singleShot(1000, self.toggle_music_mode)

    def _handle_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """
        Orchestrates manual dialog generation collecting paths delegating download resolution securely directly internally.

        Args:
            download_item (QWebEngineDownloadRequest): Engine specific data handler structurally.
        """
        default_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )

        suggested_name = download_item.downloadFileName()

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save File",
            os.path.join(default_dir, suggested_name),
        )

        if not path:
            download_item.cancel()
            return

        download_item.setDownloadDirectory(os.path.dirname(path))
        download_item.setDownloadFileName(os.path.basename(path))
        download_item.accept()

        if self.window() and hasattr(self.window(), "download_manager_dialog"):
            self.window().download_manager_dialog.add_download(download_item)

    def _check_pdf_open(
        self, state: int, item: QWebEngineDownloadRequest, temp_folder: str
    ) -> None:
        """
        Verifies specific download events monitoring completion specifically resolving PDF interactions appropriately gracefully.

        Args:
            state (int): Evaluated condition metric determining logic branches correctly.
            item (QWebEngineDownloadRequest): Targeted active network artifact dynamically processed.
            temp_folder (str): Originating path references safely maintained systematically.
        """
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            full_path = os.path.join(temp_folder, item.downloadFileName())
            self._on_pdf_downloaded(full_path)

    def _on_pdf_downloaded(self, path: str) -> None:
        """
        Redirects confirmed PDF assets into distinct rendering tab allocations automatically efficiently smoothly.

        Args:
            path (str): Final resulting system file route correctly parsed reliably.
        """
        if (
            os.path.exists(path)
            and self.window()
            and hasattr(self.window(), "open_pdf_in_new_tab")
        ):
            self.window().open_pdf_in_new_tab(path)

    def download_video(self) -> None:
        """
        Instantiates background CLI utility download threads bypassing standard media restrictions completely structurally correctly.
        """
        url = self.web.url().toString()
        if not url or "http" not in url:
            self.show_toast("Invalid URL for download.")
            return

        default_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
        dest_dir = QFileDialog.getExistingDirectory(
            self, "Select Download Directory", default_dir
        )
        if not dest_dir:
            return

        self.show_toast("Starting download...")
        self.progress.setValue(0)

        self.btn_download.setText("⏹")
        self.btn_download.setStyleSheet("color: #FF4500; font-weight: bold;")
        self.btn_download.setToolTip("Cancel Download")
        try:
            self.btn_download.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_download.clicked.connect(self.cancel_download)

        self.dl_worker = YtDlpWorker(url, dest_dir)
        self.dl_worker.progress.connect(self.progress.setValue)
        self.dl_worker.finished.connect(self._on_download_finished)
        self.dl_worker.start()

    def cancel_download(self) -> None:
        """
        Safely halts running video fetch mechanisms terminating processes appropriately cleanly precisely.
        """
        if hasattr(self, "dl_worker") and self.dl_worker.isRunning():
            self.show_toast("Cancelling download...")
            self.dl_worker.stop()

    def toggle_mute(self) -> None:
        """Mutes or unmutes the audio output specifically for this web tab."""
        is_muted = self.btn_mute.isChecked()
        self.web.page().setAudioMuted(is_muted)
        self.btn_mute.setText("🔇" if is_muted else "🔊")

    def print_to_pdf(self) -> None:
        """Renders the current web page directly to a PDF and opens it in Riemann."""
        default_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        suggested_name = (
            f"{self.web.title()}.pdf" if self.web.title() else "webpage.pdf"
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Webpage as PDF",
            os.path.join(default_dir, suggested_name),
            "PDF Files (*.pdf)",
        )

        if path:
            self.show_toast("Rendering PDF...")

            def handle_pdf_print(file_path, success):
                self.web.page().pdfPrintingFinished.disconnect(handle_pdf_print)
                if success:
                    self.show_toast("PDF saved successfully!")
                    if self.window() and hasattr(self.window(), "new_pdf_tab"):
                        self.window().new_pdf_tab(file_path)
                    self.show_toast("Failed to render PDF.")

            self.web.page().pdfPrintingFinished.connect(handle_pdf_print)
            self.web.page().printToPdf(path)

    def _on_download_finished(self, success: bool, message: str) -> None:
        """
        Realigns user interface variables matching completed states correctly presenting messages cleanly successfully accurately.

        Args:
            success (bool): Conditional pass reflecting download health natively explicitly.
            message (str): Information strings structurally appended describing outcome naturally gracefully.
        """
        self.btn_download.setText("⬇")
        self.btn_download.setStyleSheet("")
        self.btn_download.setToolTip("Download Video via yt-dlp")
        try:
            self.btn_download.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_download.clicked.connect(self.download_video)

        if success:
            self.progress.setValue(100)
            QTimer.singleShot(2000, lambda: self.progress.setValue(0))
        else:
            self.progress.setValue(0)

        self.show_toast(message)

    def deleteLater(self) -> None:
        """
        Hooks into the Qt Object deletion pipeline to aggressively scrub and stop
        phantom background Audio/Video playback processes when the tab is closed.
        """
        if hasattr(self, "web") and self.web:
            self.web.page().setAudioMuted(True)
            self.web.setHtml("")
        super().deleteLater()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            focus_widget = QApplication.focusWidget()
            if not isinstance(focus_widget, QLineEdit):
                self.web.setFocus()
