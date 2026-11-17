"""
Web Browser Component.

This module implements a full-featured web browser tab based on QWebEngineView.
It includes support for persistent profiles, ad-blocking, dark mode injection,
audio processing injection (Riemann Audio), and download management.

The start page is assets/homepage/homepage.html, a local file. It saves its
quick links by loading riemann-save URLs in a hidden frame. Those are only
honoured while the page showing them is that exact file, and the links must
parse as a list of {name, url} strings; they are stored re-serialised and
handed back to the page as JSON. Any web page (or an ad in a frame) could
otherwise store script that ran in the homepage, which as a local file can
read other local files.

Local HTML files keep LocalContentCanAccessFileUrls on, because without it a
saved page cannot even load its own stylesheet, but that setting also lets the
page fetch() or frame any file on disk. RequestInterceptor narrows it with
local_load_allowed: a local page may load scripts, styles, images, fonts and
media from its own folder and below, and nothing else from disk. The start page
may load anything under riemann/assets. Remote URLs are filtered the same
way rather than with LocalContentCanAccessRemoteUrls, which, when off, also
stopped the start page showing its site icons: a local page may navigate to a
remote page, the start page may load remote images (the icons), and nothing
else, so a local page cannot send what it reads to a server with fetch().
Each profile owns one RequestInterceptor, shared by its tabs: the profile only
keeps a weak pointer, so an interceptor owned by a tab vanished when that tab
closed and left every other tab on the profile unfiltered.
The ad blocker matches hosts by suffix (ads.example.org blocks
x.ads.example.org, not loads.example.org). interceptRequest runs on
Chromium's IO thread, so it only counts blocked requests; the count is added
to the stored total from the UI thread every BLOCK_COUNT_FLUSH_MS, since a
QSettings store must not be written from two threads.

Downloads: every tab on a profile is connected to its downloadRequested
signal, so each request is claimed once (a dynamic property on the request)
and answered by the tab whose page started it; a pop-up's download goes to the
first tab that sees it. Only PDFs are opened in a reader when they finish.

The caption engine runs in Chromium's ApplicationWorld, an isolated world
that shares the page's DOM but not its JavaScript globals, so the captions
server's port and per-run token are invisible to the site's own scripts.

Clipboard access goes through the site permissions in riemann.core.permissions
instead of JavascriptCanAccessClipboard, which granted it to every page.

Leaving a page with unsaved changes: a site that wants to warn first registers
a beforeunload handler, and Chromium then asks through javaScriptConfirm with
Qt's fixed LEAVE_PROMPT. WebPage shows that question as Leave or Stay, on the
tab it belongs to (brought to the front). Closing a tab, a window or the app
calls BrowserTab.confirm_leave first: it sends the page RequestClose, and
Chromium answers with windowCloseRequested (no handler, or Leave) or, through
WebPage, leave_refused (Stay). A page whose renderer never answers is let go
after LEAVE_WAIT_MS. Chromium asks only on pages the person has interacted
with, as in other browsers.

Notes:

_os_display_name: EXTENDED_NAME_FORMAT 3 = NameDisplay ("Ada Lovelace") Unix
only; importing it at module level broke Windows.

__init__: Let the tab shrink below the toolbar's natural width (e.g. in split
view); the rightmost buttons are clipped instead of forcing the pane wide.

eventFilter: The tab is closing: its view is gone but children still report
events.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from html import escape as html_escape
from typing import Any, Optional
from ..core.assets import asset_path, icon_path
from ..core.dependencies import venv_python
from .reader.utils import render_markdown_safely

_deno_dir = os.path.expanduser("~/.deno/bin")
if os.path.isdir(_deno_dir) and _deno_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_deno_dir}:{os.environ.get('PATH', '')}"

import shiboken6
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QCoreApplication,
    QEvent,
    QEventLoop,
    QIODevice,
    QObject,
    QSize,
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
    QWebEngineScript,
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
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import permissions
from ..core.captions_server import CaptionsServer
from .browser_handlers import WHATSAPP_HEADER_UA, WHATSAPP_HOST, ScriptInjector
from .palette import dialog_icon, incognito_url_style, link_tip_style, toast_style, tokens
from .shortcuts import share_keys
from .toast import Toast
from ..core.log import log
from ..core.doctypes import document_kind
from ..core.settings import (
    AUTO_OPEN_DOWNLOADS_KEY,
    DARK_MODE_KEY,
    HOMEPAGE_NAME_KEY,
    app_settings,
    browser_settings,
    favicon_cache,
    get_dialog_directory,
    save_last_directory,
    search_template,
    search_url,
)


HOMEPAGE_PATH = os.path.abspath(asset_path("homepage", "homepage.html"))


def is_local_homepage(url: QUrl) -> bool:
    """Whether url is Riemann's own start page file."""
    if not url.isLocalFile():
        return False
    return os.path.normcase(os.path.abspath(url.toLocalFile())) == os.path.normcase(HOMEPAGE_PATH)


ASSETS_DIR = os.path.dirname(os.path.dirname(HOMEPAGE_PATH))

_REMOTE_SCHEMES = {"http", "https", "ws", "wss", "ftp"}
_ICON_TYPES = {
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeFavicon,
}
_STATIC_TYPES = {
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeStylesheet,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeScript,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeFontResource,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeFavicon,
}


