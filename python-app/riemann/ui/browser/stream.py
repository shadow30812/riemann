"""
Streaming a page's video: to an outside player, or in Riemann's own player window.
"""

import shutil
import subprocess

from PySide6.QtCore import Qt, QUrl
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
)

from ..threads import track
from ...core.settings import app_settings, get_dialog_directory, save_last_directory
from .ytdlp import YtDlpStreamWorker


class StreamMixin:
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
            track(self.stream_worker).start()

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
