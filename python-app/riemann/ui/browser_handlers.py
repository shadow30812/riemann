"""
Browser scripts and injection handlers.

This module provides utilities for injecting custom JavaScript into
QWebEngine profiles to modify web page behavior and appearance.
"""

import os
import sys
import urllib.parse

from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript


def get_injection_script(filename: str) -> str:
    """
    Safely resolves the path to an injection script, accounting for PyInstaller's
    temporary MEIPASS directory, and returns its contents.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = getattr(sys, "_MEIPASS")
        script_path = os.path.join(
            base_path, "riemann", "assets", "injections", filename
        )
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(base_path, "assets", "injections", filename)

    try:
        with open(script_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[Riemann Error] Could not load injection script {filename}: {e}")
        return ""


class ScriptInjector:
    """
    Handles injection of JavaScript into a QWebEngineProfile.
    """

    def __init__(self, profile: QWebEngineProfile):
        """
        Initializes the ScriptInjector with a target profile.

        Args:
            profile (QWebEngineProfile): The web engine profile to receive injected scripts.
        """
        self.profile = profile

    def inject_ad_skipper(self) -> None:
        """
        Injects a script to automatically skip or fast-forward video advertisements.
        """
        js_code = get_injection_script("ad_skipper.js")
        if js_code:
            self._insert_script("RiemannAdBlock", js_code)

    def inject_backspace_handler(self) -> None:
        """
        Injects a script to handle the Backspace key for navigation, ensuring it
        does not trigger back navigation when typing in input fields.
        """
        js_code = get_injection_script("backspace_handler.js")
        if js_code:
            self._insert_script("RiemannBackspace", js_code)

    def inject_smart_dark_mode(self, web_page, dark_mode: bool) -> None:
        """
        Injects or removes a smart dark mode CSS inversion script on a specific web page.

        Args:
            web_page: The QWebEnginePage instance to inject the script into.
            dark_mode (bool): True to enable dark mode, False to disable it.
        """
        if dark_mode:
            js = get_injection_script("smart_dark_mode.js")
        else:
            js = "var el = document.getElementById('riemann-dark'); if(el) el.remove();"

        if js:
            self._insert_script(
                "RiemannSmartDark",
                js,
                injection_point=QWebEngineScript.InjectionPoint.DocumentReady,
                world_id=QWebEngineScript.ScriptWorldId.UserWorld,
            )
            try:
                web_page.runJavaScript(js, QWebEngineScript.ScriptWorldId.UserWorld)
            except Exception:
                web_page.runJavaScript(js)

    def inject_emoji_fallback(self) -> None:
        """
        Injects a CSS font-face and global rule to ensure emojis and symbols
        always have a valid fallback font by referencing the bundled Noto font.
        """
        js_code = get_injection_script("emoji_fallback.js")
        if not js_code:
            return

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_path = getattr(sys, "_MEIPASS")
            font_path = os.path.join(
                base_path, "riemann", "assets", "fonts", "NotoColorEmoji.ttf"
            )
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            font_path = os.path.join(base_path, "assets", "fonts", "NotoColorEmoji.ttf")

        font_uri = "file:///" + urllib.parse.quote(font_path.replace("\\", "/"))
        js_code = js_code.replace("{{FONT_URI}}", font_uri)

        self._insert_script(
            "RiemannEmojiFallback",
            js_code,
            injection_point=QWebEngineScript.InjectionPoint.DocumentReady,
            world_id=QWebEngineScript.ScriptWorldId.UserWorld,
        )

    def _insert_script(
        self,
        name: str,
        source: str,
        injection_point=QWebEngineScript.InjectionPoint.DocumentCreation,
        world_id=QWebEngineScript.ScriptWorldId.ApplicationWorld,
    ):
        """
        Helper method to configure and insert a QWebEngineScript into the profile.

        Args:
            name (str): The unique identifier for the script.
            source (str): The JavaScript source code.
            injection_point: When the script should run based on Qt Injection Points.
            world_id: The isolation world for the script execution.
        """
        scripts = self.profile.scripts()
        for existing_script in scripts.toList():
            if existing_script.name() == name:
                scripts.remove(existing_script)

        script = QWebEngineScript()
        script.setName(name)
        script.setSourceCode(source)
        script.setInjectionPoint(injection_point)
        script.setWorldId(world_id)
        script.setRunsOnSubFrames(True)
        scripts.insert(script)