def _inside(path: str, folder: str) -> bool:
    path = os.path.normcase(os.path.realpath(path))
    folder = os.path.normcase(os.path.realpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def local_load_allowed(first_party: QUrl, request: QUrl, resource_type) -> bool:
    """Whether a page may load request. Only local pages and file: requests are restricted."""
    if resource_type == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame:
        return True
    if first_party.isLocalFile() and request.scheme().lower() in _REMOTE_SCHEMES:
        return is_local_homepage(first_party) and resource_type in _ICON_TYPES
    if not request.isLocalFile():
        return True
    if not first_party.isLocalFile():
        return False
    target = request.toLocalFile()
    if is_local_homepage(first_party):
        return _inside(target, ASSETS_DIR)
    if resource_type not in _STATIC_TYPES:
        return False
    return _inside(target, os.path.dirname(first_party.toLocalFile()))


def sanitize_homepage_links(payload: Any) -> Optional[str]:
    """The homepage's quick links as JSON, or None when payload is not a list of {name, url} strings."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else None
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    links = [
        {"name": item["name"][:200], "url": item["url"][:2048]}
        for item in data[:500]
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("url"), str)
    ]
    return json.dumps(links)


def js_runtimes() -> dict:
    """
    yt-dlp's js_runtimes option: the Deno that the pinned deno package put
    next to the optional-packages venv's Python, else Deno on PATH.
    """
    python = venv_python()
    if python:
        path = os.path.join(os.path.dirname(python), "deno" + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(path):
            return {"deno": {"path": path}}
    return {"deno": {}}


class YtDlpStreamWorker(QThread):
    """
    Finds a direct stream URL for a page's media with yt-dlp, without downloading it, so
    it can be handed to an external player such as VLC or mpv.
    """

    finished = Signal(str)
    error = Signal(str)

    def __init__(self, url, cookies_browser=None):
        super().__init__()
        self.url = url
        self.cookies_browser = cookies_browser

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
            "js_runtimes": js_runtimes(),
        }
        if self.cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (self.cookies_browser,)

        try:
            import yt_dlp

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if info and "url" in info:
                    self.finished.emit(info["url"])
                else:
                    self.error.emit("No URL found in response")
        except Exception as e:
            self.error.emit(str(e))


class YtDlpWorker(QThread):
    """
    Background worker thread for executing yt-dlp media downloads via the Python API.
    Emits rich dictionary metrics to supply detailed information to the YtDlpDownloadManager.
    """

    progress = Signal(int)
    progress_details = Signal(dict)
    finished = Signal(bool, str)

    def __init__(self, url: str, download_dir: str, dl_opts: Optional[dict] = None) -> None:
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.dl_opts = dl_opts if dl_opts is not None else {}
        self.is_cancelled = False
        self._last_emit_time = 0.0
        self._files: set = set()

    def run(self) -> None:
        """Runs the yt-dlp download with the options the user chose."""
        is_playlist = bool(self.dl_opts.get("playlist", False))
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self.progress_hook],
            "ignoreerrors": True,
            "js_runtimes": js_runtimes(),
        }

        if is_playlist:
            ydl_opts["noplaylist"] = False
            ydl_opts["outtmpl"] = os.path.join(
                self.download_dir, "%(playlist_index&{:02d} - |)s%(title)s.%(ext)s"
            )
            pl_start = self.dl_opts.get("playlist_start")
            pl_end = self.dl_opts.get("playlist_end")

            if pl_start and pl_start.isdigit():
                ydl_opts["playliststart"] = int(pl_start)
            if pl_end and pl_end.isdigit():
                ydl_opts["playlistend"] = int(pl_end)
        else:
            ydl_opts["noplaylist"] = True
            ydl_opts["outtmpl"] = os.path.join(self.download_dir, "%(title)s.%(ext)s")

        cookies_browser = self.dl_opts.get("cookies", "none")
        if cookies_browser != "none":
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        ydl_opts["format"] = self.dl_opts.get("format", "best")
        if self.dl_opts.get("format_sort"):
            ydl_opts["format_sort"] = self.dl_opts["format_sort"]

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
            import yt_dlp

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

            if self.is_cancelled:
                self.cleanup_remnants()
                self.finished.emit(False, "Download cancelled.")
            else:
                self.finished.emit(True, "Download complete!")

        except KeyboardInterrupt:
            self.cleanup_remnants()
            self.finished.emit(False, "Download cancelled.")

        except Exception as e:
            self.cleanup_remnants()
            if self.is_cancelled:
                self.finished.emit(False, "Download cancelled.")
            else:
                self.finished.emit(False, str(e))

    def progress_hook(self, d: dict) -> None:
        """
        Internal hook called by yt-dlp to report download progress.
        Also acts as our cancellation trigger by throwing an exception if the user aborts.
        """
        for key in ("filename", "tmpfilename"):
            if d.get(key):
                self._files.add(str(d[key]))
        if self.is_cancelled:
            raise KeyboardInterrupt("Download aborted by user.")

        current_time = time.time()
        is_finished = d.get("status") == "finished"

        if not is_finished and (current_time - self._last_emit_time < 0.1):
            return

        self._last_emit_time = current_time
        dl_bytes = d.get("downloaded_bytes", 0)
        tot_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0

        if tot_bytes > 0:
            pct_val = int((dl_bytes / tot_bytes) * 100)
        else:
            try:
                pct_val = int(
                    float(
                        (
                            re.sub(r"\x1b\[[0-9;]*m", "", d.get("_percent_str", "0%"))
                            .replace("%", "")
                            .strip()
                        )
                    )
                )
            except ValueError:
                pct_val = 0

        if is_finished:
            pct_val = 100

        info = d.get("info_dict", {})
        req_downloads = info.get("requested_downloads", [info])
        total_combined_bytes = sum(
            [
                req.get("filesize") or req.get("filesize_approx") or 0
                for req in req_downloads
            ]
        )

        data = {
            "status": d.get("status"),
            "downloaded_bytes": d.get("downloaded_bytes", 0),
            "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate") or 0,
            "total_combined_bytes": total_combined_bytes,
            "speed": d.get("speed", 0),
            "eta": d.get("eta", 0),
            "playlist_index": info.get("playlist_index"),
            "playlist_count": info.get("playlist_count"),
            "filename": d.get("filename", ""),
            "video_id": info.get("id", d.get("filename", "Unknown_ID")),
            "title": info.get("title", "Unknown Title"),
            "percentage": pct_val,
        }

        if d.get("status") in ["downloading", "finished"]:
            self.progress_details.emit(data)
            self.progress.emit(pct_val)

    def stop(self) -> None:
        """
        Flags the thread for cancellation. The progress_hook will catch this on the next tick.
        """
        self.is_cancelled = True

    def cleanup_remnants(self) -> None:
        """Deletes the .part and .ytdl files of this download; files of other downloads in the folder stay."""
        doomed = set()
        for name in self._files:
            doomed.update((name + ".part", name + ".ytdl"))
            if name.endswith((".part", ".ytdl")):
                doomed.add(name)
        for path in doomed:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                log.debug("cleanup_remnants failed", exc_info=True)


class YtDlpSettingsDialog(QDialog):
    def __init__(self, parent=None, is_playlist: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Download Settings")
        self.setFixedSize(360, 360)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)
        dialog_icon(self, "sliders-horizontal")

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
        self.chk_playlist.setChecked(is_playlist)
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
        self.btn_ok.setDefault(True)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_ok)
        layout.addLayout(btn_layout)

        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def get_options(self) -> dict:
        v_idx = self.combo_video.currentIndex()
        audio_only = v_idx == 7

        fmt = "bestaudio/best" if audio_only else "bestvideo*+bestaudio/best"
        format_sort = []

        if not audio_only:
            if v_idx == 1:
                format_sort = ["res:2160"]
            elif v_idx == 2:
                format_sort = ["res:1440"]
            elif v_idx == 3:
                format_sort = ["res:1080"]
            elif v_idx == 4:
                format_sort = ["res:720"]
            elif v_idx == 5:
                format_sort = ["res:480"]
            elif v_idx == 6:
                format_sort = ["res:144"]

        return {
            "format": fmt,
            "format_sort": format_sort,
            "audio_only": audio_only,
            "audio_format": self.combo_audio.currentText(),
            "subtitles": self.chk_subs.isChecked(),
            "playlist": self.chk_playlist.isChecked(),
            "playlist_start": self.txt_pl_start.text().strip(),
            "playlist_end": self.txt_pl_end.text().strip(),
            "cookies": self.combo_cookies.currentText().lower(),
        }


DOWNLOAD_CLAIMED = "riemannDownloadClaimed"
CAPTIONS_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
LEAVE_PROMPT = "Are you sure you want to leave this page? Changes that you made may not be saved."
LEAVE_WAIT_MS = 3000


def owning_tab(page: Optional[QObject]) -> Optional["BrowserTab"]:
    """The BrowserTab whose view shows page, or None for pop-ups and pages outside a tab."""
    obj = page.parent() if page is not None else None
    while obj is not None and not isinstance(obj, BrowserTab):
        obj = obj.parent()
    return obj


def opens_after_download(path: str, mime_type: str = "") -> bool:
    """PDFs, e-books and Office files open in a tab once downloaded; code, text and pictures don't."""
    if mime_type == "application/pdf" or path.lower().endswith(".pdf"):
        return True
    return document_kind(path) in ("pdf", "ebook", "office")


def show_tab(tab: Optional[QWidget]) -> None:
    """Makes tab the current page of the tab widget holding it."""
    stack = tab.parentWidget() if tab is not None else None
    if isinstance(stack, QStackedWidget) and isinstance(stack.parentWidget(), QTabWidget):
        stack.parentWidget().setCurrentWidget(tab)


def ask_to_leave(parent: Optional[QWidget], site: str) -> bool:
    """The Leave or Stay question for a page with unsaved changes; True for Leave."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Leave page?")
    box.setText(f"Changes you made on {site} may not be saved." if site else "Changes you made on this page may not be saved.")
    box.setInformativeText("Leave the page anyway?")
    leave = box.addButton("Leave", QMessageBox.ButtonRole.AcceptRole)
    stay = box.addButton("Stay", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(stay)
    box.setEscapeButton(stay)
    box.exec()
    return box.clickedButton() is leave


class WebPage(QWebEnginePage):
    """
    Custom QWebEnginePage subclass implementing specific behaviors for
    window creation, JavaScript console logging, and custom scheme handling.
    """

    leave_refused = Signal()
    asking_to_leave = False

    def __init__(
        self, profile: QWebEngineProfile, parent: Optional[QObject] = None
    ) -> None:
        super().__init__(profile, parent)
        self._popups = []
        self.store = app_settings()
        self.site_permissions: Optional[permissions.SitePermissions] = None

    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEnginePage:
        """
        Handles background tabs and pop-ups (like Google sign-in) with a temporary view
        that shares this page's profile.
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
        if popup in self._popups:
            self._popups.remove(popup)

    def javaScriptConfirm(self, origin: QUrl, msg: str) -> bool:
        if msg != QCoreApplication.translate("QWebEnginePage", LEAVE_PROMPT):
            return super().javaScriptConfirm(origin, msg)
        tab = owning_tab(self)
        show_tab(tab)
        self.asking_to_leave = True
        try:
            leave = ask_to_leave(tab or QWebEngineView.forPage(self), origin.host())
        finally:
            self.asking_to_leave = False
        if not leave:
            self.leave_refused.emit()
        return leave

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        """Sends the page's console messages to standard output."""
        pass

    def acceptNavigationRequest(
        self, url: QUrl, _type: QWebEnginePage.NavigationType, isMainFrame: bool
    ) -> bool:
        """
        Handles Riemann's own URL schemes, such as data sent from internal pages.
        Returns False when Riemann handled the URL itself.
        """
        if url.host() == "riemann-save.local" or url.scheme() == "riemann-save":
            if url.scheme() == "riemann-save":
                payload = urllib.parse.unquote(url.toString().replace("riemann-save://", "", 1))
            else:
                query = url.query(QUrl.ComponentFormattingOption.FullyEncoded)
                payload = urllib.parse.unquote(query[5:]) if query.startswith("data=") else ""
            links = sanitize_homepage_links(payload) if is_local_homepage(self.url()) else None
            if links is not None:
                self.store.setValue("homepage_links", links)
            return False

        if isMainFrame and self.site_permissions is not None:
            apply_content_settings(self.settings(), self.site_permissions, url)
        return super().acceptNavigationRequest(url, _type, isMainFrame)


def apply_content_settings(settings: QWebEngineSettings, store, url: QUrl) -> None:
    """Sets the page attributes behind the content settings for url's site."""
    A = QWebEngineSettings.WebAttribute
    allowed = lambda key: store.get(url, key) == permissions.ALLOW
    settings.setAttribute(A.JavascriptCanOpenWindows, allowed("popups"))
    settings.setAttribute(A.PlaybackRequiresUserGesture, not allowed("autoplay"))
    settings.setAttribute(A.JavascriptEnabled, allowed("javascript"))
    settings.setAttribute(A.AutoLoadImages, allowed("images"))
    settings.setAttribute(A.AllowRunningInsecureContent, allowed("insecure_content"))


class PermissionBar(QFrame):
    """
    The "example.com wants to use your camera" bar shown under the address bar.
    Requests queue and are shown one at a time; "Remember" stores the answer
    for the site.
    """

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("permissionBar")
        self._queue: list = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.remember = QCheckBox("Remember for this site")
        self.remember.setChecked(True)
        self.btn_block = QPushButton("Block")
        self.btn_allow = QPushButton("Allow")
        self.btn_allow.setDefault(True)
        self.btn_manage = QToolButton()
        self.btn_manage.setText("Manage")
        self.btn_manage.setAutoRaise(True)
        self.btn_manage.setToolTip("Site permissions in Settings")
        self.btn_manage.clicked.connect(self._open_settings)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.btn_manage)
        layout.addWidget(self.remember)
        layout.addWidget(self.btn_block)
        layout.addWidget(self.btn_allow)
        self.btn_allow.clicked.connect(lambda: self._answer(True))
        self.btn_block.clicked.connect(lambda: self._answer(False))
        self.hide()

    def ask(self, permission, keys: list[str]) -> None:
        self._queue.append((permission, keys))
        if len(self._queue) == 1:
            self._show_current()

    def _show_current(self) -> None:
        while self._queue and not self._queue[0][0].isValid():
            self._queue.pop(0)
        if not self._queue:
            self.hide()
            return
        permission, keys = self._queue[0]
        site = permissions.site_of(permission.origin()) or permission.origin().toString()
        wants = " and ".join(permissions.KIND_BY_KEY[k].request for k in keys)
        self.label.setText(f"<b>{html_escape(site)}</b> wants to {html_escape(wants)}.")
        self.remember.setVisible(bool(permissions.site_of(permission.origin())))
        self.show()

    def _answer(self, allow: bool) -> None:
        if not self._queue:
            self.hide()
            return
        permission, keys = self._queue.pop(0)
        if permission.isValid():
            permission.grant() if allow else permission.deny()
        if self.remember.isChecked():
            for key in keys:
                self.store.set(permission.origin(), key, permissions.ALLOW if allow else permissions.BLOCK)
        self._show_current()

    def _open_settings(self) -> None:
        show = getattr(self.window(), "show_site_permissions", None)
        if show:
            show()

    def clear(self) -> None:
        for permission, _ in self._queue:
            if permission.isValid():
                permission.deny()
        self._queue.clear()
        self.hide()


