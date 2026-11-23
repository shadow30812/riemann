"""
Streaming a page's video: to an outside player, or in Riemann's own player window.

StreamLauncher asks which player and which cookies to use, with dialogs over
the widget it is given, then starts mpv on the page address, or has yt-dlp
find the direct stream and hands that to VLC or to VideoPlayer. Messages go
out through toast.
"""

import shutil
import subprocess

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ...core.settings import app_settings, get_dialog_directory, save_last_directory
from ..threads import track
from .ytdlp import YtDlpStreamWorker

BUILT_IN_PLAYER = "Built-in Player"
SEEK_MS = 10000

AUTH_METHODS = [
    "No Cookies (Public Video)",
    "Use Netscape cookies.txt file (Recommended Backup)",
    "Auto-extract: Chrome",
    "Auto-extract: Firefox",
    "Auto-extract: Brave",
    "Auto-extract: Edge",
]


def format_time(ms: int) -> str:
    """ms as m:ss, or h:mm:ss from an hour."""
    m, s = divmod(ms // 1000, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def available_players() -> list:
    """The players offered for streaming: mpv and VLC when installed, then the built-in one."""
    players = [name for name, exe in (("MPV", "mpv"), ("VLC", "vlc")) if shutil.which(exe)]
    return [*players, BUILT_IN_PLAYER]


class VideoPlayer(QDialog):
    """Plays a direct stream URL, pausing while it buffers so playback doesn't stutter."""

    def __init__(self, direct_url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Riemann Media Player - Buffering...")
        self.resize(2000, 500)

        layout = QVBoxLayout(self)
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
        self.btn_rewind = self._button(QStyle.StandardPixmap.SP_MediaSeekBackward)
        self.btn_play_pause = self._button(QStyle.StandardPixmap.SP_MediaPause)
        self.btn_forward = self._button(QStyle.StandardPixmap.SP_MediaSeekForward)
        for btn in (self.btn_rewind, self.btn_play_pause, self.btn_forward):
            controls_layout.addWidget(btn)
        layout.addLayout(controls_layout)

        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        self.btn_rewind.clicked.connect(lambda: self.player.setPosition(max(0, self.player.position() - SEEK_MS)))
        self.btn_forward.clicked.connect(lambda: self.player.setPosition(self.player.position() + SEEK_MS))
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.positionChanged.connect(self._update_slider)
        self.player.durationChanged.connect(self._update_duration)
        self.player.setSource(QUrl(direct_url))

    def _button(self, pixmap: QStyle.StandardPixmap) -> QPushButton:
        btn = QPushButton()
        btn.setIcon(self.style().standardIcon(pixmap))
        btn.setFixedSize(40, 40)
        return btn

    def _update_slider(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self.time_label.setText(f"{format_time(position)} / {format_time(self.player.duration())}")

    def _update_duration(self, duration: int) -> None:
        self.position_slider.setRange(0, duration)

    def toggle_play_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        pixmap = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self.btn_play_pause.setIcon(self.style().standardIcon(pixmap))

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.BufferingMedia:
            self.setWindowTitle("Riemann Media Player - Buffering...")
            self.player.pause()
        elif status in (QMediaPlayer.MediaStatus.BufferedMedia, QMediaPlayer.MediaStatus.LoadedMedia):
            self.setWindowTitle("Riemann Media Player")
            self.player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.setWindowTitle("Riemann Media Player - Finished")
            self.player.stop()


class StreamLauncher(QObject):
    toast = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._widget = parent
        self.selected_player: str | None = None
        self.stream_worker: YtDlpStreamWorker | None = None
        self.video_window: VideoPlayer | None = None

    def _ask_cookies(self) -> tuple | None:
        """(cookie_file, cookies_browser) the person chose, either may be None; None when cancelled."""
        choice, ok = QInputDialog.getItem(
            self._widget, "Authentication", "Select cookie source:", AUTH_METHODS, 0, False
        )
        if not ok:
            return None
        if "Netscape" in choice:
            store = app_settings()
            path, _ = QFileDialog.getOpenFileName(
                self._widget, "Select Netscape Cookies File", get_dialog_directory(store), "Text Files (*.txt)"
            )
            if not path:
                self.toast.emit("Streaming cancelled: No cookie file provided.")
                return None
            save_last_directory(store, path)
            return path, None
        if "Auto-extract" in choice:
            return None, choice.split(": ")[1].lower()
        return None, None

    def stream(self, page_url: str) -> None:
        """Asks for a player and cookies, then streams page_url's video in it."""
        raw_url = page_url.strip()
        if raw_url.startswith("-") or not raw_url.startswith(("http://", "https://")):
            self.toast.emit("Invalid or insecure URL for streaming.")
            return

        choice, ok = QInputDialog.getItem(
            self._widget,
            "Select Media Player",
            "Choose a player to stream this video:",
            available_players(),
            0,
            False,
        )
        if not ok:
            return
        self.selected_player = choice
        cookies = self._ask_cookies()
        if cookies is None:
            return
        cookie_file, cookies_browser = cookies

        if choice == "MPV":
            self._start_mpv(raw_url, cookie_file, cookies_browser)
            return
        self.toast.emit(f"Extracting stream for {choice}...")
        self.stream_worker = YtDlpStreamWorker(raw_url, cookies_browser=cookies_browser, cookie_file=cookie_file)
        self.stream_worker.finished.connect(self._on_stream_extracted)
        self.stream_worker.error.connect(lambda e: self.toast.emit(f"Stream error: {e}"))
        track(self.stream_worker).start()

    def _start_mpv(self, url: str, cookie_file: str | None, cookies_browser: str | None) -> None:
        self.toast.emit("Opening in MPV...")
        args = [str(shutil.which("mpv")), "--keep-open=yes"]
        if cookie_file:
            args.append(f"--cookies-file={cookie_file}")
        elif cookies_browser:
            args.append(f"--ytdl-raw-options=cookies-from-browser={cookies_browser}")
        args.append(url)
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        except Exception:
            self.toast.emit("Failed to start MPV.")

    def _on_stream_extracted(self, direct_url: str) -> None:
        """Hands the direct stream to the chosen player."""
        if self.selected_player == "VLC":
            try:
                subprocess.Popen(
                    [str(shutil.which("vlc")), direct_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                return
            except Exception as e:
                print(f"[ERROR] VLC failed: {e}")
                self.toast.emit("Failed to start VLC. Falling back to native player.")
        self.play(direct_url)

    def play(self, direct_url: str) -> VideoPlayer:
        """
        Opens direct_url in the built-in player window. An earlier window's player is let go and its buttons
        steer the new one, as when the tab held a single player.
        """
        previous = self.video_window
        self.video_window = VideoPlayer(direct_url, self._widget)
        if previous is not None:
            previous.player = self.video_window.player
            previous.audio_output = None
        self.video_window.show()
        return self.video_window
