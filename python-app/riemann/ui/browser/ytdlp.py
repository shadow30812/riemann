"""
yt-dlp: downloading a page's video, and finding a stream URL for an outside player.

yt-dlp comes from the optional packages (core/dependencies.py) with its pinned
YouTube solver (yt-dlp-ejs), which runs in Deno; js_runtimes points yt-dlp at
the pinned Deno, and yt-dlp is never given remote_components.

YtDlpStreamWorker reports the stream URL through found and YtDlpWorker the
outcome through done. Named finished, a signal hides QThread.finished, which
ui/threads.track and release wait for.

YtDlpWorker is yt-dlp's logger (debug, info, warning, error). With
ignoreerrors on, a failed video (a 403 from the site, a format that is
gone) is only reported to the logger and download returns normally, so the
worker keeps the errors and reports the last one instead of "Download
complete!". Without ignoreerrors, one subtitle track that answers 429 Too Many
Requests stops the whole download. yt-dlp logs a line at every step
(extraction, the challenge solver, each retry), not only while bytes arrive,
so stop is checked on every log line as well as in progress_hook: either raises
KeyboardInterrupt, which yt-dlp lets through.

SUBTITLE_LANGS takes English and its regional variants. "en.*" also matched
every machine translation into English (en-de, en-fr, ...), one request each,
and YouTube answered the burst with 429s.
"""

import os
import re
import time
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core.dependencies import script_dirs
from ...core.log import log
from ..palette import dialog_icon

_deno_dir = os.path.expanduser("~/.deno/bin")
if os.path.isdir(_deno_dir) and _deno_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_deno_dir}:{os.environ.get('PATH', '')}"


def js_runtimes() -> dict:
    """
    yt-dlp's js_runtimes option: the Deno that the pinned deno package put
    next to the optional-packages venv's Python (or in the packages folder's
    scripts), else Deno on PATH.
    """
    for folder in script_dirs():
        path = os.path.join(folder, "deno" + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(path):
            return {"deno": {"path": path}}
    return {"deno": {}}


class YtDlpStreamWorker(QThread):
    """
    Finds a direct stream URL for a page's media with yt-dlp, without downloading it, so
    it can be handed to an external player such as VLC or mpv.
    """

    found = Signal(str)
    error = Signal(str)

    def __init__(self, url, cookies_browser=None, cookie_file=None):
        super().__init__()
        self.url = url
        self.cookies_browser = cookies_browser
        self.cookie_file = cookie_file

    def run(self):
        """
        Executes the yt-dlp metadata extraction process asynchronously.

        Runs yt-dlp to parse the media URL. Emits the `found` signal with the raw
        stream URL if successful, or emits the `error` signal with a description of the
        failure if extraction fails or no URL is found in the response.
        """
        ydl_opts: dict[str, Any] = {
            "format": "best[ext=mp4]",
            "quiet": True,
            "noplaylist": True,
            "js_runtimes": js_runtimes(),
        }
        if self.cookie_file:
            ydl_opts["cookiefile"] = self.cookie_file
        elif self.cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (self.cookies_browser,)

        try:
            import yt_dlp

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if info and "url" in info:
                    self.found.emit(info["url"])
                else:
                    self.error.emit("No URL found in response")
        except Exception as e:
            self.error.emit(str(e))


SUBTITLE_LANGS = ["en", "en-[A-Z]{2}", "en-orig"]


class YtDlpWorker(QThread):
    """
    Background worker thread for executing yt-dlp media downloads via the Python API.
    Emits rich dictionary metrics to supply detailed information to the YtDlpDownloadManager.
    """

    progress = Signal(int)
    progress_details = Signal(dict)
    done = Signal(bool, str)

    def __init__(self, url: str, download_dir: str, dl_opts: dict | None = None) -> None:
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.dl_opts = dl_opts if dl_opts is not None else {}
        self.is_cancelled = False
        self._last_emit_time = 0.0
        self._files: set = set()
        self.errors: list = []

    def debug(self, msg: str) -> None:
        if self.is_cancelled:
            raise KeyboardInterrupt("Download aborted by user.")
        log.debug("yt-dlp: %s", msg)

    info = debug

    def warning(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)
        self.debug("")

    def error(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)
        text = re.sub(r"\x1b\[[0-9;]*m", "", str(msg)).strip()
        text = re.sub(r"^ERROR:\s*", "", text)
        if text and not text.startswith("Traceback"):
            self.errors.append(text)

    def run(self) -> None:
        """Runs the yt-dlp download with the options the user chose."""
        is_playlist = bool(self.dl_opts.get("playlist", False))
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "logger": self,
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
            ydl_opts["subtitleslangs"] = SUBTITLE_LANGS
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
                self.done.emit(False, "Download cancelled.")
            elif self.errors:
                self.done.emit(False, self.failure_message())
            else:
                self.done.emit(True, "Download complete!")

        except KeyboardInterrupt:
            self.cleanup_remnants()
            self.done.emit(False, "Download cancelled.")

        except Exception as e:
            self.cleanup_remnants()
            if self.is_cancelled:
                self.done.emit(False, "Download cancelled.")
            else:
                self.done.emit(False, str(e))

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

                            re.sub(r"\x1b\[[0-9;]*m", "", d.get("_percent_str", "0%"))
                            .replace("%", "")
                            .strip()

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

    def failure_message(self) -> str:
        if len(self.errors) == 1:
            return f"Download failed: {self.errors[0]}"
        return f"{len(self.errors)} downloads failed. Last error: {self.errors[-1]}"

    def stop(self) -> None:
        """
        Flags the thread for cancellation. The next progress tick or log line from
        yt-dlp raises KeyboardInterrupt, which ends the download.
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
