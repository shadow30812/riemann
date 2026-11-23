"""
Window chrome: title, toasts, full screen and auto-hiding controls, themes, the status metrics and media shutdown.

Part of RiemannWindow (riemann/app.py), split out by concern; the methods use
the window's attributes freely, as they did when they lived in one class.
"""

import os
import time
import psutil
from PySide6.QtCore import (
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QCursor,
    QIcon,
)
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineSettings,
)
from ..ui.browser import BrowserTab
from ..ui.reader.tab import ReaderTab
from ..ui.viewers import VIEWER_TABS
from ..ui import tooltip
from ..ui.palette import toast_style, tokens
from ..ui.toast import Toast
from ..core.log import log
from ..core.assets import asset_path, icon_path
from ..core.settings import DARK_MODE_KEY


class ChromeMixin:
    lbl_toast = None

    def _reveal_controls(self, show: bool):
        """Shows or hides the menu bar, tab bars and toolbars."""
        state = getattr(self, "_fullscreen_state", 0)
        if show:
            self.menuBar().show()
            self._restore_tab_bars()
            self._set_tabs_toolbar_visible(True)
        else:
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            if self.tabs_side.isVisible():
                self.tabs_side.tabBar().hide()
            if state == 2:
                self._set_tabs_toolbar_visible(False)
            elif state == 1:
                self._set_tabs_toolbar_visible(True)

    def _check_auto_hide(self):
        """
        Checks the mouse cursor position to automatically hide UI controls in fullscreen reading mode.
        """
        if getattr(self, "_reader_fullscreen", False) and not self._pointer_over_controls():
            self._reveal_controls(False)

    def _revealed_control_widgets(self) -> list:
        """The bars full screen reveals: menu bar, tab strips, and each visible tab's own bars and rail."""
        widgets = [self.menuBar(), self.tabs_main.tabBar(), self.tabs_side.tabBar()]
        for pane in (self.tabs_main, self.tabs_side):
            tab = pane.currentWidget()
            if tab is None or not pane.isVisible():
                continue
            for name in ("toolbar", "anno_toolbar", "search_bar", "ai_search_bar"):
                bar = getattr(tab, name, None)
                if bar is not None:
                    widgets.append(bar)
            layout = getattr(tab, "toolbar_layout", None)
            if layout is not None:
                widgets += [w for w in (layout.rail, layout.pill) if w is not None]
        return [w for w in widgets if w.isVisible()]

    def _pointer_over_controls(self) -> bool:
        """
        Whether the pointer is over the revealed bars: above the lowest
        visible bar at the top of the window (at least 100 px), or on any of
        them elsewhere, such as the reader's side rail.
        """
        pos = QCursor.pos()
        local = self.mapFromGlobal(pos)
        bottom = 100
        for widget in self._revealed_control_widgets():
            top_left = widget.mapTo(self, widget.rect().topLeft())
            rect = widget.rect().translated(top_left)
            if rect.contains(local):
                return True
            if rect.top() < bottom + 10 and rect.width() > self.width() // 3:
                bottom = max(bottom, rect.bottom() + 1)
        return local.y() <= bottom

    def _update_window_title(self, index: int = -1) -> None:
        self._sync_assistant_document()
        target = (
            self.tabs_side
            if (self.tabs_side.isVisible() and self.tabs_side.hasFocus())
            else self.tabs_main
        )
        idx = target.currentIndex()
        prefix = "Riemann (Incognito)" if self.incognito else "Riemann"

        if idx != -1:
            tab_title = target.tabText(idx)
            self.setWindowTitle(f"{prefix} - {tab_title}")
        else:
            self.setWindowTitle(prefix)

        target_widget = target.currentWidget()
        if target_widget:
            target_widget.setFocus()

        self.refresh_signature_panel()

    def show_toast(self, msg: str) -> None:
        if self.lbl_toast is None:
            self.lbl_toast = Toast(self)
        self.lbl_toast.show_message(msg, toast_style(tokens(getattr(self, "dark_mode", False))), 4000)

    def exit_fullscreen(self) -> None:
        """Helper to fully exit any fullscreen reading states."""
        self._fullscreen_state = 0
        self._reader_fullscreen = False

        self.fs_tracker_timer.stop()
        self.floating_fs_btn.hide()
        self.floating_fs_timer.stop()

        self.menuBar().show()
        self._restore_tab_bars()
        self._set_tabs_toolbar_visible(True)

        if getattr(self, "_was_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()

        self._broadcast_fullscreen_icon(0)
        self._update_floating_fs_btn_icon()

    def toggle_reader_fullscreen(self) -> None:
        """
        Toggles global fullscreen mode cycling through 3 states:
        Normal -> Reading Mode (toolbar visible) -> Pure Fullscreen.
        """
        state = getattr(self, "_fullscreen_state", 0)

        if state == 0:
            self._fullscreen_state = 1
            self._was_maximized = self.isMaximized()
            self.menuBar().hide()
            self.tabs_main.tabBar().hide()
            self.tabs_side.tabBar().hide()
            self._set_tabs_toolbar_visible(True)
            self._reader_fullscreen = True
            self.showFullScreen()
            self._broadcast_fullscreen_icon(1)
            self.fs_tracker_timer.start()

        elif state == 1:
            self._fullscreen_state = 2
            self._set_tabs_toolbar_visible(False)
            self._broadcast_fullscreen_icon(2)
            self.fs_tracker_timer.start()

        else:
            self.exit_fullscreen()
        self._update_floating_fs_btn_icon()

    def _set_tabs_toolbar_visible(self, visible: bool) -> None:
        """Shows or hides the toolbar of every tab."""
        for tab_widget in [self.tabs_main, self.tabs_side]:
            for i in range(tab_widget.count()):
                w = tab_widget.widget(i)
                if hasattr(w, "toolbar"):
                    if getattr(w, "is_preview", False):
                        w.toolbar.setVisible(False)
                    elif getattr(w, "current_path", True):
                        w.toolbar.setVisible(visible)

    def toggle_ui_theme(self) -> None:
        """
        Toggles the global UI dark mode without altering the content of the active tabs.
        """
        self.dark_mode = not self.dark_mode
        self.settings.setValue(DARK_MODE_KEY, self.dark_mode)
        self.enforce_global_stylesheet()
        self._update_floating_btn_style()
        self._style_menu_corner()
        self._update_layout_button_icons()

        if hasattr(self, "explorer_panel"):
            self.explorer_panel.update_theme(self.dark_mode)

        self.sidecar_panel.update_theme(self.dark_mode)
        self._refresh_split_style()
        if hasattr(self, "ytdlp_manager_dialog"):
            self.ytdlp_manager_dialog.apply_theme(self.dark_mode)

        if hasattr(self, "_update_global_metrics"):
            self._update_global_metrics()

        for tab_widget in (self.tabs_main, self.tabs_side):
            for i in range(tab_widget.count()):
                w = tab_widget.widget(i)
                if hasattr(w, "_update_icons"):
                    w._update_icons()
                if hasattr(w, "apply_ui_theme"):
                    w.apply_ui_theme(self.dark_mode)
                if getattr(w, "is_system_tab", False):
                    tab_widget.setTabIcon(i, w.tab_icon(self.dark_mode))
                if getattr(w, "is_preview", False):
                    tab_widget.setTabIcon(i, self._preview_icon())
                elif isinstance(w, (ReaderTab,) + VIEWER_TABS):
                    tab_widget.setTabIcon(i, self._document_icon(w.current_path or ""))

        self.refresh_signature_panel()

    def toggle_active_tab_theme(self) -> None:
        """
        Toggles the local content theme (PDF canvas or web page)
        of the currently focused tab without affecting the global UI.
        """
        target_widget = self.tabs_main.currentWidget()
        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            target_widget = self.tabs_side.currentWidget()

        if hasattr(target_widget, "toggle_theme"):
            target_widget.toggle_theme()

    def _kill_all_media_safely(self) -> None:
        """
        Navigates active media tabs to their root domain before exit
        to sever the media pipeline without destroying C++ objects prematurely.
        """
        media_domains = [
            "youtube.com",
            "dailymotion.com",
            "reddit.com",
            "vimeo.com",
            "twitch.tv",
            "spotify.com",
            "netflix.com",
        ]

        for target in (self.tabs_main, self.tabs_side):
            for i in range(target.count()):
                wid = target.widget(i)
                if (
                    isinstance(wid, BrowserTab)
                    and hasattr(wid, "web")
                    and wid.web.page()
                ):
                    wid.web.page().setAudioMuted(True)
                    url_str = wid.web.url().toString().lower()
                    is_media = False

                    for domain in media_domains:
                        if domain in url_str:
                            is_media = True
                            wid.web.settings().setAttribute(
                                QWebEngineSettings.WebAttribute.JavascriptEnabled, False
                            )
                            wid.web.load(QUrl("about:blank"))
                            break

                    if not is_media:
                        wid.web.triggerPageAction(QWebEnginePage.WebAction.Stop)

    def enforce_global_stylesheet(self) -> None:
        """
        Forces the application to read and apply the appropriate QSS file,
        preventing Qt from reverting to native generic tab styles.
        """
        css_file = (
            "modern_dark.css"
            if getattr(self, "dark_mode", False)
            else "modern_light.css"
        )

        theme_dir = asset_path("theme")
        css_path = os.path.join(theme_dir, css_file)

        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()

            theme_dir_css = theme_dir.replace("\\", "/")
            stylesheet = stylesheet.replace("url('", f"url('{theme_dir_css}/")
            stylesheet = stylesheet.replace('url("', f'url("{theme_dir_css}/')

            self.setStyleSheet(stylesheet)
        else:
            self.setStyleSheet("")
        tooltip.set_dark(getattr(self, "dark_mode", False))

    def _get_icon_img_tag(self, icon_base: str) -> str:
        """Generates an HTML img tag pointing to the correct local SVG based on dark mode."""
        path = icon_path(f"{icon_base}.svg", getattr(self, "dark_mode", False)).replace(os.sep, "/")
        return f'<img src="file:///{path}" width="14" height="14" style="vertical-align: middle;">'

    def _update_global_metrics(self) -> None:
        """Updates the global metrics ticker in the menu bar with icons and real speeds."""
        uptime_seconds = int(time.time() - getattr(self, "_start_time", time.time()))
        m, s = divmod(uptime_seconds, 60)
        h, m = divmod(m, 60)
        uptime_str = f"{h:02d}:{m:02d}:{s:02d}"

        mem_str = "--"
        net_str = f"{self._get_icon_img_tag('arrow-big-down-dash')} -- MB/s &nbsp; {self._get_icon_img_tag('arrow-big-up-dash')} -- MB/s"
        bat_str = f"{self._get_icon_img_tag('battery')} --%"

        if psutil:
            try:
                process = psutil.Process(os.getpid())
                mem_bytes = process.memory_info().rss
                for child in process.children(recursive=True):
                    mem_bytes += child.memory_info().rss
                mem_mb = mem_bytes / (1024 * 1024)
                mem_str = f"{mem_mb:.1f} MB"

                current_net_io = psutil.net_io_counters()
                current_time = time.time()

                if getattr(self, "_last_net_io", None):
                    dt = current_time - self._last_net_time
                    if dt > 0:
                        recv_speed = (
                            (current_net_io.bytes_recv - self._last_net_io.bytes_recv)
                            / dt
                            / (1024 * 1024)
                        )
                        sent_speed = (
                            (current_net_io.bytes_sent - self._last_net_io.bytes_sent)
                            / dt
                            / (1024 * 1024)
                        )
                        net_str = f"{self._get_icon_img_tag('arrow-big-down-dash')} {recv_speed:.2f} MB/s &nbsp; {self._get_icon_img_tag('arrow-big-up-dash')} {sent_speed:.2f} MB/s"

                self._last_net_io = current_net_io
                self._last_net_time = current_time

                battery = psutil.sensors_battery()
                if battery:
                    if battery.power_plugged:
                        bat_icon = "battery-charging"
                    elif battery.percent > 80:
                        bat_icon = "battery-full"
                    elif battery.percent > 30:
                        bat_icon = "battery-medium"
                    elif battery.percent > 10:
                        bat_icon = "battery-low"
                    else:
                        bat_icon = "battery"

                    bat_str = (
                        f"{self._get_icon_img_tag(bat_icon)} {battery.percent:.0f}%"
                    )
            except Exception:
                log.debug("_update_global_metrics failed", exc_info=True)

        clock_str = time.strftime("%I:%M:%S %p")
        self.metrics_label.setText(
            f" <b>{clock_str}</b> &nbsp;|&nbsp; Mem: {mem_str} &nbsp;|&nbsp; Uptime: {uptime_str} &nbsp;|&nbsp; {net_str} &nbsp;|&nbsp; {bat_str} "
        )
        needed = self.metrics_label.sizeHint().width()
        if needed > self.metrics_label.minimumWidth():
            self.metrics_label.setMinimumWidth(needed)
            self.menu_corner_widget.adjustSize()
            self.menuBar().setCornerWidget(self.menu_corner_widget, Qt.Corner.TopRightCorner)

    def _update_floating_btn_style(self) -> None:
        """Applies dynamic theme styling to the floating fullscreen button."""
        t = tokens(self.dark_mode)
        self.floating_fs_btn.setStyleSheet(
            f"background: {t['raised']}; color: {t['text']}; border: 1px solid {t['border_strong']}; border-radius: 8px;"
        )
        self._update_floating_fs_btn_icon()

    def _update_floating_fs_btn_icon(self) -> None:
        """Retrieves and applies the correct icon state to the floating button."""
        state = getattr(self, "_fullscreen_state", 0)
        icons = {0: "panel-top.svg", 1: "maximize.svg", 2: "minimize.svg"}
        icon_name = icons.get(state, "maximize.svg")

        self.floating_fs_btn.setIcon(QIcon(icon_path(icon_name, getattr(self, "dark_mode", False))))

    def _broadcast_fullscreen_icon(self, state: int) -> None:
        """Helper to safely sync the fullscreen icon across all open tabs simultaneously."""
        for target in (self.tabs_main, self.tabs_side):
            for i in range(target.count()):
                widget = target.widget(i)
                if hasattr(widget, "update_fullscreen_icon"):
                    widget.update_fullscreen_icon(state)

    def _track_mouse_for_fs(self) -> None:
        """
        Polls the mouse position during fullscreen to reliably show UI elements,
        bypassing event-swallowing issues in QWebEngineView and QScrollArea.
        """
        if not getattr(self, "_reader_fullscreen", False):
            return

        local_pos = self.mapFromGlobal(QCursor.pos())

        if local_pos.y() < 40:
            if self.hover_timer.isActive():
                self.hover_timer.stop()
            self._reveal_controls(True)
        elif not self._pointer_over_controls():
            if not self.hover_timer.isActive():
                self.hover_timer.start()
        elif self.hover_timer.isActive():
            self.hover_timer.stop()

        if self.settings.value("app/floating_fs_btn", True, type=bool):
            if local_pos.x() > self.width() - 150 and local_pos.y() < 100:
                self._update_floating_fs_btn_icon()
                self.floating_fs_btn.move(
                    self.width() - self.floating_fs_btn.width() - 20, 20
                )
                if not self.floating_fs_btn.isVisible():
                    self.floating_fs_btn.show()
                    self.floating_fs_btn.raise_()
                self.floating_fs_timer.stop()
            elif local_pos.x() < 150 and local_pos.y() < 100:
                self._update_floating_fs_btn_icon()
                self.floating_fs_btn.move(20, 20)
                if not self.floating_fs_btn.isVisible():
                    self.floating_fs_btn.show()
                    self.floating_fs_btn.raise_()
                self.floating_fs_timer.stop()
            else:
                if (
                    self.floating_fs_btn.isVisible()
                    and not self.floating_fs_timer.isActive()
                ):
                    self.floating_fs_timer.start()