BLOCKED_DOMAINS = (
    "doubleclick.net",
    "googleadservices.com",
    "googlesyndication.com",
    "adservice.google.com",
    "pagead2.googlesyndication.com",
    "tpc.googlesyndication.com",
    "youtube.com/ptracking",
    "youtube.com/pagead",
    "google-analytics.com",
    "dmxleo.com",
)


ADS_BLOCKED_KEY = "browser/ads_blocked"
BLOCK_COUNT_FLUSH_MS = 5000


def host_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def is_blocked(url: QUrl, rules) -> bool:
    """True when url's host is a listed domain or below it; a rule with a path ("youtube.com/pagead") also needs that path prefix."""
    host, path = url.host(), url.path()
    for rule in rules:
        domain, _, prefix = rule.partition("/")
        if host_matches(host, domain) and (not prefix or path.startswith("/" + prefix)):
            return True
    return False


class RequestInterceptor(QWebEngineUrlRequestInterceptor):
    """
    Handles AdBlocking by intercepting network requests to known advertising
    and tracking domains, User Agent spoofing for WhatsApp,
    and surgical Header injection for Monkeytype/Firebase auth.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.blocked_domains = list(BLOCKED_DOMAINS)
        self.spoofed_ua = WHATSAPP_HEADER_UA.encode()
        self.blocked_count = 0
        self._flushed_count = 0
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(BLOCK_COUNT_FLUSH_MS)
        self._flush_timer.timeout.connect(self.flush_block_count)
        self._flush_timer.start()
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.flush_block_count)

    def flush_block_count(self) -> None:
        """Adds the requests blocked since the last flush to the stored total; runs on the UI thread."""
        count = self.blocked_count
        delta = count - self._flushed_count
        if delta <= 0:
            return
        self._flushed_count = count
        settings = app_settings()
        settings.setValue(ADS_BLOCKED_KEY, settings.value(ADS_BLOCKED_KEY, 0, type=int) + delta)

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        """Blocks requests to listed domains and adjusts headers where a site needs it."""
        if (
            info.resourceType()
            == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeServiceWorker
        ):
            info.block(True)
            return

        if not local_load_allowed(info.firstPartyUrl(), info.requestUrl(), info.resourceType()):
            info.block(True)
            return

        url = info.requestUrl()
        if is_blocked(url, self.blocked_domains):
            info.block(True)
            self.blocked_count += 1
            return

        if host_matches(url.host(), WHATSAPP_HOST):
            info.setHttpHeader(b"User-Agent", self.spoofed_ua)


def _os_display_name() -> str:
    """The user's full name from the OS account, else their login name."""
    try:
        if sys.platform == "win32":
            import ctypes

            size = ctypes.c_ulong(256)
            buf = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)) and buf.value:
                return buf.value
        else:
            import pwd

            full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
            if full:
                return full
    except Exception:
        log.debug("_os_display_name failed", exc_info=True)
    try:
        return os.getlogin()
    except OSError:
        import getpass

        return getpass.getuser()


