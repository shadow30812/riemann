"""
Live captions, music mode, the video speed controller, mute and the page's playing state.

MediaControls works on one QWebEngineView and owns no buttons: the tab says
whether a mode should be on and shows what comes back. Messages go out
through toast. playing_changed carries "playing" when the page makes sound
and None 1.5 s after it falls silent, for the speaker mark on the tab bar.
Music mode is injected again after each page load while it is on. The music
and video engines are looked for in assets, then beside the executable.

The caption engine runs in Chromium's ApplicationWorld (CAPTIONS_WORLD), an
isolated world that shares the page's DOM but not its JavaScript globals, so
the captions server's port and per-run token are invisible to the site's own
scripts.
"""

import json
import os
import sys
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView

from ...core.assets import asset_path
from .page import CAPTIONS_WORLD

SILENCE_MS = 1500
MUSIC_RESTORE_MS = 1000


def captions_script() -> str:
    try:
        with open(asset_path("caption_engine.js"), encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[ERROR] Failed to load captions script: {e}")
        return ""


def engine_script(name: str) -> str | None:
    """The text of assets/name ("" when it can't be read), or None when the file is missing."""
    try:
        path = asset_path(name)
        if not os.path.exists(path):
            path = os.path.join(os.path.dirname(sys.executable), "riemann", "assets", name)
        if not os.path.exists(path):
            print(f"[Riemann Error] Engine not found at: {path}")
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[ERROR] Failed to load {name}: {e}")
        return ""


class MediaControls(QObject):
    toast = Signal(str)
    playing_changed = Signal(object)

    def __init__(self, web: QWebEngineView, captions_server: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.web = web
        self.captions_server = captions_server
        self.music_on = False
        self.video_on = False
        self._silence = QTimer(self)
        self._silence.setSingleShot(True)
        self._silence.setInterval(SILENCE_MS)
        self._silence.timeout.connect(lambda: self.playing_changed.emit(None))
        web.page().recentlyAudibleChanged.connect(self._on_audible_changed)
        web.loadFinished.connect(self._restore_music_mode)

    def set_captions(self, on: bool) -> None:
        """Turns the live captioning overlay on the page on or off."""
        if on:
            server = self.captions_server
            port = server.port if server else 8765
            token = getattr(server, "token", "") if server else ""
            self.web.page().runJavaScript(
                f"""
                window.RIEMANN_CAPTIONS_PORT = {port};
                window.RIEMANN_CAPTIONS_TOKEN = {json.dumps(token)};
                {captions_script()}
                if (window.RiemannCaptions) window.RiemannCaptions.enable();
            """,
                CAPTIONS_WORLD,
            )
            self.toast.emit("Live Captions Enabled")
        else:
            self.web.page().runJavaScript(
                "if (window.RiemannCaptions) window.RiemannCaptions.disable();", CAPTIONS_WORLD
            )
            self.toast.emit("Live Captions Disabled")

    def _set_mode(self, script: str, api: str, label: str, on: bool) -> bool:
        base = engine_script(script)
        if base is None:
            self.toast.emit(f"Error: Missing {script}")
        if not base:
            return False
        action = "enable" if on else "disable"
        self.toast.emit(f"{label} {'ON' if on else 'OFF'}")
        self.web.page().runJavaScript(f"{base}\nif(window.{api}) window.{api}.{action}();")
        return on

    def set_music_mode(self, on: bool) -> bool:
        """Turns music mode on or off; whether it is on afterwards (off when its script is missing)."""
        self.music_on = self._set_mode("audio_engine.js", "RiemannAudio", "Music Mode", on)
        return self.music_on

    def set_video_mode(self, on: bool) -> bool:
        """Turns the video speed controller on or off; whether it is on afterwards."""
        self.video_on = self._set_mode("video_engine.js", "RiemannVideo", "Video Mode", on)
        return self.video_on

    def set_muted(self, muted: bool) -> None:
        self.web.page().setAudioMuted(muted)

    def _restore_music_mode(self) -> None:
        if self.music_on:
            QTimer.singleShot(MUSIC_RESTORE_MS, self, lambda: self.set_music_mode(True))

    def _on_audible_changed(self, audible: bool) -> None:
        if audible:
            self._silence.stop()
            self.playing_changed.emit("playing")
        else:
            self._silence.start()
