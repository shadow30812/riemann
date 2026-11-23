"""
Live captions, music mode, the video speed controller, mute and the tab's playing state.

The caption engine runs in Chromium's ApplicationWorld (CAPTIONS_WORLD), an
isolated world that shares the page's DOM but not its JavaScript globals, so
the captions server's port and per-run token are invisible to the site's own
scripts.
"""

import json
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QTabWidget

from ...core.assets import asset_path
from .page import CAPTIONS_WORLD


class MediaMixin:
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
            with open(candidate_path, encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[ERROR] Failed to load captions script: {e}")
            return ""

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

            with open(candidate_path, encoding="utf-8") as f:
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

            with open(candidate_path, encoding="utf-8") as f:
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

    def toggle_mute(self) -> None:
        """Mutes or unmutes the audio output specifically for this web tab."""
        is_muted = self.btn_mute.isChecked()
        self.web.page().setAudioMuted(is_muted)

        icon = "volume-x.svg" if is_muted else "volume-on.svg"
        self.btn_mute.setIcon(self._get_icon(icon))

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