class BrowserTab(QWidget):
    def __init__(
        self,
        start_url: str = "",
        parent: Optional[QWidget] = None,
        profile: Optional[QWebEngineProfile] = None,
        dark_mode: bool = False,
        incognito: bool = False,
    ) -> None:
        """With incognito, the tab uses an in-memory profile."""
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

        self.request_interceptor = self.profile.findChild(RequestInterceptor)
        if self.request_interceptor is None:
            self.request_interceptor = RequestInterceptor(self.profile)
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
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        tb_layout = QHBoxLayout(self.toolbar)
        tb_layout.setContentsMargins(5, 0, 5, 0)

        self.btn_back = QPushButton()
        self.btn_back.setIcon(
            QIcon(
                icon_path("chevron-left.svg")
            )
        )
        self.btn_back.setIconSize(icon_size)
        self.btn_back.setFixedWidth(30)

        self.btn_fwd = QPushButton()
        self.btn_fwd.setIcon(
            QIcon(
                icon_path("chevron-right.svg")
            )
        )
        self.btn_fwd.setIconSize(icon_size)
        self.btn_fwd.setFixedWidth(30)

        self.btn_reload = QPushButton()
        self.btn_reload.setIcon(
            QIcon(
                icon_path("rotate-cw.svg")
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
            QIcon(icon_path("file.svg")),
            QLineEdit.ActionPosition.LeadingPosition,
        )

        if self.incognito:
            self.btn_incognito_icon = QPushButton()
            self.btn_incognito_icon.setIcon(
                QIcon(
                    icon_path("incognito.svg")
                )
            )
            self.btn_incognito_icon.setIconSize(icon_size)
            self.btn_incognito_icon.setFlat(True)
            self.btn_incognito_icon.setFixedWidth(30)
            self.btn_incognito_icon.setToolTip(
                "Incognito Mode: History will not be saved."
            )
            self.txt_url.setStyleSheet(incognito_url_style())

        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setIcon(
            QIcon(
                icon_path("sun.svg" if getattr(self, "dark_mode", False) else "moon.svg")
            )
        )
        self.btn_theme_toggle.setIconSize(icon_size)
        self.btn_theme_toggle.setFixedWidth(30)
        self.btn_theme_toggle.setToolTip("Toggle Browser Dark Mode")
        self.btn_theme_toggle.clicked.connect(self.toggle_theme)

        self.btn_bookmark = QPushButton()
        self.btn_bookmark.setIcon(
            QIcon(
                icon_path("bookmark.svg")
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
                icon_path("captions-off.svg")
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
                icon_path("volume-on.svg")
            )
        )
        self.btn_mute.setIconSize(icon_size)
        self.btn_mute.setFixedWidth(30)
        self.btn_mute.setCheckable(True)
        self.btn_mute.setToolTip("Mute/Unmute Tab")
        self.btn_mute.clicked.connect(self.toggle_mute)

        self.btn_music = QPushButton()
        self.btn_music.setIcon(
            QIcon(icon_path("music.svg"))
        )
        self.btn_music.setIconSize(icon_size)
        self.btn_music.setFixedWidth(30)
        self.btn_music.setCheckable(True)
        self.btn_music.setToolTip("Toggle Audiophile Music Mode")
        self.btn_music.clicked.connect(self.toggle_music_mode)

        self.btn_video_speed = QPushButton()
        self.btn_video_speed.setIcon(
            QIcon(icon_path("gauge.svg"))
        )
        self.btn_video_speed.setIconSize(icon_size)
        self.btn_video_speed.setFixedWidth(30)
        self.btn_video_speed.setCheckable(True)
        self.btn_video_speed.setToolTip("Toggle Video Speed Controller")
        self.btn_video_speed.clicked.connect(self.toggle_video_mode)

        self.btn_stream = QPushButton()
        self.btn_stream.setIcon(
            QIcon(
                icon_path("airplay.svg")
            )
        )
        self.btn_stream.setIconSize(icon_size)
        self.btn_stream.setFixedWidth(30)
        self.btn_stream.setToolTip("Stream Video Externally")
        self.btn_stream.clicked.connect(self.stream_video)

        self.btn_download = QPushButton()
        self.btn_download.setIcon(
            QIcon(
                icon_path("download.svg")
            )
        )
        self.btn_download.setIconSize(icon_size)
        self.btn_download.setFixedWidth(30)
        self.btn_download.setToolTip("Download Video via yt-dlp")
        self.btn_download.clicked.connect(self.download_video)

        self.btn_print_pdf = QPushButton()
        self.btn_print_pdf.setIcon(
            QIcon(
                icon_path("printer.svg")
            )
        )
        self.btn_print_pdf.setIconSize(icon_size)
        self.btn_print_pdf.setFixedWidth(30)
        self.btn_print_pdf.setToolTip("Save Webpage to PDF")
        self.btn_print_pdf.clicked.connect(self.print_to_pdf)

        self.btn_fullscreen = QPushButton()
        self.btn_fullscreen.setIcon(self._get_icon("maximize.svg"))
        self.btn_fullscreen.setIconSize(icon_size)
        self.btn_fullscreen.setFixedWidth(30)
        self.btn_fullscreen.setToolTip("Toggle Fullscreen/Reading Mode")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen_mode)

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
        tb_layout.addWidget(self.btn_fullscreen)
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
                background-color: %s;
                border-radius: 2px;
            }
        """ % tokens(self.dark_mode)["accent"])
        layout.insertWidget(0, self.progress)

        self.lbl_toast = Toast(self)
        self.lbl_toast.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self.incognito:
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
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )

        self.site_permissions = (
            permissions.SitePermissions(persist=False, parent=self)
            if self.incognito
            else permissions.shared()
        )
        page.site_permissions = self.site_permissions
        self.profile.setPersistentPermissionsPolicy(
            QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime
        )
        self.permission_bar = PermissionBar(self.site_permissions, self)
        layout.addWidget(self.permission_bar)
        page.permissionRequested.connect(self._on_permission_requested)
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

        self.shortcut_text_zoom_in = QShortcut(QKeySequence("Ctrl+Shift+="), self)
        self.shortcut_text_zoom_in.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_text_zoom_in.activated.connect(lambda: self.modify_text_zoom(10))

        self.shortcut_text_zoom_in_alt = QShortcut(QKeySequence("Ctrl+Shift++"), self)
        self.shortcut_text_zoom_in_alt.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_text_zoom_in_alt.activated.connect(
            lambda: self.modify_text_zoom(10)
        )

        self.shortcut_text_zoom_out = QShortcut(QKeySequence("Ctrl+Shift+-"), self)
        self.shortcut_text_zoom_out.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_text_zoom_out.activated.connect(
            lambda: self.modify_text_zoom(-10)
        )

        self.shortcut_text_zoom_out_alt = QShortcut(QKeySequence("Ctrl+Shift+_"), self)
        self.shortcut_text_zoom_out_alt.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut_text_zoom_out_alt.activated.connect(
            lambda: self.modify_text_zoom(-10)
        )

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
        share_keys(self)

        self.apply_theme()
        self.web.installEventFilter(self)

        for child in self.web.children():
            child.installEventFilter(self)

        if self.incognito:
            self.txt_url.setStyleSheet(incognito_url_style())

        if self.window() and hasattr(self.window(), "history_model"):
            self.completer.setModel(self.window().history_model)

        if not start_url:
            homepage_path = HOMEPAGE_PATH

            if os.path.exists(homepage_path):
                home_url = QUrl.fromLocalFile(homepage_path)
                home_url.setQuery("theme=" + ("dark" if self.dark_mode else "light"))
                self.web.load(home_url)
            else:
                engine = urllib.parse.urlsplit(search_template())
                self.web.load(QUrl(f"{engine.scheme}://{engine.netloc}"))
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
        self.link_tooltip.setStyleSheet(link_tip_style(tokens(self.dark_mode)))
        self.link_tooltip.hide()
        self.web.page().linkHovered.connect(self._on_link_hovered)
        self.web.page().recentlyAudibleChanged.connect(self._on_audio_state_changed)

    def focusInEvent(self, event: Any) -> None:
        """
        Forwards focus to the web view so page shortcuts work. The hand-off
        waits for the next event-loop pass: doing it inside the focus event
        (itself often inside a window activation) occasionally crashed
        QtWebEngine at startup.
        """
        super().focusInEvent(event)
        QTimer.singleShot(0, self, self._focus_web_view)

    def _focus_web_view(self) -> None:
        web = getattr(self, "web", None)
        if web is not None and shiboken6.isValid(web) and web.isVisible() and self.hasFocus():
            web.setFocus()

    def _on_homepage_load_finished(self, ok: bool) -> None:
        """Sends system data to the local start page once it has loaded."""
        if ok and is_local_homepage(self.web.url()):
            settings = app_settings()
            name = settings.value(HOMEPAGE_NAME_KEY, "", type=str)

            if not name:
                name = _os_display_name()

            links = sanitize_homepage_links(settings.value("homepage_links", "")) or "null"
            fav_settings = favicon_cache()
            cached_icons = {}
            for key in fav_settings.allKeys():
                cached_icons[key] = fav_settings.value(key, "", type=str)
            icons_json = json.dumps(cached_icons)

            theme = "dark" if self.dark_mode else "light"
            js_code = (
                f"window.setTheme && window.setTheme('{theme}'); "
                f"window.cachedIcons = {icons_json}; window.searchTemplate = {json.dumps(search_template())}; "
                f"window.initHomepage({json.dumps(name)}, {links});"
            )
            self.web.page().runJavaScript(js_code)

    def _on_permission_requested(self, permission) -> None:
        """Answers a page's permission request from the site settings, or asks."""
        keys = permissions.kinds_for_permission(permission.permissionType())
        verdict = permissions.decide(self.site_permissions, permission.origin(), keys)
        if verdict == permissions.ALLOW:
            permission.grant()
        elif verdict == permissions.BLOCK:
            permission.deny()
        else:
            self.permission_bar.ask(permission, keys)

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
        """Web content asking for full screen toggles the window's full screen."""
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
        t = tokens(self.dark_mode)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, False)
        self.web.page().setBackgroundColor(QColor(t["canvas"] if self.dark_mode else t["surface"]))

        if is_local_homepage(self.web.url()):
            theme = "dark" if self.dark_mode else "light"
            self.web.page().runJavaScript(f"window.setTheme && window.setTheme('{theme}');")
        self.script_injector.inject_smart_dark_mode(self.web.page(), self.dark_mode)
        style = (
            f"QWidget {{ background: {t['canvas']}; color: {t['text']}; }} "
            f"QLineEdit {{ background: {t['surface']}; border: 1px solid {t['border']}; "
            f"border-radius: 8px; padding: 5px 10px; color: {t['text']}; "
            f"selection-background-color: {t['accent']}; selection-color: {t['accent_text']}; }} "
            f"QLineEdit:focus {{ border-color: {t['accent']}; }}"
        )
        self.toolbar.setStyleSheet(style)
        self.search_bar.setStyleSheet(style)
        self.lbl_toast.setStyleSheet(toast_style(t) + " font-weight: bold;")
        if hasattr(self, "link_tooltip"):
            self.link_tooltip.setStyleSheet(link_tip_style(t))

        if self.incognito:
            self.txt_url.setStyleSheet(incognito_url_style())

        menu_bg, menu_fg, menu_border = t["surface"], t["text"], t["border"]
        if hasattr(self, "btn_zoom") and self.btn_zoom.menu():
            self.btn_zoom.menu().setStyleSheet(
                f"QMenu {{ background: {menu_bg}; \
                           color: {menu_fg}; \
                           border: 1px solid {menu_border}; }} \
                QMenu::item:selected {{ background: {t['raised']}; }}"
            )

        if hasattr(self, "btn_back"):
            self._update_icons()

    def toggle_theme(self) -> None:
        """
        Reverses the active display theme state, applying updates to the local rendering pipeline.
        """
        self.dark_mode = not self.dark_mode
        if hasattr(self.window(), "settings"):
            self.window().settings.setValue(DARK_MODE_KEY, self.dark_mode)
        if hasattr(self.window(), "dark_mode"):
            self.window().dark_mode = self.dark_mode

        self.apply_theme()

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """Lets Riemann's own shortcuts through before the web view takes the key."""
        if not shiboken6.isValid(self.web):
            return False

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
        new_factor = max(0.1, min(self.web.zoomFactor() + delta, 5.0))
        self.web.setZoomFactor(new_factor)
        self.btn_zoom.setText(f"{int(new_factor * 100)}%")
        domain = self.web.url().host()

        if domain:
            browser_settings().setValue(
                f"zoom/{domain}", new_factor
            )

    def modify_text_zoom(self, delta: float) -> None:
        """
        Adjusts only the text font size on the web page, without modifying layout scale.
        """
        js_code = f"""
        (function() {{
            let currentSize = parseFloat(document.body.style.fontSize) || 100;
            currentSize += {delta};
            document.body.style.fontSize = currentSize + '%';
        }})();
        """
        self.web.page().runJavaScript(js_code)

    def reset_zoom(self) -> None:
        self.web.setZoomFactor(1.0)
        self.btn_zoom.setText("100%")
        domain = self.web.url().host()

        if domain:
            browser_settings().setValue(f"zoom/{domain}", 1.0)

    def toggle_search(self) -> None:
        """
        Alternates the user-facing visibility properties for the page search utility bar.
        """
        self.search_bar.setVisible(not self.search_bar.isVisible())
        if self.search_bar.isVisible():
            self.txt_find.setFocus()

    def find_next(self) -> None:
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
            else search_url(text)
        )
        if not url.scheme():
            url.setScheme("https")
        self.web.load(url)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.lbl_toast.isVisible():
            self.lbl_toast.reposition()

    def show_toast(self, message: str) -> None:
        self.lbl_toast.show_message(message, self.lbl_toast.styleSheet())

    def _update_url_bar(self, url: QUrl) -> None:
        s_url = url.toString()

        domain = url.host()
        if domain:
            saved_zoom = float(
                browser_settings().value(f"zoom/{domain}", 1.0)
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

        if is_local_homepage(url):
            self.txt_url.setText("")
            self.txt_url.setPlaceholderText("Search the web or enter URL...")
        else:
            self.txt_url.setText(s_url)
            self.txt_url.setPlaceholderText("Enter URL or Search...")

        self.txt_url.setCursorPosition(0)

        if (
            not self.incognito
            and not is_local_homepage(url)
            and self.window()
            and hasattr(self.window(), "add_to_history")
        ):
            self.window().add_to_history(s_url, "web")

        self._update_bookmark_icon(s_url)

    def _update_bookmark_icon(self, url: str) -> None:
        """Shows on the star button whether url is bookmarked."""
        if self.window() and hasattr(self.window(), "bookmarks_manager"):
            is_bm = self.window().bookmarks_manager.is_bookmarked(url)
            self.btn_bookmark.setChecked(is_bm)
            self.btn_bookmark.setIcon(
                self._get_icon("bookmark-filled.svg" if is_bm else "bookmark.svg")
            )

    def toggle_bookmark(self) -> None:
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
            token = getattr(self.captions_server, "token", "") if self.captions_server else ""
            self.web.page().runJavaScript(f"""
                window.RIEMANN_CAPTIONS_PORT = {port};
                window.RIEMANN_CAPTIONS_TOKEN = {json.dumps(token)};
                {js_payload}
                if (window.RiemannCaptions) window.RiemannCaptions.enable();
            """, CAPTIONS_WORLD)
            self.show_toast("Live Captions Enabled")
        else:
            self.web.page().runJavaScript(
                "if (window.RiemannCaptions) window.RiemannCaptions.disable();", CAPTIONS_WORLD
            )
            self.show_toast("Live Captions Disabled")

    def get_captions_script(self) -> str:
        try:
            candidate_path = asset_path("caption_engine.js")
            with open(candidate_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[ERROR] Failed to load captions script: {e}")
            return ""

    def _update_tab_title(self, title: str) -> None:
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                idx = parent.indexOf(self)
                if idx != -1:
                    parent.setTabText(idx, title)
                break
            parent = parent.parent()

    def _update_tab_icon(self, icon) -> None:
        """Shows the site's favicon on the tab."""

        parent = self.parent()
        page_icon = QIcon(icon_path("browser.png"))
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

                    fav_settings = favicon_cache()
                    fav_settings.setValue(host, data_uri)

    def get_audio_script(self) -> str:
        try:
            candidate_path = asset_path("audio_engine.js")

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
        try:
            candidate_path = asset_path("video_engine.js")

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
        if self.btn_music.isChecked():
            QTimer.singleShot(1000, self, self.toggle_music_mode)

    def _handle_download(self, download_item: QWebEngineDownloadRequest) -> None:
        """Asks where to save a download started on this profile; only one tab answers each request."""
        if download_item.property(DOWNLOAD_CLAIMED):
            return
        owner = owning_tab(download_item.page())
        if owner is not None and owner is not self:
            return
        download_item.setProperty(DOWNLOAD_CLAIMED, True)
        store = app_settings()
        start_dir = get_dialog_directory(store)

        path, _ = QFileDialog.getSaveFileName(
            self, "Save File", os.path.join(start_dir, download_item.downloadFileName())
        )

        if not path:
            download_item.cancel()
            return

        save_last_directory(store, path)
        download_item.setDownloadDirectory(os.path.dirname(path))
        download_item.setDownloadFileName(os.path.basename(path))
        download_item.accept()

        self.show_toast(f"Starting: {download_item.downloadFileName()}")

        def update_prog():
            total = download_item.totalBytes()
            if total > 0:
                self.progress.setValue(int((download_item.receivedBytes() / total) * 100))

        download_item.receivedBytesChanged.connect(update_prog)
        download_item.totalBytesChanged.connect(update_prog)

        def on_state_changed(state):
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self.show_toast("Download complete!")
                self.progress.setValue(0)
                self._check_download_open(
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

    def _check_download_open(
        self, state: int, item: QWebEngineDownloadRequest, temp_folder: str
    ) -> None:
        if state != QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            return
        if not app_settings().value(AUTO_OPEN_DOWNLOADS_KEY, True, type=bool):
            return
        full_path = os.path.join(temp_folder, item.downloadFileName())
        if opens_after_download(full_path, item.mimeType()):
            self._on_document_downloaded(full_path)

    def _on_document_downloaded(self, path: str) -> None:
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

        is_pl = "list=" in url.lower() or "/playlist" in url.lower()
        dialog = YtDlpSettingsDialog(self, is_playlist=is_pl)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dl_opts = dialog.get_options()

            store = app_settings()
            start_dir = get_dialog_directory(store)
            dest_dir = QFileDialog.getExistingDirectory(
                self, "Select Download Directory", start_dir
            )

            if not dest_dir:
                return

            save_last_directory(store, dest_dir)
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
            self.dl_worker = YtDlpWorker(url, dest_dir, dl_opts)

            try:
                if self.window() and hasattr(self.window(), "ytdlp_manager_dialog"):
                    self.window().ytdlp_manager_dialog.add_download(
                        self.dl_worker,
                        raw_title,
                        dest_dir,
                        dl_opts.get("playlist", False),
                    )
            except Exception as e:
                print(f"[Warning] Could not link to yt-dlp Download Manager: {e}")

            self.dl_worker.progress.connect(self.progress.setValue)
            self.dl_worker.finished.connect(self._on_download_finished)
            self.dl_worker.start()

    def cancel_download(self) -> None:
        if hasattr(self, "dl_worker") and self.dl_worker.isRunning():
            self.show_toast("Cancelling download...")
            self.dl_worker.stop()
            self.progress.setValue(0)

    def toggle_mute(self) -> None:
        """Mutes or unmutes the audio output specifically for this web tab."""
        is_muted = self.btn_mute.isChecked()
        self.web.page().setAudioMuted(is_muted)

        icon = "volume-x.svg" if is_muted else "volume-on.svg"
        self.btn_mute.setIcon(self._get_icon(icon))

    def print_to_pdf(self) -> None:
        """Renders the current web page directly to a PDF and opens it in Riemann."""
        store = app_settings()
        start_dir = get_dialog_directory(store)

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
            save_last_directory(store, path)
            self.show_toast("Rendering PDF...")

            def handle_pdf_print(file_path, success):
                self.web.page().pdfPrintingFinished.disconnect(handle_pdf_print)
                if success:
                    self.show_toast("PDF saved successfully!")
                    if self.window() and hasattr(self.window(), "new_pdf_tab"):
                        self.window().new_pdf_tab(file_path)
                else:
                    self.show_toast("Failed to render PDF.")

            self.web.page().pdfPrintingFinished.connect(handle_pdf_print)
            self.web.page().printToPdf(path)

    def _on_download_finished(self, success: bool, message: str) -> None:
        self.btn_download.setIcon(self._get_icon("download.svg"))
        self.btn_download.setToolTip("Download Video via yt-dlp")
        try:
            self.btn_download.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_download.clicked.connect(self.download_video)

        if success:
            self.progress.setValue(100)
            QTimer.singleShot(2000, self.progress, lambda: self.progress.setValue(0))
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
            self.web.page().runJavaScript(
                "document.querySelectorAll('audio, video').forEach(media => media.pause());"
            )
        super().deleteLater()

    def confirm_leave(self) -> bool:
        """Lets the page ask before it is closed, if it has unsaved changes; False when the person chose Stay."""
        page = self.web.page() if shiboken6.isValid(self.web) else None
        if not isinstance(page, WebPage):
            return True
        loop = QEventLoop()
        answer: list = []

        def settle(leave: bool) -> None:
            if not answer:
                answer.append(leave)
                loop.quit()

        def closed() -> None:
            settle(True)

        def refused() -> None:
            settle(False)

        def no_reply() -> None:
            if not page.asking_to_leave:
                settle(True)

        page.windowCloseRequested.connect(closed)
        page.leave_refused.connect(refused)
        QTimer.singleShot(LEAVE_WAIT_MS, loop, no_reply)
        page.triggerAction(QWebEnginePage.WebAction.RequestClose)
        if not answer:
            loop.exec()
        page.windowCloseRequested.disconnect(closed)
        page.leave_refused.disconnect(refused)
        return answer[0]

    def changeEvent(self, event: QEvent) -> None:
        """On activation, gives focus back to the web view."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            focus_widget = QApplication.focusWidget()
            if not isinstance(focus_widget, QLineEdit):
                self.web.setFocus()

    def _get_icon(self, filename: str) -> QIcon:
        """The icon for filename in the current theme (the white variant in dark mode)."""
        is_dark = getattr(self, "dark_mode", False)
        return QIcon(icon_path(filename, is_dark))

    def _update_icons(self) -> None:
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

        icons = {0: "panel-top.svg", 1: "maximize.svg", 2: "minimize.svg"}
        self.btn_fullscreen.setIcon(
            self._get_icon(
                icons.get(
                    getattr(self.window(), "_fullscreen_state", 0)
                    if self.window()
                    else 0,
                    "maximize.svg",
                )
            )
        )

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
        Presents a dropdown of available media players (VLC, MPV, Native)
        and securely pipes cookies to bypass authentication blocks.
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

        auth_methods = [
            "No Cookies (Public Video)",
            "Use Netscape cookies.txt file (Recommended Backup)",
            "Auto-extract: Chrome",
            "Auto-extract: Firefox",
            "Auto-extract: Brave",
            "Auto-extract: Edge",
        ]

        auth_choice, auth_ok = QInputDialog.getItem(
            self, "Authentication", "Select cookie source:", auth_methods, 0, False
        )
        if not auth_ok:
            return

        cookies_browser = None
        cookie_file = None

        if "Netscape" in auth_choice:
            store = app_settings()
            start_dir = get_dialog_directory(store)

            path, _ = QFileDialog.getOpenFileName(
                self, "Select Netscape Cookies File", start_dir, "Text Files (*.txt)"
            )
            if path:
                save_last_directory(store, path)
                cookie_file = path
            else:
                self.show_toast("Streaming cancelled: No cookie file provided.")
                return
        elif "Auto-extract" in auth_choice:
            cookies_browser = auth_choice.split(": ")[1].lower()

        if choice == "MPV":
            self.show_toast("Opening in MPV...")
            mpv_args = [str(shutil.which("mpv")), "--keep-open=yes"]

            if cookie_file:
                mpv_args.append(f"--cookies-file={cookie_file}")
            elif cookies_browser:
                mpv_args.append(
                    f"--ytdl-raw-options=cookies-from-browser={cookies_browser}"
                )

            mpv_args.append(raw_url)

            try:
                subprocess.Popen(
                    mpv_args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
            except Exception:
                self.show_toast("Failed to start MPV.")
        else:
            self.show_toast(f"Extracting stream for {choice}...")
            self.stream_worker = YtDlpStreamWorker(raw_url)

            if cookie_file:

                def custom_run():
                    ydl_opts = {
                        "format": "best[ext=mp4]",
                        "quiet": True,
                        "noplaylist": True,
                        "cookiefile": cookie_file,
                    }
                    try:
                        import yt_dlp

                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(
                                self.stream_worker.url, download=False
                            )
                            if info and "url" in info:
                                self.stream_worker.finished.emit(info["url"])
                            else:
                                self.stream_worker.error.emit(
                                    "No URL found in response"
                                )
                    except Exception as e:
                        self.stream_worker.error.emit(str(e))

                self.stream_worker.run = custom_run
            elif cookies_browser:
                self.stream_worker.cookies_browser = cookies_browser

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

    def toggle_fullscreen_mode(self) -> None:
        """Triggers the global window fullscreen state."""
        if self.window() and hasattr(self.window(), "toggle_reader_fullscreen"):
            self.window().toggle_reader_fullscreen()

    def update_fullscreen_icon(self, state: int) -> None:
        icons = {0: "panel-top.svg", 1: "maximize.svg", 2: "minimize.svg"}
        self.btn_fullscreen.setIcon(self._get_icon(icons.get(state, "maximize.svg")))


class PreviewTextTab(QWidget):
    """
    A lightweight read-only viewer for previewing Markdown, HTML and text files.

    Everything is drawn by QTextBrowser, so no Chromium process is started:
    Markdown is rendered to rich text, HTML is shown with its markup and
    local images (scripts never run), and other text files get syntax
    highlighting when Pygments is available.
    """

    MAX_BYTES = 2 * 1024 * 1024

    def __init__(self, path: str, dark_mode: bool = False, parent=None):
        super().__init__(parent)
        self.current_path = path
        self.is_preview = True
        self.dark_mode = dark_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QTextBrowser

        self.editor = QTextBrowser()
        self.editor.setOpenLinks(False)
        self.editor.anchorClicked.connect(self._on_anchor_clicked)
        layout.addWidget(self.editor)

        self.apply_theme(dark_mode)
        self._load()

    def _read_text(self) -> str:
        with open(self.current_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(self.MAX_BYTES)
            if len(content) == self.MAX_BYTES:
                content += "\n\n[... File truncated for preview ...]"
        return content

    def _load(self) -> None:
        path = self.current_path
        lower = path.lower()
        folder = os.path.dirname(os.path.abspath(path))
        self.editor.document().setBaseUrl(QUrl.fromLocalFile(folder + os.sep))
        self.editor.setSearchPaths([folder])

        try:
            content = self._read_text()
        except Exception as e:
            self.editor.setPlainText(f"Error loading preview: {e}")
            return

        if lower.endswith((".md", ".markdown")):
            self.editor.setHtml(self._markdown_html(content))
        elif lower.endswith((".html", ".htm", ".xhtml")):
            self.editor.setHtml(self._strip_active_content(content))
        else:
            self.editor.setHtml(self._code_html(content, path))

    def _page_css(self) -> str:
        t = tokens(self.dark_mode)
        fg, muted, code_bg, border, link = t["text"], t["muted"], t["raised"], t["border_strong"], t["accent"]
        return f"""
            body {{ color: {fg}; font-size: 15px; }}
            h1, h2 {{ border-bottom: 1px solid {border}; padding-bottom: 4px; }}
            a {{ color: {link}; }}
            code {{ background-color: {code_bg}; font-family: monospace; }}
            pre {{ background-color: {code_bg}; font-family: monospace; padding: 10px; }}
            blockquote {{ color: {muted}; border-left: 3px solid {border}; padding-left: 10px; }}
            table {{ border-collapse: collapse; }}
            th, td {{ border: 1px solid {border}; padding: 4px 8px; }}
        """

    def _markdown_html(self, text: str) -> str:
        try:
            body = render_markdown_safely(text, extensions=("fenced_code", "tables", "sane_lists", "toc"))
        except Exception:
            import html as html_lib

            body = f"<pre>{html_lib.escape(text)}</pre>"
        return f"<html><head><style>{self._page_css()}</style></head><body>{self._qt_blocks(body)}</body></html>"

    def _qt_blocks(self, body: str) -> str:
        """
        Qt's rich text pads and borders table cells only, so code blocks become
        a padded cell and quotes a cell beside a narrow bar. Opening and closing
        tags are replaced one by one, which keeps nested quotes nested.
        """
        t = tokens(self.dark_mode)
        body = re.sub(
            r"<pre\b",
            f'<table width="100%" cellspacing="0" cellpadding="10" bgcolor="{t["raised"]}"'
            ' style="margin: 6px 0; border-style: none;"><tr><td><pre',
            body,
        )
        body = re.sub(r"\n?(</code>)?</pre>", lambda m: (m.group(1) or "") + "</pre></td></tr></table>", body)
        body = re.sub(
            r"<blockquote\b[^>]*>",
            f'<table cellspacing="0" cellpadding="0" style="margin: 6px 0; border-style: none;"><tr>'
            f'<td style="border-style: none none none solid; border-width: 3px; border-color: {t["border_strong"]};'
            f' padding-left: 12px; color: {t["muted"]};">',
            body,
        )
        return body.replace("</blockquote>", "</td></tr></table>")

    @staticmethod
    def _strip_active_content(content: str) -> str:
        """Removes scripts and embedded frames; the preview only shows the page."""
        content = re.sub(r"(?is)<script\b.*?</script>", "", content)
        content = re.sub(r"(?is)<(iframe|object|embed)\b.*?(</\1>|/?>)", "", content)
        return content

    def _code_html(self, text: str, path: str) -> str:
        import html as html_lib

        try:
            from pygments import highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import TextLexer, get_lexer_for_filename

            try:
                lexer = get_lexer_for_filename(path, stripnl=False)
            except Exception:
                lexer = TextLexer()
            formatter = HtmlFormatter(
                noclasses=True,
                nobackground=True,
                style="monokai" if self.dark_mode else "default",
            )
            body = highlight(text, lexer, formatter)
        except Exception:
            body = f"<pre>{html_lib.escape(text)}</pre>"
        return (
            f"<html><head><style>{self._page_css()} pre {{ font-size: 13px; }}</style>"
            f"</head><body>{body}</body></html>"
        )

    def _on_anchor_clicked(self, url: QUrl) -> None:
        if not url.scheme() and url.fragment():
            self.editor.scrollToAnchor(url.fragment())
            return
        relative = url.isRelative()
        if relative:
            url = self.editor.document().baseUrl().resolved(url)
        win = self.window()
        if url.isLocalFile():
            if not relative:
                return
            local = url.toLocalFile()
            if os.path.isfile(local) and hasattr(win, "_open_document"):
                win._open_document(local)
            return
        if url.scheme() in ("http", "https") and hasattr(win, "new_browser_tab"):
            win.new_browser_tab(url.toString())

    def apply_theme(self, dark_mode: bool):
        self.dark_mode = dark_mode
        t = tokens(dark_mode)
        bg = t["canvas"] if dark_mode else t["surface"]
        fg = t["text"]
        self.editor.setStyleSheet(
            f"QTextBrowser {{ background-color: {bg}; color: {fg}; border: none; padding: 16px 24px; }}"
        )

    def toggle_theme(self):
        self.apply_theme(not self.dark_mode)
        self._load()

    def cleanup(self):
        self.editor.clear()

    def update_fullscreen_icon(self, state):
        pass
