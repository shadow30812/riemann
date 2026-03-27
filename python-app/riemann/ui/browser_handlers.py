"""
Browser scripts and injection handlers.

This module provides utilities for injecting custom JavaScript into
QWebEngine profiles to modify web page behavior and appearance.
"""

import os
import sys
import urllib.parse

from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript


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
        js_code = """
        (function() {
            const clearAds = () => {
                const skipBtns = document.querySelectorAll('.ytp-ad-skip-button, .ytp-ad-skip-button-modern, .videoAdUiSkipButton');
                skipBtns.forEach(b => { b.click(); });
                const overlays = document.querySelectorAll('.ytp-ad-overlay-close-button');
                overlays.forEach(b => { b.click(); });
                const video = document.querySelector('video');
                const adShowing = document.querySelector('.ad-showing');
                if (video && adShowing) {
                    video.playbackRate = 16.0;
                    video.muted = true;
                    if(isFinite(video.duration)) video.currentTime = video.duration;
                }
            };
            setInterval(clearAds, 50);
        })();
        """
        self._insert_script("RiemannAdBlock", js_code)

    def inject_backspace_handler(self) -> None:
        """
        Injects a script to handle the Backspace key for navigation, ensuring it
        does not trigger back navigation when typing in input fields.
        """
        js_code = """
        document.addEventListener("keydown", function(e) {
            if (e.key === "Backspace" && !e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
                const tag = document.activeElement.tagName;
                const type = document.activeElement.type;
                const isInput = (tag === "INPUT" && type !== "button" && type !== "submit" && type !== "checkbox" && type !== "radio")
                                || tag === "TEXTAREA"
                                || document.activeElement.isContentEditable;
                if (!isInput) {
                    e.preventDefault();
                    window.history.back();
                }
            }
        });
        """
        self._insert_script("RiemannBackspace", js_code)

    def inject_smart_dark_mode(self, web_page, is_dark_mode: bool) -> None:
        """
        Injects or removes a smart dark mode CSS inversion script on a specific web page.

        Args:
            web_page: The QWebEnginePage instance to inject the script into.
            is_dark_mode (bool): True to enable dark mode, False to disable it.
        """
        if is_dark_mode:
            js = """
            (function() {
                var existing = document.getElementById('riemann-dark');
                if (existing) existing.remove();
                function getBrightness(elem) {
                    var style = window.getComputedStyle(elem);
                    var color = style.backgroundColor;
                    if (color === 'rgba(0, 0, 0, 0)' || color === 'transparent') return 255;
                    var rgb = color.match(/\\d+/g);
                    if (!rgb) return 255;
                    var r = parseInt(rgb[0]), g = parseInt(rgb[1]), b = parseInt(rgb[2]);
                    return (0.299 * r + 0.587 * g + 0.114 * b);
                }
                var bodyB = getBrightness(document.body);
                var htmlB = getBrightness(document.documentElement);
                if (!(bodyB < 140 || htmlB < 140)) {
                    var css = `html { filter: invert(1) hue-rotate(180deg) !important; }
                               img, video, iframe, canvas, :fullscreen { filter: invert(1) hue-rotate(180deg) !important; }`;
                    var style = document.createElement('style');
                    style.id = 'riemann-dark';
                    style.innerHTML = css;
                    document.head.appendChild(style);
                }
            })();
            """
        else:
            js = "var el = document.getElementById('riemann-dark'); if(el) el.remove();"

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
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_path = getattr(sys, "_MEIPASS")
            font_path = os.path.join(
                base_path, "riemann", "assets", "fonts", "NotoColorEmoji.ttf"
            )
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            font_path = os.path.join(base_path, "assets", "fonts", "NotoColorEmoji.ttf")

        font_uri = "file:///" + urllib.parse.quote(font_path.replace("\\", "/"))

        js = f"""
        (function() {{
            if (document.getElementById('riemann-emoji-fallback')) return;
            
            var css = `
                @font-face {{
                    font-family: "Riemann Noto Emoji";
                    src: url("{font_uri}") format("truetype");
                }}
                body, p, span, div, h1, h2, h3, h4, h5, h6, input, textarea {{ 
                    font-family: inherit, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Riemann Noto Emoji", "Twemoji Mozilla" !important; 
                }}
            `;
            var style = document.createElement('style');
            style.id = 'riemann-emoji-fallback';
            style.innerHTML = css;
            document.documentElement.appendChild(style);
        }})();
        """
        self._insert_script(
            "RiemannEmojiFallback",
            js,
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
        self.profile.scripts().insert(script)
