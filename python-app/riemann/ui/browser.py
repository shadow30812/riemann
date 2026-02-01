import os
from typing import Any, Optional

from PySide6.QtCore import QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
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


class AdBlockInterceptor(QWebEngineUrlRequestInterceptor):
    """
    Intercepts network requests and blocks known ad/tracking domains.
    """

    def __init__(self, parent=None):
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
        ]

    def interceptRequest(self, info: QWebEngineUrlRequestInfo):
        url = info.requestUrl().toString().lower()
        if any(domain in url for domain in self.blocked_domains):
            info.block(True)


class BrowserTab(QWidget):
    """
    A full-featured web browser tab using QWebEngineView.

    Includes navigation controls, dark mode support, ad-blocking,
    history autocomplete, and download management integration.
    """

    def __init__(
        self,
        start_url: str = "https://www.google.com",
        parent: Optional[QWidget] = None,
        dark_mode: bool = True,
    ) -> None:
        """
        Initializes the BrowserTab.

        Args:
            start_url: The initial URL to load.
            parent: The parent widget.
            dark_mode: Initial theme state (True for dark mode).
        """
        super().__init__(parent)
        self.dark_mode = dark_mode

        base_path = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        storage_path = os.path.join(base_path, "browser_data")
        os.makedirs(storage_path, exist_ok=True)

        self.profile = QWebEngineProfile("RiemannPersistentProfile", self)
        self.profile.setPersistentStoragePath(storage_path)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.ad_interceptor = AdBlockInterceptor(self)
        self.profile.setUrlRequestInterceptor(self.ad_interceptor)

        self.profile.downloadRequested.connect(self._handle_download)
        self.inject_ad_skipper()
        self.inject_backspace_handler()

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
        self.txt_url.setCompleter(self.completer)

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

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setFixedWidth(40)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)

        tb_layout.addWidget(self.btn_back)
        tb_layout.addWidget(self.btn_fwd)
        tb_layout.addWidget(self.btn_reload)
        tb_layout.addWidget(self.txt_url)
        tb_layout.addWidget(self.btn_bookmark)
        tb_layout.addWidget(self.btn_music)
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

        self.web = QWebEngineView()
        page = QWebEnginePage(self.profile, self.web)
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PdfViewerEnabled, False
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
        )
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

        if self.window() and hasattr(self.window(), "history_model"):
            self.completer.setModel(self.window().history_model)

        self.web.load(QUrl(start_url))

    def open_devtools(self) -> None:
        """Opens the Web Inspector for the current page."""
        if not hasattr(self, "_devtools_window"):
            self._devtools_window = QWidget()
            self._devtools_window.setWindowTitle("Inspector")
            self._devtools_window.resize(800, 600)

            layout = QVBoxLayout(self._devtools_window)
            layout.setContentsMargins(0, 0, 0, 0)

            self._devtools_view = QWebEngineView()
            layout.addWidget(self._devtools_view)

            # Link the inspector to the current page
            self._devtools_view.page().setInspectedPage(self.web.page())

        self._devtools_window.show()
        self._devtools_window.raise_()

    def hard_reload(self) -> None:
        """Clears the HTTP cache and reloads the current page."""
        self.profile.clearHttpCache()
        self.web.reload()

    def inject_ad_skipper(self) -> None:
        """Injects JavaScript to automatically skip video advertisements."""
        js_code = """
        (function() {
            const clearAds = () => {
                const skipBtns = document.querySelectorAll('.ytp-ad-skip-button, .ytp-ad-skip-button-modern, .videoAdUiSkipButton');
                skipBtns.forEach(b => { b.click(); });
                const overlays = document.querySelectorAll('.ytp-ad-overlay-close-button');
                overlays.forEach(b => { b.click(); });
                const video = document.querySelector('video');
                const adShowing = document.querySelector('.ad-showing');
                if (video && adShowing) {
                    video.playbackRate = 16.0;
                    video.muted = true;
                    if(isFinite(video.duration)) video.currentTime = video.duration;
                }
            };
            setInterval(clearAds, 50);
        })();
        """
        script = QWebEngineScript()
        script.setName("RiemannAdBlock")
        script.setSourceCode(js_code)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        script.setRunsOnSubFrames(True)
        self.profile.scripts().insert(script)

    def inject_backspace_handler(self) -> None:
        """Injects JavaScript to handle Backspace navigation logic."""
        js_code = """
        document.addEventListener("keydown", function(e) {
            if (e.key === "Backspace" && !e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
                const tag = document.activeElement.tagName;
                const type = document.activeElement.type;
                const isInput = (tag === "INPUT" && type !== "button" && type !== "submit" && type !== "checkbox" && type !== "radio") 
                                || tag === "TEXTAREA" 
                                || document.activeElement.isContentEditable;
                if (!isInput) {
                    e.preventDefault();
                    window.history.back();
                }
            }
        });
        """
        script = QWebEngineScript()
        script.setName("RiemannBackspace")
        script.setSourceCode(js_code)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        script.setRunsOnSubFrames(True)
        self.profile.scripts().insert(script)

    def focus_url_bar(self) -> None:
        """Focuses and selects all text in the URL bar."""
        self.txt_url.setFocus()
        self.txt_url.selectAll()

    def _handle_fullscreen_request(self, request: QWebEngineDownloadRequest) -> None:
        """Handles fullscreen requests from web content."""
        request.accept()
        if self.window() and hasattr(self.window(), "toggle_reader_fullscreen"):
            is_fs = getattr(self.window(), "_reader_fullscreen", False)
            if request.toggleOn() != is_fs:
                self.window().toggle_reader_fullscreen()

    def apply_theme(self) -> None:
        """Applies colors based on the current Dark/Light mode setting."""
        settings = self.web.page().settings()
        if self.dark_mode:
            bg, fg, inp_bg, border = "#333", "#ddd", "#444", "#555"
            settings.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, True)
            self.web.page().setBackgroundColor(QColor("#333"))
        else:
            bg, fg, inp_bg, border = "#f0f0f0", "#222", "#fff", "#ccc"
            settings.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, False)
            self.web.page().setBackgroundColor(QColor("#fff"))
        style = f"QWidget {{ background: {bg}; color: {fg}; }} QLineEdit {{ background: {inp_bg}; border: 1px solid {border}; border-radius: 4px; padding: 4px; }}"
        self.toolbar.setStyleSheet(style)
        self.search_bar.setStyleSheet(style)

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
        """Handles window resize events."""
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
        """Updates URL bar and adds the URL to history."""
        s_url = url.toString()
        self.txt_url.setText(s_url)
        self.txt_url.setCursorPosition(0)

        if self.window() and hasattr(self.window(), "add_to_history"):
            self.window().add_to_history(s_url)

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

    def get_audio_script(self):
        """
        Robustly loads the audio_engine.js file.
        Anchors the path relative to this browser.py file.
        """
        try:
            # 1. Get the directory containing browser.py (riemann/ui)
            current_dir = os.path.dirname(os.path.abspath(__file__))

            # 2. Go up one level to 'riemann', then into 'assets'
            # Result: .../riemann/assets/audio_engine.js
            script_path = os.path.join(current_dir, "..", "assets", "audio_engine.js")
            script_path = os.path.abspath(script_path)  # Normalize path

            if not os.path.exists(script_path):
                print(f"[Riemann Error] Audio Engine not found at: {script_path}")
                self.show_toast("Error: Missing audio_engine.js")
                return ""

            with open(script_path, "r", encoding="utf-8") as f:
                content = f.read()
                return content

        except Exception as e:
            print(f"[ERROR] Failed to load audio script: {e}")
            return ""

    def toggle_music_mode(self) -> None:
        """Toggles the Audio Engine state based on button check status."""
        is_active = self.btn_music.isChecked()

        # 1. Prepare Script
        base_js = self.get_audio_script()
        if not base_js:
            print("[ERROR] Aborting injection: Script content is empty.")
            self.btn_music.setChecked(False)
            return

        # 2. Command: Enable or Disable
        if is_active:
            command = "if(window.RiemannAudio) window.RiemannAudio.enable();"
            self.show_toast("Music Mode ON")
        else:
            command = "if(window.RiemannAudio) window.RiemannAudio.disable();"
            self.show_toast("Music Mode OFF")

        # 3. Inject (Base + Command)
        # We inject the base engine every time just in case it wasn't loaded
        full_script = base_js + "\n" + command
        self.web.page().runJavaScript(full_script)

    def _restore_music_mode(self) -> None:
        """Re-enables music mode after page navigation if button is checked."""
        if self.btn_music.isChecked():
            # Wait briefly for DOM to settle, then inject
            QTimer.singleShot(1000, self.toggle_music_mode)

    def _handle_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """Handles file download requests."""
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
