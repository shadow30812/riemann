"""
Web Browser Component.

This module implements a full-featured web browser tab based on QWebEngineView.
It includes support for persistent profiles, ad-blocking, dark mode injection,
audio processing injection (Riemann Audio), and download management.
"""

import os
import re
import subprocess
import sys
from typing import Any, Optional

from PySide6.QtCore import (
    QEvent,
    QObject,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QShortcut
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
    QCompleter,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .browser_handlers import ScriptInjector


class YtDlpWorker(QThread):
    progress = Signal(int)
    finished = Signal(bool, str)

    def __init__(self, url: str, download_dir: str) -> None:
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.process: Optional[subprocess.Popen] = None
        self.is_cancelled = False

    def run(self) -> None:
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
        """Terminates the active yt-dlp subprocess."""
        self.is_cancelled = True
        if self.process:
            self.process.terminate()


class WebPage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self._popups = []

    def createWindow(self, _type):
        """
        Handles background tab opening and popups (like Google Login) by
        creating a temporary view that shares the same profile/session.
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

    def _cleanup_popup(self, popup):
        if popup in self._popups:
            self._popups.remove(popup)

    def javaScriptConsoleMessage(self, level, message, line, source):
        """Print web errors to your Python terminal"""
        self.level = level
        print(f"[JS] {message} (Line {line} in {source})\n\nlevel- {level}")


class RequestInterceptor(QWebEngineUrlRequestInterceptor):
    """
    Handles AdBlocking by intercepting network requests to known advertising
    and tracking domains, User Agent spoofing for WhatsApp,
    and surgical Header injection for Monkeytype/Firebase auth.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        """Initializes the interceptor with a list of blocked domains."""
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
        Blocks the request if the URL contains a blacklisted domain.

        Args:
            info: Information about the URL request.
        """
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
        Initializes the BrowserTab.

        Args:
            start_url: The initial URL to load.
            parent: The parent widget.
            dark_mode: Initial theme state (True for dark mode).
            incognito: Whether to use an ephemeral, in-memory profile.
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

        self.btn_bookmark = QPushButton("☆")
        self.btn_bookmark.setFixedWidth(30)
        self.btn_bookmark.setCheckable(True)
        self.btn_bookmark.setToolTip("Bookmark this page")
        self.btn_bookmark.clicked.connect(self.toggle_bookmark)

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

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setFixedWidth(40)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)

        tb_layout.addWidget(self.btn_back)
        tb_layout.addWidget(self.btn_fwd)
        tb_layout.addWidget(self.btn_reload)

        if self.incognito:
            tb_layout.addWidget(self.btn_incognito_icon)

        tb_layout.addWidget(self.txt_url)
        tb_layout.addWidget(self.btn_bookmark)
        tb_layout.addWidget(self.btn_music)
        tb_layout.addWidget(self.btn_download)
        tb_layout.addWidget(self.lbl_zoom)

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
        page.featurePermissionRequested.connect(self._on_feature_permission_requested)
        page.fullScreenRequested.connect(self._handle_fullscreen_request)
        self.web.setPage(page)
        layout.addWidget(self.web)

        self.btn_back.clicked.connect(self.web.back)
        self.btn_fwd.clicked.connect(self.web.forward)
        self.btn_reload.clicked.connect(self.web.reload)

        self.web.urlChanged.connect(self._update_url_bar)
        self.web.loadProgress.connect(self.progress.setValue)
        self.web.loadFinished.connect(lambda: self.progress.setValue(0))
        self.web.loadFinished.connect(self._restore_music_mode)
        self.web.titleChanged.connect(self._update_tab_title)

        self.shortcut_reload = QShortcut(QKeySequence("F5"), self)
        self.shortcut_reload.activated.connect(self.web.reload)

        self.shortcut_reload_ctrl = QShortcut(QKeySequence("Ctrl+R"), self)
        self.shortcut_reload_ctrl.activated.connect(self.web.reload)

        self.shortcut_hard_reload = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.shortcut_hard_reload.activated.connect(self.hard_reload)

        self.shortcut_f6 = QShortcut(QKeySequence("F6"), self)
        self.shortcut_f6.activated.connect(self.focus_url_bar)

        self.shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_find.activated.connect(self.toggle_search)

        self.shortcut_zoom_in = QShortcut(QKeySequence("Ctrl+="), self)
        self.shortcut_zoom_in.activated.connect(lambda: self.modify_zoom(0.1))

        self.shortcut_zoom_in_alt = QShortcut(QKeySequence("Ctrl++"), self)
        self.shortcut_zoom_in_alt.activated.connect(lambda: self.modify_zoom(0.1))

        self.shortcut_zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        self.shortcut_zoom_out.activated.connect(lambda: self.modify_zoom(-0.1))

        self.shortcut_zoom_out = QShortcut(QKeySequence("Ctrl+_"), self)
        self.shortcut_zoom_out.activated.connect(lambda: self.modify_zoom(-0.1))

        self.shortcut_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        self.shortcut_zoom_reset.activated.connect(self.reset_zoom)

        self.shortcut_back_alt = QShortcut(QKeySequence("Alt+Left"), self)
        self.shortcut_back_alt.activated.connect(self.web.back)

        self.shortcut_fwd_alt = QShortcut(QKeySequence("Alt+Right"), self)
        self.shortcut_fwd_alt.activated.connect(self.web.forward)

        self.shortcut_music = QShortcut(QKeySequence("Ctrl+M"), self)
        self.shortcut_music.activated.connect(self.btn_music.click)

        self.shortcut_devtools_func = QShortcut(QKeySequence("F12"), self)
        self.shortcut_devtools_func.activated.connect(self.open_devtools)

        self.shortcut_devtools = QShortcut(QKeySequence("Ctrl+Shift+I"), self)
        self.shortcut_devtools.activated.connect(self.open_devtools)

        self.apply_theme()
        self.web.installEventFilter(self)

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

        self.web.load(QUrl(start_url))

    def focusInEvent(self, event: Any) -> None:
        """
        Handles the event when the Tab widget itself receives focus.
        Immediately forwards focus to the web view to enable page shortcuts.
        """
        self.web.setFocus()
        super().focusInEvent(event)

    def _on_feature_permission_requested(
        self, url: QUrl, feature: QWebEnginePage.Feature
    ) -> None:
        """
        Auto-grants permissions for Clipboard access so 'Copy' buttons work.
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
        """Opens the Web Inspector for the current page in a separate window."""
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
        """Clears the HTTP cache and reloads the current page."""
        self.profile.clearHttpCache()
        self.web.reload()

    def focus_url_bar(self) -> None:
        """Focuses and selects all text in the URL bar."""
        self.txt_url.setFocus()
        self.txt_url.selectAll()

    def _handle_fullscreen_request(self, request: QWebEngineDownloadRequest) -> None:
        """
        Handles fullscreen requests from web content.
        Toggles the main application window's fullscreen state to match.
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
        """Applies colors based on the current Dark/Light mode setting."""
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

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """
        Filters events to handle specific shortcuts before the WebEngine consumes them.
        """
        if source == self.web and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_F11:
                self.window().toggle_reader_fullscreen()
                return True

            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                key = event.key()
                if key == Qt.Key.Key_T:
                    self.window().open_pdf_smart()
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
        """Increments or decrements the zoom factor and updates the label."""
        new_factor = max(0.1, min(self.web.zoomFactor() + delta, 5.0))
        self.web.setZoomFactor(new_factor)
        self.lbl_zoom.setText(f"{int(new_factor * 100)}%")

    def reset_zoom(self) -> None:
        """Resets zoom to 100% and updates the label."""
        self.web.setZoomFactor(1.0)
        self.lbl_zoom.setText("100%")

    def toggle_search(self) -> None:
        """Toggles the visibility of the find-in-page bar."""
        self.search_bar.setVisible(not self.search_bar.isVisible())
        if self.search_bar.isVisible():
            self.txt_find.setFocus()

    def find_next(self) -> None:
        """Finds the next occurrence of the search text."""
        self.web.findText(self.txt_find.text())

    def find_prev(self) -> None:
        """Finds the previous occurrence of the search text."""
        self.web.findText(self.txt_find.text(), QWebEngineView.FindFlag.FindBackward)

    def navigate_to_url(self) -> None:
        """Loads the URL entered in the address bar."""
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
        """Handles window resize events to center the toast notification."""
        super().resizeEvent(event)
        if self.lbl_toast.isVisible():
            self.lbl_toast.move(
                (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
            )

    def show_toast(self, message: str) -> None:
        """Displays a temporary notification overlay."""
        self.lbl_toast.setText(message)
        self.lbl_toast.adjustSize()
        self.lbl_toast.move(
            (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
        )
        self.lbl_toast.show()
        self.lbl_toast.raise_()
        QTimer.singleShot(3000, self.lbl_toast.hide)

    def _update_url_bar(self, url: QUrl) -> None:
        """Updates URL bar text and adds the URL to history."""
        s_url = url.toString()
        self.txt_url.setText(s_url)
        self.txt_url.setCursorPosition(0)

        if (
            not self.incognito
            and self.window()
            and hasattr(self.window(), "add_to_history")
        ):
            self.window().add_to_history(s_url, "web")

        self._update_bookmark_icon(s_url)

    def _update_bookmark_icon(self, url: str) -> None:
        """Updates the bookmark button state based on the current URL."""
        if self.window() and hasattr(self.window(), "bookmarks_manager"):
            is_bm = self.window().bookmarks_manager.is_bookmarked(url)
            self.btn_bookmark.setChecked(is_bm)
            self.btn_bookmark.setText("★" if is_bm else "☆")

    def toggle_bookmark(self) -> None:
        """Toggles bookmark status for current URL."""
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
        """Updates the parent tab widget's title."""
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

    def get_audio_script(self) -> str:
        """
        Loads the audio_engine.js file from the assets directory.
        Returns the script content as a string.
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
        """Toggles the Audio Engine state based on button check status."""
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
        """Re-enables music mode after page navigation if button is checked."""
        if self.btn_music.isChecked():
            QTimer.singleShot(1000, self.toggle_music_mode)

    def _handle_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """Handles file download requests via a file dialog."""
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
        """Checks if a download is a PDF and opens it if complete."""
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            full_path = os.path.join(temp_folder, item.downloadFileName())
            self._on_pdf_downloaded(full_path)

    def _on_pdf_downloaded(self, path: str) -> None:
        """Callback when an auto-downloaded PDF finishes."""
        if (
            os.path.exists(path)
            and self.window()
            and hasattr(self.window(), "open_pdf_in_new_tab")
        ):
            self.window().open_pdf_in_new_tab(path)

    def download_video(self) -> None:
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

        # Transform the download button into a Cancel button
        self.btn_download.setText("⏹")
        self.btn_download.setStyleSheet("color: #FF4500; font-weight: bold;")
        self.btn_download.setToolTip("Cancel Download")
        try:
            self.btn_download.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_download.clicked.connect(self.cancel_download)

        # Initialize and start the background thread
        self.dl_worker = YtDlpWorker(url, dest_dir)
        self.dl_worker.progress.connect(self.progress.setValue)
        self.dl_worker.finished.connect(self._on_download_finished)
        self.dl_worker.start()

    def cancel_download(self) -> None:
        """Triggered when the user clicks the Stop button."""
        if hasattr(self, "dl_worker") and self.dl_worker.isRunning():
            self.show_toast("Cancelling download...")
            self.dl_worker.stop()

    def _on_download_finished(self, success: bool, message: str) -> None:
        # Revert the Cancel button back to a Download button
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
