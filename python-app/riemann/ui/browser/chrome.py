"""
The browser tab's toolbar, find bar, keyboard shortcuts, theme, zoom and bookmarks.

Notes:

_build_toolbar: Let the tab shrink below the toolbar's natural width (e.g. in
split view); the rightmost buttons are clipped instead of forcing the pane wide.
"""

from typing import Any

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.assets import icon_path
from ...core.settings import DARK_MODE_KEY, browser_settings
from ..palette import incognito_url_style, link_tip_style, menu_style, page_theme, toast_style, tokens
from ..shortcuts import share_keys
from .interceptor import is_local_homepage


class ChromeMixin:
    def _build_toolbar(self, layout: QVBoxLayout, icon_size: QSize) -> None:
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
        self.btn_back.setToolTip("Back (Alt+Left)")

        self.btn_fwd = QPushButton()
        self.btn_fwd.setIcon(
            QIcon(
                icon_path("chevron-right.svg")
            )
        )
        self.btn_fwd.setIconSize(icon_size)
        self.btn_fwd.setFixedWidth(30)
        self.btn_fwd.setToolTip("Forward (Alt+Right)")

        self.btn_reload = QPushButton()
        self.btn_reload.setIcon(
            QIcon(
                icon_path("rotate-cw.svg")
            )
        )
        self.btn_reload.setIconSize(icon_size)
        self.btn_reload.setFixedWidth(30)
        self.btn_reload.setToolTip("Reload (F5)")

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
        self.btn_download.clicked.connect(self._on_download_clicked)

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

    def _build_search_bar(self, layout: QVBoxLayout) -> None:
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

    def _create_shortcuts(self) -> None:
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

            if (current_app_fs and not target_state_fs) or (not current_app_fs and target_state_fs):
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
            theme = page_theme(self.dark_mode)
            self.web.page().runJavaScript(f"window.setTheme && window.setTheme('{theme}');")
        self.script_injector.inject_smart_dark_mode(self.web.page(), self.dark_mode)
        style = (
            f"QWidget {{ background: {t['canvas']}; color: {t['text']}; }} "
            f"QLineEdit {{ background: {t['surface']}; border: 1px solid {t['border']}; "
            f"border-radius: 8px; padding: 5px 10px; color: {t['text']}; "
            f"selection-background-color: {t['accent']}; selection-color: {t['accent_text']}; }} "
            f"QLineEdit:focus {{ border-color: {t['accent']}; }}"
            + menu_style(t)
        )
        self.toolbar.setStyleSheet(style)
        self.search_bar.setStyleSheet(style)
        self.lbl_toast.setStyleSheet(toast_style(t) + " font-weight: bold;")
        if hasattr(self, "link_tooltip"):
            self.link_tooltip.setStyleSheet(link_tip_style(t))

        if self.incognito:
            self.txt_url.setStyleSheet(incognito_url_style())

        self.web.setStyleSheet(menu_style(t))

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

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.lbl_toast.isVisible():
            self.lbl_toast.reposition()

    def show_toast(self, message: str) -> None:
        self.lbl_toast.show_message(message, self.lbl_toast.styleSheet())

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
        downloads = getattr(self, "downloads", None)
        dl_icon = "circle-stop.svg" if downloads is not None and downloads.video_download_running() else "download.svg"
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
