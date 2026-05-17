import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
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

        # Poll every 500ms to update the seek bar and detect new media
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self.poll_media_state)
        self.poll_timer.start()

        self.hide()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 0, 15, 0)
        layout.setSpacing(8)

        self.btn_play_pause = QPushButton(self)
        self.btn_play_pause.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        )
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
        self._is_seeking = False
        if self.active_browser:
            seek_val = self.position_slider.value()
            js = f"let m = [...document.querySelectorAll('audio, video')].find(e => e.duration > 0); if(m) m.currentTime = {seek_val};"
            self.active_browser.web.page().runJavaScript(js)

    def toggle_playback(self):
        if self.active_browser:
            js = "let m = [...document.querySelectorAll('audio, video')].find(e => e.duration > 0); if(m) { if(m.paused) m.play(); else m.pause(); }"
            self.active_browser.web.page().runJavaScript(js)

    def find_active_browser(self):
        """Finds the first browser tab using safe duck typing."""
        for tabs in [self.main_window.tabs_main, self.main_window.tabs_side]:
            if tabs.isVisible() and tabs.hasFocus():
                curr = tabs.currentWidget()
                if hasattr(curr, "web"):
                    return curr

        curr_main = self.main_window.tabs_main.currentWidget()
        if hasattr(curr_main, "web"):
            return curr_main

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
        browser = self.find_active_browser()
        if not browser:
            self.set_visibility(False)
            self.active_browser = None
            return

        self.active_browser = browser

        # JS snippet altered to return a STRING delimited by a pipe "|" to bypass Qt dict mapping bugs
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
            print(f"[Media Poll] String Payload: {result}")

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

                    icon = (
                        QStyle.StandardPixmap.SP_MediaPlay
                        if paused
                        else QStyle.StandardPixmap.SP_MediaPause
                    )
                    self.btn_play_pause.setIcon(self.style().standardIcon(icon))
                except Exception:
                    self.set_visibility(False)
            else:
                self.set_visibility(False)

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
