"""
Web Browser Component.

This module implements a full-featured web browser tab based on QWebEngineView.
It includes support for persistent profiles, ad-blocking, dark mode injection,
audio processing injection (Riemann Audio), and download management.
"""

import base64
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import urllib.parse
from typing import Any, Optional

try:
    import yt_dlp
except ImportError:
    pass

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QIODevice,
    QObject,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
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
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QSlider,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.captions_server import CaptionsServer
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


class YtDlpStreamWorker(QThread):
    """
    Background worker thread for extracting raw media stream URLs using yt-dlp.

    This thread queries the provided target URL to extract a direct playback link
    (e.g., an mp4 stream) without downloading the entire media file. This is utilized
    primarily for piping proprietary or unsupported video codecs directly to native
    media players like VLC or MPV.
    """

    finished = Signal(str)
    error = Signal(str)

    def __init__(self, url):
        """
        Initializes the stream extraction worker.

        Args:
            url (str): The source web URL containing the media to be extracted.
        """
        super().__init__()
        self.url = url

    def run(self):
        """
        Executes the yt-dlp metadata extraction process asynchronously.

        Runs yt-dlp to parse the media URL. Emits the `finished` signal with the raw
        stream URL if successful, or emits the `error` signal with a description of the
        failure if extraction fails or no URL is found in the response.
        """
        ydl_opts: dict[str, Any] = {
            "format": "best[ext=mp4]",
            "quiet": True,
            "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if info and "url" in info:
                    self.finished.emit(info["url"])
                else:
                    self.error.emit("No URL found in response")
        except Exception as e:
            self.error.emit(str(e))


class MockDownloadItem(QObject):
    """
    A duck-typed mock of QWebEngineDownloadRequest.
    Allows yt-dlp to report its progress directly into the native Download Manager.
    """

    stateChanged = Signal(QWebEngineDownloadRequest.DownloadState)
    isFinishedChanged = Signal()
    receivedBytesChanged = Signal()
    totalBytesChanged = Signal()

    def __init__(self, url: str, download_dir: str, filename: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._dir = download_dir
        self._filename = filename
        self._state = QWebEngineDownloadRequest.DownloadState.DownloadInProgress
        self._total = 100
        self._received = 0
        self.worker: Optional["YtDlpWorker"] = None

    def id(self) -> int:
        return id(self)

    def downloadFileName(self) -> str:
        return self._filename

    def downloadDirectory(self) -> str:
        return self._dir

    def state(self) -> QWebEngineDownloadRequest.DownloadState:
        return self._state

    def totalBytes(self) -> int:
        return self._total

    def receivedBytes(self) -> int:
        return self._received

    def url(self) -> QUrl:
        return QUrl(self._url)

    def mimeType(self) -> str:
        return "video/mp4"

    def isFinished(self) -> bool:
        return self._state != QWebEngineDownloadRequest.DownloadState.DownloadInProgress

    def isPaused(self) -> bool:
        return False

    def interruptReason(self) -> int:
        return 0

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def accept(self) -> None:
        pass

    def cancel(self) -> None:
        if self.worker:
            self.worker.stop()
        self._state = QWebEngineDownloadRequest.DownloadState.DownloadCancelled
        self.stateChanged.emit(self._state)
        self.isFinishedChanged.emit()

    def update_progress(self, percent: int) -> None:
        self._received = percent
        self.receivedBytesChanged.emit()

    def finish(self, success: bool, msg: str) -> None:
        if success:
            self._state = QWebEngineDownloadRequest.DownloadState.DownloadCompleted
            self._received = self._total
            self.receivedBytesChanged.emit()
        else:
            self._state = QWebEngineDownloadRequest.DownloadState.DownloadInterrupted
        self.stateChanged.emit(self._state)
        self.isFinishedChanged.emit()


class YtDlpWorker(QThread):
    """
    Background worker thread for executing yt-dlp media downloads via the Python API.
    Reports progress and completion status asynchronously to the main thread.
    """

    progress = Signal(int)
    finished = Signal(bool, str)

    def __init__(self, url: str, download_dir: str, dl_opts: dict) -> None:
        """
        Initializes the yt-dlp download worker.

        Args:
            url (str): The target media URL to download.
            download_dir (str): The local directory path to save the downloaded file.
            dl_opts (dict): Dictionary containing user-selected download options.
        """
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.dl_opts = dl_opts
        self.is_cancelled = False

    def run(self) -> None:
        """
        Executes the yt-dlp Python API, mapping user options to internal flags,
        and manages the download lifecycle safely within the process.
        """
        ydl_opts = {
            "outtmpl": os.path.join(self.download_dir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self.progress_hook],
        }

        if self.dl_opts.get("playlist"):
            ydl_opts["noplaylist"] = False
            pl_start = self.dl_opts.get("playlist_start")
            pl_end = self.dl_opts.get("playlist_end")

            if pl_start and pl_start.isdigit():
                ydl_opts["playliststart"] = int(pl_start)
            if pl_end and pl_end.isdigit():
                ydl_opts["playlistend"] = int(pl_end)
        else:
            ydl_opts["noplaylist"] = True

        cookies_browser = self.dl_opts.get("cookies", "none")
        if cookies_browser != "none":
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        ydl_opts["format"] = self.dl_opts.get("format", "best")
        postprocessors = []

        if self.dl_opts.get("audio_only"):
            audio_fmt = self.dl_opts.get("audio_format", "best")
            pp = {"key": "FFmpegExtractAudio"}
            if audio_fmt != "best":
                pp["preferredcodec"] = audio_fmt
            postprocessors.append(pp)
        else:
            ydl_opts["merge_output_format"] = "mp4"

        if self.dl_opts.get("subtitles") and not self.dl_opts.get("audio_only"):
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = ["en.*"]
            ydl_opts["compat_opts"] = ["no-keep-subs"]
            postprocessors.append({"key": "FFmpegEmbedSubtitle"})

        if postprocessors:
            ydl_opts["postprocessors"] = postprocessors

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

            if self.is_cancelled:
                self.finished.emit(False, "Download cancelled.")
            else:
                self.finished.emit(True, "Download complete!")

        except Exception as e:
            if self.is_cancelled:
                self.finished.emit(False, "Download cancelled.")
            else:
                self.finished.emit(False, str(e))

    def progress_hook(self, d: dict) -> None:
        """
        Internal hook called by yt-dlp to report download progress.
        Also acts as our cancellation trigger by throwing an exception if the user aborts.
        """
        if self.is_cancelled:
            raise Exception("Download aborted by user.")

        if d.get("status") == "downloading":
            percent_str = d.get("_percent_str", "0%")
            percent_str = re.sub(r"\x1b\[[0-9;]*m", "", percent_str)
            try:
                val = float(percent_str.replace("%", "").strip())
                self.progress.emit(int(val))
            except ValueError:
                pass

    def stop(self) -> None:
        """
        Flags the thread for cancellation. The progress_hook will catch this on the next tick.
        """
        self.is_cancelled = True


class YtDlpSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download Settings")
        self.setFixedSize(360, 360)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Video Quality:"))
        self.combo_video = QComboBox()
        self.combo_video.addItems(
            [
                "Best Available",
                "4K (2160p)",
                "QHD (1440p)",
                "FHD (1080p)",
                "HD (720p)",
                "SD (480p)",
                "LoQ (144p)",
                "Audio Only",
            ]
        )
        self.combo_video.setStyleSheet("padding: 5px;")
        layout.addWidget(self.combo_video)

        layout.addWidget(QLabel("Audio Format (for Audio Only):"))
        self.combo_audio = QComboBox()
        self.combo_audio.addItems(["best", "mp3", "m4a", "flac", "wav"])
        self.combo_audio.setStyleSheet("padding: 5px;")
        layout.addWidget(self.combo_audio)

        self.chk_subs = QCheckBox("Download and embed English subtitles")
        self.chk_subs.setChecked(True)
        layout.addWidget(self.chk_subs)

        self.chk_playlist = QCheckBox("Download playlist (if applicable)")
        self.chk_playlist.setChecked(False)
        layout.addWidget(self.chk_playlist)

        pl_layout = QHBoxLayout()
        pl_layout.addWidget(QLabel("Playlist Range:"))
        self.txt_pl_start = QLineEdit()
        self.txt_pl_start.setPlaceholderText("Start (e.g. 1)")
        self.txt_pl_end = QLineEdit()
        self.txt_pl_end.setPlaceholderText("End (e.g. 12)")
        pl_layout.addWidget(self.txt_pl_start)
        pl_layout.addWidget(self.txt_pl_end)
        layout.addLayout(pl_layout)

        layout.addWidget(QLabel("Extract Cookies From (Bypass Blocks):"))
        self.combo_cookies = QComboBox()
        self.combo_cookies.addItems(
            ["None", "chrome", "edge", "firefox", "brave", "opera", "safari", "vivaldi"]
        )
        self.combo_cookies.setStyleSheet("padding: 5px;")
        layout.addWidget(self.combo_cookies)

        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("Download")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok.setStyleSheet(
            "background-color: #ff4500; color: white; font-weight: bold;"
        )

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_ok)
        layout.addLayout(btn_layout)

        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def get_options(self) -> dict:
        v_idx = self.combo_video.currentIndex()
        audio_only = v_idx == 5

        if audio_only:
            fmt = "bestaudio/best"
        elif v_idx == 0:
            fmt = "bestvideo+bestaudio/best"
        elif v_idx == 1:
            fmt = "bestvideo[height<=2160]+bestaudio/best"
        elif v_idx == 2:
            fmt = "bestvideo[height<=1440]+bestaudio/best"
        elif v_idx == 3:
            fmt = "bestvideo[height<=1080]+bestaudio/best"
        elif v_idx == 4:
            fmt = "bestvideo[height<=720]+bestaudio/best"
        elif v_idx == 5:
            fmt = "bestvideo[height<=480]+bestaudio/best"
        elif v_idx == 6:
            fmt = "bestvideo[height<=144]+bestaudio/best"
        else:
            fmt = "best"

        return {
            "format": fmt,
            "audio_only": audio_only,
            "audio_format": self.combo_audio.currentText(),
            "subtitles": self.chk_subs.isChecked(),
            "playlist": self.chk_playlist.isChecked(),
            "playlist_start": self.txt_pl_start.text().strip(),
            "playlist_end": self.txt_pl_end.text().strip(),
            "cookies": self.combo_cookies.currentText().lower(),
        }


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
        page.windowCloseRequested.connect(popup_view.close)

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
            settings = QSettings("Riemann", "PDFReader")
            current_blocks = settings.value("browser/ads_blocked", 0, type=int)
            settings.setValue("browser/ads_blocked", current_blocks + 1)
            return

        if "whatsapp.com" in url:
            info.setHttpHeader(b"User-Agent", self.spoofed_ua)


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
        icon_size = QSize(18, 18)

        if profile:
            self.profile = profile
        else:
            self.profile = QWebEngineProfile(self)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._audio_timer = QTimer(self)
        self._audio_timer.setSingleShot(True)
        self._audio_timer.timeout.connect(self._clear_audio_state)

        self.request_interceptor = RequestInterceptor(self)
        self.profile.setUrlRequestInterceptor(self.request_interceptor)
        self.profile.downloadRequested.connect(self._handle_download)

        self.script_injector = ScriptInjector(self.profile)
        self.script_injector.inject_ad_skipper()
        self.script_injector.inject_backspace_handler()
        self.script_injector.inject_emoji_fallback()
        self.script_injector.inject_whatsapp_ua()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(40)
        tb_layout = QHBoxLayout(self.toolbar)
        tb_layout.setContentsMargins(5, 0, 5, 0)

        self.btn_back = QPushButton()
        self.btn_back.setIcon(
            QIcon(
                get_resource_path(
                    os.path.join("..", "assets", "icons", "chevron-left.svg")
                )
            )
        )
        self.btn_back.setIconSize(icon_size)
        self.btn_back.setFixedWidth(30)

        self.btn_fwd = QPushButton()
        self.btn_fwd.setIcon(
            QIcon(
                get_resource_path(
                    os.path.join("..", "assets", "icons", "chevron-right.svg")
                )
            )
        )
        self.btn_fwd.setIconSize(icon_size)
        self.btn_fwd.setFixedWidth(30)

        self.btn_reload = QPushButton()
        self.btn_reload.setIcon(
            QIcon(
                get_resource_path(
                    os.path.join("..", "assets", "icons", "rotate-cw.svg")
                )
            )
        )
        self.btn_reload.setIconSize(icon_size)
        self.btn_reload.setFixedWidth(30)

        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("Enter URL or Search...")
        self.txt_url.returnPressed.connect(self.navigate_to_url)

        self.completer = QCompleter()
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.txt_url.setCompleter(self.completer)

        self.action_security = self.txt_url.addAction(
            QIcon(get_resource_path(os.path.join("..", "assets", "icons", "file.svg"))),
            QLineEdit.ActionPosition.LeadingPosition,
        )

        if self.incognito:
            self.btn_incognito_icon = QPushButton()
            self.btn_incognito_icon.setIcon(
                QIcon(
                    get_resource_path(
                        os.path.join("..", "assets", "icons", "incognito.svg")
                    )
                )
            )
            self.btn_incognito_icon.setIconSize(icon_size)
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

        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setIcon(
            QIcon(
                get_resource_path(
                    os.path.join(
                        "..",
                        "assets",
                        "icons",
                        "sun.svg" if getattr(self, "dark_mode", False) else "moon.svg",
                    )
                )
            )
        )
        self.btn_theme_toggle.setIconSize(icon_size)
        self.btn_theme_toggle.setFixedWidth(30)
        self.btn_theme_toggle.setToolTip("Toggle Browser Dark Mode")
        self.btn_theme_toggle.clicked.connect(self.toggle_theme)

        self.btn_bookmark = QPushButton()
        self.btn_bookmark.setIcon(
            QIcon(
                get_resource_path(os.path.join("..", "assets", "icons", "bookmark.svg"))
            )
        )
        self.btn_bookmark.setIconSize(icon_size)
        self.btn_bookmark.setObjectName("bookmarkBtn")
        self.btn_bookmark.setFixedWidth(30)
        self.btn_bookmark.setCheckable(True)
        self.btn_bookmark.setToolTip("Bookmark this page")
        self.btn_bookmark.clicked.connect(self.toggle_bookmark)

        self.btn_captions = QPushButton()
        self.btn_captions.setIcon(
            QIcon(
                get_resource_path(
                    os.path.join("..", "assets", "icons", "captions-off.svg")
                )
            )
        )
        self.btn_captions.setIconSize(icon_size)
        self.btn_captions.setObjectName("captionsBtn")
        self.btn_captions.setFixedWidth(30)
        self.btn_captions.setCheckable(True)
        self.btn_captions.setToolTip("Toggle Live Captions (Any Language to English)")
        self.btn_captions.clicked.connect(self.toggle_captions)

        self.btn_mute = QPushButton()
        self.btn_mute.setIcon(
            QIcon(
                get_resource_path(
                    os.path.join("..", "assets", "icons", "volume-on.svg")
                )
            )
        )
        self.btn_mute.setIconSize(icon_size)
        self.btn_mute.setFixedWidth(30)
        self.btn_mute.setCheckable(True)
        self.btn_mute.setToolTip("Mute/Unmute Tab")
        self.btn_mute.clicked.connect(self.toggle_mute)

        self.btn_music = QPushButton()
        self.btn_music.setIcon(
            QIcon(get_resource_path(os.path.join("..", "assets", "icons", "music.svg")))
        )
        self.btn_music.setIconSize(icon_size)
        self.btn_music.setFixedWidth(30)
        self.btn_music.setCheckable(True)
        self.btn_music.setToolTip("Toggle Audiophile Music Mode")
        self.btn_music.clicked.connect(self.toggle_music_mode)

        self.btn_video_speed = QPushButton()
        self.btn_video_speed.setIcon(
            QIcon(get_resource_path(os.path.join("..", "assets", "icons", "gauge.svg")))
        )
        self.btn_video_speed.setIconSize(icon_size)
        self.btn_video_speed.setFixedWidth(30)
        self.btn_video_speed.setCheckable(True)
        self.btn_video_speed.setToolTip("Toggle Video Speed Controller")
        self.btn_video_speed.clicked.connect(self.toggle_video_mode)

        self.btn_stream = QPushButton()
        self.btn_stream.setIcon(
            QIcon(
                get_resource_path(os.path.join("..", "assets", "icons", "airplay.svg"))
            )
        )
        self.btn_stream.setIconSize(icon_size)
        self.btn_stream.setFixedWidth(30)
        self.btn_stream.setToolTip("Stream Video Externally")
        self.btn_stream.clicked.connect(self.stream_video)

        self.btn_download = QPushButton()
        self.btn_download.setIcon(
            QIcon(
                get_resource_path(os.path.join("..", "assets", "icons", "download.svg"))
            )
        )
        self.btn_download.setIconSize(icon_size)
        self.btn_download.setFixedWidth(30)
        self.btn_download.setToolTip("Download Video via yt-dlp")
        self.btn_download.clicked.connect(self.download_video)

        self.btn_print_pdf = QPushButton()
        self.btn_print_pdf.setIcon(
            QIcon(
                get_resource_path(os.path.join("..", "assets", "icons", "printer.svg"))
            )
        )
        self.btn_print_pdf.setIconSize(icon_size)
        self.btn_print_pdf.setFixedWidth(30)
        self.btn_print_pdf.setToolTip("Save Webpage to PDF")
        self.btn_print_pdf.clicked.connect(self.print_to_pdf)

        self.btn_zoom = QPushButton("100%")
        self.btn_zoom.setFixedWidth(65)
        self.btn_zoom.setToolTip("Zoom Controls")

        zoom_menu = QMenu(self.btn_zoom)
        action_zoom_in = QAction("(+) Zoom In", self)
        action_zoom_in.triggered.connect(lambda: self.modify_zoom(0.1))

        action_zoom_out = QAction("(-) Zoom Out", self)
        action_zoom_out.triggered.connect(lambda: self.modify_zoom(-0.1))

        action_zoom_reset = QAction("Reset (100%)", self)
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
        tb_layout.addWidget(self.btn_captions)
        tb_layout.addWidget(self.btn_mute)
        tb_layout.addWidget(self.btn_music)
        tb_layout.addWidget(self.btn_video_speed)
        tb_layout.addWidget(self.btn_theme_toggle)
        tb_layout.addWidget(self.btn_stream)
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
        layout.insertWidget(0, self.progress)

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
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
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
        self.web.titleChanged.connect(self._update_tab_title)

        self.web.loadFinished.connect(lambda: self.progress.setValue(0))
        self.web.loadFinished.connect(self._restore_music_mode)
        self.web.loadFinished.connect(self._on_homepage_load_finished)

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

        self.shortcut_video = QShortcut(QKeySequence("Ctrl+G"), self)
        self.shortcut_video.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_video.activated.connect(self.btn_video_speed.click)

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
                os.path.join(base_dir, "..", "assets", "homepage", "homepage.html")
            )

            if os.path.exists(homepage_path):
                self.web.load(QUrl.fromLocalFile(homepage_path))
            else:
                self.web.load(QUrl("https://www.google.com"))
        else:
            self.web.load(QUrl(start_url))

        for widget_class in (QPushButton, QToolButton, QComboBox):
            for w in self.findChildren(widget_class):
                w.setCursor(Qt.CursorShape.PointingHandCursor)

        app = QApplication.instance()
        if not hasattr(app, "captions_server"):
            app.captions_server = CaptionsServer()
        self.captions_server = app.captions_server

        self.link_tooltip = QLabel(self)
        self.link_tooltip.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; padding: 3px 7px; "
            "border: 1px solid #444; border-radius: 3px; font-size: 11px;"
        )
        self.link_tooltip.hide()
        self.web.page().linkHovered.connect(self._on_link_hovered)
        self.web.page().recentlyAudibleChanged.connect(self._on_audio_state_changed)

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
            settings = QSettings("Riemann", "PDFReader")
            name = settings.value("homepage/custom_name", "", type=str)

            if not name:
                try:
                    name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
                    if not name:
                        name = os.getlogin()
                except Exception:
                    name = os.getlogin()

            links = settings.value("homepage_links", "null")
            fav_settings = QSettings("Riemann", "FaviconCache")
            cached_icons = {}
            for key in fav_settings.allKeys():
                cached_icons[key] = fav_settings.value(key, "", type=str)
            icons_json = json.dumps(cached_icons)

            js_code = f"window.cachedIcons = {icons_json}; window.initHomepage('{name}', {links});"
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
        style = f"QWidget {{ background: {bg}; \
                                            color: {fg}; }} \
                                QLineEdit {{ background: {inp_bg}; \
                                            border: 1px solid {border}; \
                                            border-radius: 4px; \
                                            padding: 4px; }}"
        self.toolbar.setStyleSheet(style)
        self.search_bar.setStyleSheet(style)

        if self.incognito:
            self.txt_url.setStyleSheet("""
                QLineEdit { border: 2px solid #6A0DAD; 
                            background-color: #2D2D2D; 
                            color: white; 
                            border-radius: 4px; 
                            padding: 4px; }
            """)

        menu_bg = "#2C2C30" if self.dark_mode else "#F0F0F0"
        menu_fg = "#E0E0E0" if self.dark_mode else "#111111"
        menu_border = "#3F3F46" if self.dark_mode else "#CCCCCC"
        if hasattr(self, "btn_zoom") and self.btn_zoom.menu():
            self.btn_zoom.menu().setStyleSheet(
                f"QMenu {{ background: {menu_bg}; \
                           color: {menu_fg}; \
                           border: 1px solid {menu_border}; }} \
                QMenu::item:selected {{ background: rgba(128, 128, 128, 0.2); }}"
            )

        if hasattr(self, "btn_back"):
            self._update_icons()

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

                    if key == Qt.Key.Key_B:
                        if self.window() and hasattr(self.window(), "new_browser_tab"):
                            self.window().new_browser_tab()
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
        domain = self.web.url().host()

        if domain:
            QSettings("Riemann", "BrowserSettings").setValue(
                f"zoom/{domain}", new_factor
            )

    def reset_zoom(self) -> None:
        """
        Resets the rendering layout zoom factor uniformly to absolute normal metrics.
        """
        self.web.setZoomFactor(1.0)
        self.btn_zoom.setText("100%")
        domain = self.web.url().host()

        if domain:
            QSettings("Riemann", "BrowserSettings").setValue(f"zoom/{domain}", 1.0)

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

        domain = url.host()
        if domain:
            saved_zoom = float(
                QSettings("Riemann", "BrowserSettings").value(f"zoom/{domain}", 1.0)
            )
            self.web.setZoomFactor(saved_zoom)
            self.btn_zoom.setText(f"{int(saved_zoom * 100)}%")

        scheme = url.scheme()
        if scheme == "https":
            self.action_security.setIcon(self._get_icon("lock.svg"))
            self.action_security.setToolTip("Connection is secure")
        elif scheme == "http":
            self.action_security.setIcon(self._get_icon("lock-open.svg"))
            self.action_security.setToolTip("Connection is not secure")
        else:
            self.action_security.setIcon(self._get_icon("file.svg"))
            self.action_security.setToolTip("Local File")

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
            self.btn_bookmark.setIcon(
                self._get_icon("bookmark-filled.svg" if is_bm else "bookmark.svg")
            )

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

    def toggle_captions(self) -> None:
        """Toggles the live captioning overlay on the web page."""
        is_enabled = self.btn_captions.isChecked()
        icon_name = "captions.svg" if is_enabled else "captions-off.svg"
        self.btn_captions.setIcon(self._get_icon(icon_name))

        if is_enabled:
            js_payload = self.get_captions_script()
            port = self.captions_server.port if self.captions_server else 8765
            self.web.page().runJavaScript(f"""
                window.RIEMANN_CAPTIONS_PORT = {port};
                {js_payload}
                if (window.RiemannCaptions) window.RiemannCaptions.enable();
            """)
            self.show_toast("Live Captions Enabled")
        else:
            self.web.page().runJavaScript(
                "if (window.RiemannCaptions) window.RiemannCaptions.disable();"
            )
            self.show_toast("Live Captions Disabled")

    def get_captions_script(self) -> str:
        """Loads the raw Javascript payload for the captions overlay engine."""
        try:
            candidate_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "assets",
                "caption_engine.js",
            )
            with open(candidate_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[ERROR] Failed to load captions script: {e}")
            return ""

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

        Args:
            icon: The QIcon extracted from the rendered webpage.
        """

        parent = self.parent()
        icon_path = get_resource_path(
            os.path.join("..", "assets", "icons", "browser.png")
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

        if not icon.isNull():
            url = self.web.url()
            host = url.host()
            if host and host not in ["riemann-save.local", "newtab"]:
                pixmap = icon.pixmap(64, 64)
                if not pixmap.isNull():
                    byte_array = QByteArray()
                    buffer = QBuffer(byte_array)
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    pixmap.save(buffer, "PNG")
                    b64_data = base64.b64encode(byte_array.data()).decode("utf-8")
                    data_uri = f"data:image/png;base64,{b64_data}"

                    fav_settings = QSettings("Riemann", "FaviconCache")
                    fav_settings.setValue(host, data_uri)

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

    def get_video_script(self) -> str:
        """
        Extracts foundational Javascript processing payloads natively bundled into application assets reliably.
        """
        try:
            candidate_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "assets",
                "video_engine.js",
            )

            if not os.path.exists(candidate_path):
                candidate_path = os.path.join(
                    os.path.dirname(sys.executable),
                    "riemann",
                    "assets",
                    "video_engine.js",
                )

            if not os.path.exists(candidate_path):
                print(f"[Riemann Error] Video Engine not found at: {candidate_path}")
                self.show_toast("Error: Missing video_engine.js")
                return ""

            with open(candidate_path, "r", encoding="utf-8") as f:
                return f.read()

        except Exception as e:
            print(f"[ERROR] Failed to load video script: {e}")
            return ""

    def toggle_video_mode(self) -> None:
        """
        Coordinates client-side video injection dynamically.
        """
        is_active = self.btn_video_speed.isChecked()

        base_js = self.get_video_script()
        if not base_js:
            print("[ERROR] Aborting injection: Script content is empty.")
            self.btn_video_speed.setChecked(False)
            return

        if is_active:
            command = "if(window.RiemannVideo) window.RiemannVideo.enable();"
            self.show_toast("Video Mode ON")
        else:
            command = "if(window.RiemannVideo) window.RiemannVideo.disable();"
            self.show_toast("Video Mode OFF")

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
        app_settings = QSettings("Riemann", "PDFReader")
        start_dir = get_dialog_directory(app_settings)

        path, _ = QFileDialog.getSaveFileName(
            self, "Save File", os.path.join(start_dir, download_item.downloadFileName())
        )

        if not path:
            download_item.cancel()
            return

        save_last_directory(app_settings, path)
        download_item.setDownloadDirectory(os.path.dirname(path))
        download_item.setDownloadFileName(os.path.basename(path))
        download_item.accept()

        self.show_toast(f"Starting: {download_item.downloadFileName()}")

        def update_prog(received: int, total: int):
            total = download_item.totalBytes()
            received = download_item.receivedBytes()
            if total > 0:
                self.progress.setValue(int((received / total) * 100))

        download_item.downloadProgress.connect(update_prog)

        def on_state_changed(state):
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self.show_toast("Download complete!")
                self.progress.setValue(0)
                self._check_pdf_open(
                    state, download_item, download_item.downloadDirectory()
                )
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                self.show_toast("Download cancelled.")
                self.progress.setValue(0)
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
                self.show_toast("Download failed.")
                self.progress.setValue(0)

        download_item.stateChanged.connect(on_state_changed)
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

        dialog = YtDlpSettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dl_opts = dialog.get_options()

            app_settings = QSettings("Riemann", "PDFReader")
            start_dir = get_dialog_directory(app_settings)
            dest_dir = QFileDialog.getExistingDirectory(
                self, "Select Download Directory", start_dir
            )

            if not dest_dir:
                return

            save_last_directory(app_settings, dest_dir)
            self.show_toast("Starting download...")
            self.progress.setValue(0)

            self.btn_download.setIcon(self._get_icon("circle-stop.svg"))
            self.btn_download.setToolTip("Cancel Download")
            try:
                self.btn_download.clicked.disconnect()
            except RuntimeError:
                pass
            self.btn_download.clicked.connect(self.cancel_download)

            raw_title = self.web.title() or "Video_Download"
            safe_title = re.sub(r'[\\/*?:"<>|]', "", raw_title).replace(" ", "_")

            is_audio = dl_opts.get("audio_only")
            audio_fmt = dl_opts.get("audio_format", "best")

            if is_audio:
                ext = "mp3" if audio_fmt == "best" else audio_fmt
            else:
                ext = "mp4"

            filename = f"{safe_title}.{ext}"

            self.dl_worker = YtDlpWorker(url, dest_dir, dl_opts)
            self.mock_dl_item = MockDownloadItem(url, dest_dir, filename, self)
            self.mock_dl_item.worker = self.dl_worker

            try:
                if self.window() and hasattr(self.window(), "download_manager_dialog"):
                    self.window().download_manager_dialog.add_download(
                        self.mock_dl_item
                    )
            except Exception as e:
                print(f"[Warning] Could not link yt-dlp to Download Manager: {e}")

            self.dl_worker.progress.connect(self.progress.setValue)
            self.dl_worker.progress.connect(self.mock_dl_item.update_progress)

            self.dl_worker.finished.connect(
                lambda success, msg: [
                    self._on_download_finished(success, msg),
                    self.mock_dl_item.finish(success, msg),
                ]
            )

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

        icon = "volume-x.svg" if is_muted else "volume-on.svg"
        self.btn_mute.setIcon(self._get_icon(icon))

    def print_to_pdf(self) -> None:
        """Renders the current web page directly to a PDF and opens it in Riemann."""
        app_settings = QSettings("Riemann", "PDFReader")
        start_dir = get_dialog_directory(app_settings)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Webpage as PDF",
            os.path.join(
                start_dir,
                f"{self.web.title()}.pdf" if self.web.title() else "webpage.pdf",
            ),
            "PDF Files (*.pdf)",
        )

        if path:
            save_last_directory(app_settings, path)
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
        self.btn_download.setIcon(self._get_icon("download.svg"))
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
        """
        Intercepts state changes to ensure that the correct web component retains input focus.

        Args:
            event (QEvent): The state change event triggered by the system.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            focus_widget = QApplication.focusWidget()
            if not isinstance(focus_widget, QLineEdit):
                self.web.setFocus()

    def _get_icon(self, filename: str) -> QIcon:
        """
        Resolves and loads the appropriate themed icon based on the browser's current dark mode setting.

        Args:
            filename (str): The base filename of the SVG/PNG icon to fetch.

        Returns:
            QIcon: The resolved Qt icon object.
        """
        is_dark = getattr(self, "dark_mode", False)
        if (
            is_dark
            and filename.endswith(".svg")
            and not filename.endswith("-white.svg")
        ):
            filename = filename.replace(".svg", "-white.svg")

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_path = getattr(sys, "_MEIPASS")
            path = os.path.join(base_path, "riemann", "assets", "icons", filename)
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base_path, "assets", "icons", filename)

        if not os.path.exists(path) and "-white.svg" in filename:
            path = path.replace("-white.svg", ".svg")

        return QIcon(path)

    def _update_icons(self) -> None:
        """Refreshes all browser icons dynamically when the theme changes."""
        self.btn_back.setIcon(self._get_icon("chevron-left.svg"))
        self.btn_fwd.setIcon(self._get_icon("chevron-right.svg"))
        self.btn_reload.setIcon(self._get_icon("rotate-cw.svg"))

        if self.incognito:
            self.btn_incognito_icon.setIcon(self._get_icon("incognito.svg"))

        icon_name = "moon.svg" if getattr(self, "dark_mode", False) else "sun.svg"
        self.btn_theme_toggle.setIcon(self._get_icon(icon_name))

        is_bm = False
        if hasattr(self.window(), "bookmarks_manager"):
            is_bm = self.window().bookmarks_manager.is_bookmarked(
                self.web.url().toString()
            )
        self.btn_bookmark.setIcon(
            self._get_icon("bookmark-filled.svg" if is_bm else "bookmark.svg")
        )

        self.btn_captions.setIcon(
            self._get_icon(
                "captions.svg" if self.btn_captions.isChecked() else "captions-off.svg"
            )
        )

        self.btn_mute.setIcon(
            self._get_icon(
                "volume-x.svg" if self.btn_mute.isChecked() else "volume-on.svg"
            )
        )
        self.btn_music.setIcon(self._get_icon("music.svg"))
        self.btn_video_speed.setIcon(self._get_icon("gauge.svg"))

        self.btn_stream.setIcon(self._get_icon("airplay.svg"))
        dl_icon = (
            "circle-stop.svg"
            if getattr(self, "dl_worker", None) and self.dl_worker.isRunning()
            else "download.svg"
        )
        self.btn_download.setIcon(self._get_icon(dl_icon))

        self.btn_print_pdf.setIcon(self._get_icon("printer.svg"))

        if hasattr(self, "btn_find_prev"):
            self.btn_find_prev.setIcon(self._get_icon("chevron-up.svg"))
            self.btn_find_next.setIcon(self._get_icon("chevron-down.svg"))
            self.btn_close_find.setIcon(self._get_icon("x.svg"))

        if hasattr(self, "action_security"):
            url = self.web.url()
            scheme = url.scheme() if url else ""
            if scheme == "https":
                self.action_security.setIcon(self._get_icon("lock.svg"))
            elif scheme == "http":
                self.action_security.setIcon(self._get_icon("lock-open.svg"))
            else:
                self.action_security.setIcon(self._get_icon("file.svg"))

    def _on_link_hovered(self, url: str) -> None:
        """Shows target URL at the bottom left when hovering over links, matching native browser UX."""
        if url:
            self.link_tooltip.setText(url)
            self.link_tooltip.adjustSize()
            self.link_tooltip.move(10, self.height() - self.link_tooltip.height() - 10)
            self.link_tooltip.show()
            self.link_tooltip.raise_()
        else:
            self.link_tooltip.hide()

    def _on_audio_state_changed(self, audible: bool) -> None:
        if audible:
            self._audio_timer.stop()
            self._set_tab_playing_state("playing")
        else:
            self._audio_timer.start(1500)

    def _clear_audio_state(self) -> None:
        self._set_tab_playing_state(None)

    def _set_tab_playing_state(self, state: str | None) -> None:
        """Helper to propagate the playing state to the parent tab bar."""
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                idx = parent.indexOf(self)
                if idx != -1:
                    parent.tabBar().setTabData(idx, state)
                    parent.tabBar().update()
                break
            parent = parent.parent()

    def stream_video(self) -> None:
        """
        Presents a dropdown of available media players (VLC, MPV, Native).
        """
        raw_url = self.web.url().toString().strip()

        if raw_url.startswith("-") or not (
            raw_url.startswith("http://") or raw_url.startswith("https://")
        ):
            self.show_toast("Invalid or insecure URL for streaming.")
            return

        available_players = []

        if shutil.which("mpv"):
            available_players.append("MPV")
        if shutil.which("vlc"):
            available_players.append("VLC")

        available_players.append("Built-in Player")

        choice, ok = QInputDialog.getItem(
            self,
            "Select Media Player",
            "Choose a player to stream this video:",
            available_players,
            0,
            False,
        )

        if not ok:
            return

        self.selected_player = choice

        if choice == "MPV":
            self.show_toast("Opening in MPV...")
            try:
                subprocess.Popen(
                    [str(shutil.which("mpv")), "--keep-open=yes", raw_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                self.show_toast("Failed to start MPV.")
        else:
            self.show_toast(f"Extracting stream for {choice}...")
            self.stream_worker = YtDlpStreamWorker(raw_url)
            self.stream_worker.finished.connect(self._on_stream_extracted)
            self.stream_worker.error.connect(
                lambda e: self.show_toast(f"Stream error: {e}")
            )
            self.stream_worker.start()

    def _on_stream_extracted(self, direct_url: str) -> None:
        """Routes the extracted raw video stream to the selected player."""
        if self.selected_player == "VLC":
            try:
                subprocess.Popen(
                    [str(shutil.which("vlc")), direct_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                print(f"[ERROR] VLC failed: {e}")
                self.show_toast("Failed to start VLC. Falling back to native player.")
                self._start_playback(direct_url)
        else:
            self._start_playback(direct_url)

    def _start_playback(self, direct_url: str):
        self.video_window = QDialog(self)
        self.video_window.setWindowTitle("Riemann Media Player - Buffering...")
        self.video_window.resize(2000, 500)

        layout = QVBoxLayout(self.video_window)
        layout.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget()
        layout.addWidget(self.video_widget)

        progress_layout = QHBoxLayout()
        progress_layout.setContentsMargins(10, 0, 10, 0)

        self.time_label = QLabel("00:00 / 00:00")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)

        progress_layout.addWidget(self.position_slider)
        progress_layout.addWidget(self.time_label)
        layout.addLayout(progress_layout)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(10, 5, 10, 10)
        controls_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_rewind = QPushButton()
        self.btn_rewind.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward)
        )

        self.btn_play_pause = QPushButton()
        self.btn_play_pause.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        )

        self.btn_forward = QPushButton()
        self.btn_forward.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward)
        )

        for btn in [self.btn_rewind, self.btn_play_pause, self.btn_forward]:
            btn.setFixedSize(40, 40)
            controls_layout.addWidget(btn)

        layout.addLayout(controls_layout)

        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.btn_play_pause.clicked.connect(self._toggle_play_pause)
        self.btn_rewind.clicked.connect(
            lambda: self.player.setPosition(max(0, self.player.position() - 10000))
        )
        self.btn_forward.clicked.connect(
            lambda: self.player.setPosition(self.player.position() + 10000)
        )
        self.position_slider.sliderMoved.connect(self.player.setPosition)

        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.positionChanged.connect(self._update_slider)
        self.player.durationChanged.connect(self._update_duration)

        self.player.setSource(QUrl(direct_url))
        self.video_window.show()

    def _format_time(self, ms: int) -> str:
        seconds = ms // 1000
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _update_slider(self, position: int):
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)

        pos_str = self._format_time(position)
        dur_str = self._format_time(self.player.duration())
        self.time_label.setText(f"{pos_str} / {dur_str}")

    def _update_duration(self, duration: int):
        self.position_slider.setRange(0, duration)

    def _toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play_pause.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
            )
        else:
            self.btn_play_pause.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            )

    def _on_media_status_changed(self, status):
        """Prevents the player from skipping and sputtering by pausing until buffered."""
        if status == QMediaPlayer.MediaStatus.BufferingMedia:
            self.video_window.setWindowTitle("Riemann Media Player - Buffering...")
            self.player.pause()
        elif status in (
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.LoadedMedia,
        ):
            self.video_window.setWindowTitle("Riemann Media Player")
            self.player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.video_window.setWindowTitle("Riemann Media Player - Finished")
            self.player.stop()

    def _get_shifted_icon(self, icon_name: str, up_offset: int = 2) -> QIcon:
        """Offsets an icon upwards to fix QLineEdit action vertical alignment issues."""
        base_icon = self._get_icon(icon_name)
        pixmap = base_icon.pixmap(16, 16)

        shifted = QPixmap(16, 16)
        shifted.fill(Qt.GlobalColor.transparent)

        painter = QPainter(shifted)
        painter.drawPixmap(0, -up_offset, pixmap)
        painter.end()

        return QIcon(shifted)
