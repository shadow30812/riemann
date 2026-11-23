"""
The web browser: BrowserTab and the parts around it.

tab.py is BrowserTab; its behaviour is split into mixins by subject (chrome,
media, stream, downloads). interceptor.py decides what pages may load (the
start page, local files, the ad blocker), page.py is the tab's
QWebEnginePage, ytdlp.py the yt-dlp workers and preview.py the light view of
Markdown, HTML and text files. This module re-exports what the rest of the
app imports.
"""

from .interceptor import (
    BLOCKED_DOMAINS,
    HOMEPAGE_PATH,
    RequestInterceptor,
    is_blocked,
    is_local_homepage,
    local_load_allowed,
    sanitize_homepage_links,
)
from .page import CAPTIONS_WORLD, WebPage, apply_content_settings, owning_tab
from .preview import PreviewTextTab
from .tab import BrowserTab
from .ytdlp import YtDlpSettingsDialog, YtDlpStreamWorker, YtDlpWorker, js_runtimes

__all__ = [
    "BLOCKED_DOMAINS",
    "CAPTIONS_WORLD",
    "HOMEPAGE_PATH",
    "BrowserTab",
    "PreviewTextTab",
    "RequestInterceptor",
    "WebPage",
    "YtDlpSettingsDialog",
    "YtDlpStreamWorker",
    "YtDlpWorker",
    "apply_content_settings",
    "is_blocked",
    "is_local_homepage",
    "js_runtimes",
    "local_load_allowed",
    "owning_tab",
    "sanitize_homepage_links",
]
