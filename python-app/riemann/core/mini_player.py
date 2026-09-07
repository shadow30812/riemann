import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)


class MiniAudioPlayer(QWidget):
    """
    A smart compact player that polls Riemann's BrowserTabs to detect active HTML5
    media (YouTube, Spotify Web, etc.) and provides native Play/Pause and Seek controls.
    """

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.active_browser = None
        self._is_seeking = False

        self.setup_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self.poll_media_state)
        self.poll_timer.start()

        self.hide()

    def _get_icon(self, icon_name: str) -> QIcon:
        """
        Resolves the target SVG icon relative to the parent application's currently configured theme state.
        Automatically appends '-white' to the filename if dark mode is active.
        """
        is_dark = getattr(self.main_window, "dark_mode", False)
        suffix = "-white" if is_dark else ""
        base_name = icon_name.replace(".svg", "")

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_path = getattr(sys, "_MEIPASS")
            path = os.path.join(
                base_path, "riemann", "assets", "icons", f"{base_name}{suffix}.svg"
            )
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(
                base_path, "assets", "icons", f"{base_name}{suffix}.svg"
            )

        if not os.path.exists(path) and suffix:
            path = path.replace(f"{suffix}.svg", ".svg")

        return QIcon(path)

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 0, 15, 0)
        layout.setSpacing(8)

        self.btn_play_pause = QPushButton(self)
        self.btn_play_pause.setIcon(self._get_icon("pause.svg"))
        self.btn_play_pause.setFixedSize(24, 24)
        self.btn_play_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_pause.clicked.connect(self.toggle_playback)

        self.position_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.position_slider.setRange(0, 100)
        self.position_slider.setFixedWidth(100)
        self.position_slider.setCursor(Qt.CursorShape.PointingHandCursor)

        self.position_slider.sliderPressed.connect(self.slider_pressed)
        self.position_slider.sliderReleased.connect(self.slider_released)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(
            "color: #888; font-size: 11px; font-weight: bold;"
        )

        layout.addWidget(self.btn_play_pause)
        layout.addWidget(self.position_slider)
        layout.addWidget(self.time_label)

    def slider_pressed(self):
        self._is_seeking = True

    def slider_released(self):
        if self.active_browser:
            seek_val = self.position_slider.value()
            js = f"""
            try {{
                let mediaElements = [...document.querySelectorAll('audio, video')];
                let m = mediaElements.find(e => !e.paused && e.duration > 0) || mediaElements.find(e => e.duration > 0);
                if(m) m.currentTime = {seek_val};
            }} catch(e) {{}}
            """
            self.active_browser.web.page().runJavaScript(js)

        QTimer.singleShot(500, self._release_seek_lock)

    def _release_seek_lock(self):
        self._is_seeking = False

    def toggle_playback(self):
        if self.active_browser:
            js = """
            try {
                let mediaElements = [...document.querySelectorAll('audio, video')];
                let m = mediaElements.find(e => !e.paused && e.duration > 0) || mediaElements.find(e => e.duration > 0);
                if(m) {
                    if (m.paused) m.play(); else m.pause();
                }
            } catch(e) {}
            """
            self.active_browser.web.page().runJavaScript(js)

    def _is_browser_valid(self, browser):
        """Checks if a browser tab instance is still valid and alive."""
        if not browser:
            return False
        try:
            from shiboken6 import isValid
            if not isValid(browser):
                return False
        except Exception:
            pass
        if not hasattr(browser, "web") or not browser.web:
            return False
        try:
            page = browser.web.page()
            if not page:
                return False
        except (RuntimeError, AttributeError):
            return False
        return True

    def _get_all_app_browsers(self):
        """Collects all BrowserTab instances across all windows and tab widgets."""
        from PySide6.QtWidgets import QApplication
        browsers = []
        windows = [self.main_window]
        try:
            for top in QApplication.topLevelWidgets():
                if hasattr(top, "tabs_main") and top not in windows:
                    windows.append(top)
        except Exception:
            pass

        for win in windows:
            for tab_widget in [getattr(win, "tabs_main", None), getattr(win, "tabs_side", None)]:
                if not tab_widget:
                    continue
                for i in range(tab_widget.count()):
                    w = tab_widget.widget(i)
                    if hasattr(w, "web") and w not in browsers and self._is_browser_valid(w):
                        browsers.append(w)
        return browsers

    def find_active_browser(self):
        """
        Finds the browser tab to control.
        App-wide: checks active tab, audible tabs, or retains the currently controlled browser.
        """
        all_browsers = self._get_all_app_browsers()

        # 1. If currently focused tab is a browser with media/audio, prioritize it
        for tabs in [self.main_window.tabs_main, self.main_window.tabs_side]:
            if tabs and tabs.isVisible() and tabs.hasFocus():
                curr = tabs.currentWidget()
                if self._is_browser_valid(curr):
                    try:
                        if curr.web.page().recentlyAudible() or not self._is_browser_valid(self.active_browser):
                            return curr
                    except Exception:
                        pass

        # 2. Check if any browser tab in the app is actively audible (playing audio)
        for b in all_browsers:
            try:
                if b.web.page().recentlyAudible():
                    return b
            except Exception:
                pass

        # 3. If currently tracking a valid browser, keep tracking it even if user switched tabs
        if self._is_browser_valid(self.active_browser) and self.active_browser in all_browsers:
            return self.active_browser

        # 4. Fallback: check current tab of main tabs
        curr_main = self.main_window.tabs_main.currentWidget()
        if self._is_browser_valid(curr_main):
            return curr_main

        # 5. Fallback: any browser tab in the app
        if all_browsers:
            return all_browsers[0]

        return None

    def set_visibility(self, visible):
        """Safely toggles visibility and forcefully recalculates the MenuBar layout."""
        if self.isVisible() != visible:
            self.setVisible(visible)

            parent = self.parentWidget()
            if parent:
                parent.adjustSize()
                parent.updateGeometry()

            if self.main_window.menuBar():
                self.main_window.menuBar().adjustSize()
                self.main_window.menuBar().update()

    def poll_media_state(self):
        is_dark = getattr(self.main_window, "dark_mode", False)
        border_color = "white" if is_dark else "black"

        new_style = f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: rgba(136, 136, 136, 0.4);
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: #888;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #888;
                border: 2px solid {border_color};
                width: 10px;
                margin: -5px 0; /* Expands the handle outside the thin groove */
                border-radius: 7px; /* Makes it a perfect circle */
            }}
        """
        if self.position_slider.styleSheet() != new_style:
            self.position_slider.setStyleSheet(new_style)

        browser = self.find_active_browser()
        if not browser:
            self.set_visibility(False)
            self.active_browser = None
            return

        self.active_browser = browser

        js = """
        (function() {
            try {
                let mediaElements = [...document.querySelectorAll('audio, video')];
                let media = mediaElements.find(e => !e.paused && e.duration > 0) || mediaElements.find(e => e.duration > 0);
                if (media && !isNaN(media.duration)) {
                    let isPaused = media.paused ? "1" : "0";
                    return media.currentTime + "|" + media.duration + "|" + isPaused;
                }
                return "none";
            } catch(e) {
                return "error";
            }
        })();
        """

        def callback(result):
            if result and isinstance(result, str) and "|" in result:
                try:
                    parts = result.split("|")
                    current = float(parts[0])
                    duration = float(parts[1])
                    paused = parts[2] == "1"

                    self.set_visibility(True)

                    if not self._is_seeking:
                        self.position_slider.setRange(0, int(duration))
                        self.position_slider.setValue(int(current))

                    self.time_label.setText(
                        f"{self.format_time(current)} / {self.format_time(duration)}"
                    )

                    icon_name = "play.svg" if paused else "pause.svg"
                    self.btn_play_pause.setIcon(self._get_icon(icon_name))
                except Exception:
                    self.set_visibility(False)
            else:
                self.set_visibility(False)
                # If current browser has no media, allow switching to another browser on next poll
                if result == "none":
                    self.active_browser = None

        if hasattr(self.active_browser.web.page(), "runJavaScript"):
            self.active_browser.web.page().runJavaScript(js, callback)

    def format_time(self, seconds):
        if not seconds:
            return "00:00"
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

