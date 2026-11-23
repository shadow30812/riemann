"""
BrowserTab: one web page in a tab, with Riemann's toolbar around it.

The toolbar, find bar, shortcuts, theme, zoom and bookmarks come from the
chrome mixin; downloads is a mixin too. The attributes they share (web,
profile, toolbar buttons, progress, lbl_toast, dark_mode, incognito) are made
in __init__. Captions, music and video modes and mute are MediaControls
(media.py), and streaming to a player is StreamLauncher (stream.py): parts
the tab owns, given only what they work on (the web view, the captions
server, the widget their dialogs open over). The tab turns its buttons into
calls on them and their signals into toasts and the tab bar's speaker mark.

Notes:

_os_display_name: EXTENDED_NAME_FORMAT 3 = NameDisplay ("Ada Lovelace") Unix
only; importing it at module level broke Windows.

progress: the page-loading line above the toolbar, LOAD_BAR_HEIGHT pixels.
Its own style sheet sets the height too, since the theme style sheets give
every progress bar min-height: 8px, which outranked setFixedHeight.

homepage_icon_tones: the start page can't read the pixels of the favicons it
loads from other sites, so the tab measures the cached copy of each quick
link's icon (icon_tone) and sends the tones as window.iconTones; the page
puts a plate behind an icon in the theme it would vanish in.

eventFilter: The tab is closing: its view is gone but children still report
events.
"""

import base64
import json
import os
import sys
import urllib.parse
from typing import Any

import shiboken6
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QEventLoop,
    QIODevice,
    QObject,
    QSize,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import permissions
from ...core.assets import icon_path
from ...core.captions_server import CaptionsServer
from ...core.log import log
from ...core.settings import (
    HOMEPAGE_LINKS_KEY,
    HOMEPAGE_NAME_KEY,
    app_settings,
    browser_settings,
    favicon_cache,
    search_template,
    search_url,
)
from ..browser_handlers import ScriptInjector
from ..palette import icon_tone, incognito_url_style, legible_icon, link_tip_style, page_theme, tokens
from ..toast import Toast
from .chrome import ChromeMixin
from .downloads import DownloadsMixin
from .interceptor import (
    HOMEPAGE_PATH,
    RequestInterceptor,
    is_local_homepage,
    sanitize_homepage_links,
)
from .media import MediaControls
from .page import LEAVE_WAIT_MS, PermissionBar, WebPage
from .stream import StreamLauncher

LOAD_BAR_HEIGHT = 2


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


def homepage_icon_tones(links_json: str, cached_icons: dict) -> dict:
    """{host: "dark" or "light"} for the quick links whose cached icon needs a plate in that theme."""
    try:
        links = json.loads(links_json) if links_json else []
    except ValueError:
        return {}
    tones = {}
    for link in links if isinstance(links, list) else []:
        url = str(link.get("url", "")) if isinstance(link, dict) else ""
        host = urllib.parse.urlsplit(url if "://" in url else "https://" + url).hostname or ""
        data = cached_icons.get(host, "")
        if host in tones or not data.startswith("data:image/") or "," not in data:
            continue
        try:
            image = QImage.fromData(base64.b64decode(data.split(",", 1)[1]))
        except ValueError:
            continue
        if image.isNull():
            continue
        tone = icon_tone(
            image.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
        if tone:
            tones[host] = tone
    return tones


class BrowserTab(ChromeMixin, DownloadsMixin, QWidget):
    def __init__(
        self,
        start_url: str = "",
        parent: QWidget | None = None,
        profile: QWebEngineProfile | None = None,
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

        self._build_toolbar(layout, icon_size)

        self._build_search_bar(layout)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(LOAD_BAR_HEIGHT)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 0px;
                background-color: transparent;
                min-height: {LOAD_BAR_HEIGHT}px;
                max-height: {LOAD_BAR_HEIGHT}px;
            }}
            QProgressBar::chunk {{
                background-color: {tokens(self.dark_mode)["accent"]};
                border-radius: 1px;
            }}
        """)
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
        self.web.loadFinished.connect(self._on_homepage_load_finished)

        self._create_shortcuts()

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
                home_url.setQuery("theme=" + page_theme(self.dark_mode))
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
        self.media = MediaControls(self.web, app.captions_server, self)
        self.media.toast.connect(self.show_toast)
        self.media.playing_changed.connect(self._set_tab_playing_state)
        self.streamer = StreamLauncher(self)
        self.streamer.toast.connect(self.show_toast)

        self.link_tooltip = QLabel(self)
        self.link_tooltip.setStyleSheet(link_tip_style(tokens(self.dark_mode)))
        self.link_tooltip.hide()
        self.web.page().linkHovered.connect(self._on_link_hovered)

    def toggle_captions(self) -> None:
        on = self.btn_captions.isChecked()
        self.btn_captions.setIcon(self._get_icon("captions.svg" if on else "captions-off.svg"))
        self.media.set_captions(on)

    def toggle_music_mode(self) -> None:
        self.btn_music.setChecked(self.media.set_music_mode(self.btn_music.isChecked()))

    def toggle_video_mode(self) -> None:
        self.btn_video_speed.setChecked(self.media.set_video_mode(self.btn_video_speed.isChecked()))

    def toggle_mute(self) -> None:
        muted = self.btn_mute.isChecked()
        self.media.set_muted(muted)
        self.btn_mute.setIcon(self._get_icon("volume-x.svg" if muted else "volume-on.svg"))

    def stream_video(self) -> None:
        self.streamer.stream(self.web.url().toString())

    def _set_tab_playing_state(self, state: str | None) -> None:
        """Shows on the tab bar whether this tab is playing sound."""
        parent = self.parent()
        while parent:
            if isinstance(parent, QTabWidget):
                idx = parent.indexOf(self)
                if idx != -1:
                    parent.tabBar().setTabData(idx, state)
                    parent.tabBar().update()
                break
            parent = parent.parent()

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

            links = sanitize_homepage_links(settings.value(HOMEPAGE_LINKS_KEY, "")) or "null"
            fav_settings = favicon_cache()
            cached_icons = {}
            for key in fav_settings.allKeys():
                cached_icons[key] = fav_settings.value(key, "", type=str)
            icons_json = json.dumps(cached_icons)

            tones_json = json.dumps(homepage_icon_tones("" if links == "null" else links, cached_icons))

            theme = page_theme(self.dark_mode)
            js_code = (
                f"window.setTheme && window.setTheme('{theme}'); "
                f"window.cachedIcons = {icons_json}; window.iconTones = {tones_json}; window.searchTemplate = "
                f"{json.dumps(search_template())}; "
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
        """Shows the site's favicon on the tab, on a plate when it would vanish on the tab bar."""

        parent = self.parent()
        page_icon = QIcon(icon_path("browser.png"))
        while parent:
            if isinstance(parent, QTabWidget):
                idx = parent.indexOf(self)
                if idx != -1 and not icon.isNull():
                    parent.setTabIcon(
                        idx, legible_icon(icon, bool(getattr(self.window(), "dark_mode", self.dark_mode)))
                    )
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

    def refresh_tab_icon(self) -> None:
        self._update_tab_icon(self.web.icon())

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
