"""
Browser scripts and injection handlers.

This module provides utilities for injecting custom JavaScript into
QWebEngine profiles to modify web page behavior and appearance.

WhatsApp Web refuses browsers it doesn't recognise, so Riemann presents a
regular Chrome there, twice: RequestInterceptor sends WHATSAPP_HEADER_UA as
the User-Agent header and inject_whatsapp_ua makes navigator.userAgent return
WHATSAPP_NAVIGATOR_UA. The two have always differed (Linux Chrome 144 against
Windows Chrome 124); they are kept as they were until someone checks which
WhatsApp accepts.
"""

import base64
import json

from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript
from ..core.assets import asset_path

WHATSAPP_HOST = "whatsapp.com"
WHATSAPP_HEADER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.7559.59 Safari/537.36"
)
WHATSAPP_NAVIGATOR_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def get_injection_script(filename: str) -> str:
    """
    The contents of a script in assets (in the source tree or a PyInstaller bundle), or
    an empty string if it can't be read.
    """
    script_path = asset_path("injections", filename)

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
        """Adds or removes the smart dark mode script on one page."""
        if "monkeytype.com" in web_page.url().toString():
            return

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

        font_path = asset_path("fonts", "NotoColorEmoji.ttf")

        try:
            with open(font_path, "rb") as f:
                font_data = f.read()
            b64_font = base64.b64encode(font_data).decode("utf-8")
            data_uri = f"data:font/ttf;base64,{b64_font}"
            js_code = js_code.replace("{{FONT_URI}}", data_uri)
        except Exception as e:
            print(f"[Riemann Error] Failed to encode emoji font: {e}")
            return

        self._insert_script(
            "RiemannEmojiFallback",
            js_code,
            injection_point=QWebEngineScript.InjectionPoint.DocumentReady,
            world_id=QWebEngineScript.ScriptWorldId.UserWorld,
        )

    def inject_whatsapp_ua(self) -> None:
        """Injects a modern Chrome User-Agent strictly for WhatsApp to bypass block screens."""
        js_code = f"""
        if (window.location.hostname.includes({json.dumps(WHATSAPP_HOST)})) {{
            Object.defineProperty(navigator, 'userAgent', {{
                get: function () {{ return {json.dumps(WHATSAPP_NAVIGATOR_UA)}; }}
            }});
        }}
        """
        self._insert_script(
            "RiemannWhatsAppUA",
            js_code,
            injection_point=QWebEngineScript.InjectionPoint.DocumentCreation,
            world_id=QWebEngineScript.ScriptWorldId.MainWorld,
        )

    def _insert_script(
        self,
        name: str,
        source: str,
        injection_point=QWebEngineScript.InjectionPoint.DocumentCreation,
        world_id=QWebEngineScript.ScriptWorldId.ApplicationWorld,
    ):
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
