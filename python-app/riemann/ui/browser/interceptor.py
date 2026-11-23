"""
What browser pages may load: the start page, local files and the ad blocker.

The start page is assets/homepage/homepage.html, a local file. It saves its
quick links by loading riemann-save URLs in a hidden frame. Those are only
honoured while the page showing them is that exact file, and the links must
parse as a list of {name, url} strings (sanitize_homepage_links); they are
stored re-serialised and handed back to the page as JSON. Any web page (or an
ad in a frame) could otherwise store script that ran in the homepage, which as
a local file can read other local files.

Local HTML files keep LocalContentCanAccessFileUrls on, because without it a
saved page cannot even load its own stylesheet, but that setting also lets the
page fetch() or frame any file on disk. RequestInterceptor narrows it with
local_load_allowed: a local page may load scripts, styles, images, fonts and
media from its own folder and below, and nothing else from disk. The start page
may load anything under riemann/assets. Remote URLs are filtered the same
way rather than with LocalContentCanAccessRemoteUrls, which, when off, also
stopped the start page showing its site icons: a local page may navigate to a
remote page, the start page may load remote images (the icons), and nothing
else, so a local page cannot send what it reads to a server with fetch().
Each profile owns one RequestInterceptor, shared by its tabs: the profile only
keeps a weak pointer, so an interceptor owned by a tab vanished when that tab
closed and left every other tab on the profile unfiltered.

The ad blocker matches hosts by suffix (ads.example.org blocks
x.ads.example.org, not loads.example.org). interceptRequest runs on
Chromium's IO thread, so it only counts blocked requests; the count is added
to the stored total from the UI thread every BLOCK_COUNT_FLUSH_MS, since a
QSettings store must not be written from two threads.
"""

import json
import os
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor

from ...core.assets import asset_path
from ...core.settings import app_settings
from ..browser_handlers import WHATSAPP_HEADER_UA, WHATSAPP_HOST

HOMEPAGE_PATH = os.path.abspath(asset_path("homepage", "homepage.html"))


def is_local_homepage(url: QUrl) -> bool:
    """Whether url is Riemann's own start page file."""
    if not url.isLocalFile():
        return False
    return os.path.normcase(os.path.abspath(url.toLocalFile())) == os.path.normcase(HOMEPAGE_PATH)


ASSETS_DIR = os.path.dirname(os.path.dirname(HOMEPAGE_PATH))


_REMOTE_SCHEMES = {"http", "https", "ws", "wss", "ftp"}


_ICON_TYPES = {
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeFavicon,
}


_STATIC_TYPES = {
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeStylesheet,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeScript,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeFontResource,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeFavicon,
}


def _inside(path: str, folder: str) -> bool:
    path = os.path.normcase(os.path.realpath(path))
    folder = os.path.normcase(os.path.realpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def local_load_allowed(first_party: QUrl, request: QUrl, resource_type) -> bool:
    """Whether a page may load request. Only local pages and file: requests are restricted."""
    if resource_type == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame:
        return True
    if first_party.isLocalFile() and request.scheme().lower() in _REMOTE_SCHEMES:
        return is_local_homepage(first_party) and resource_type in _ICON_TYPES
    if not request.isLocalFile():
        return True
    if not first_party.isLocalFile():
        return False
    target = request.toLocalFile()
    if is_local_homepage(first_party):
        return _inside(target, ASSETS_DIR)
    if resource_type not in _STATIC_TYPES:
        return False
    return _inside(target, os.path.dirname(first_party.toLocalFile()))


def sanitize_homepage_links(payload: Any) -> str | None:
    """The homepage's quick links as JSON, or None when payload is not a list of {name, url} strings."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else None
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    links = [
        {"name": item["name"][:200], "url": item["url"][:2048]}
        for item in data[:500]
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("url"), str)
    ]
    return json.dumps(links)


BLOCKED_DOMAINS = (
    "doubleclick.net",
    "googleadservices.com",
    "googlesyndication.com",
    "adservice.google.com",
    "pagead2.googlesyndication.com",
    "tpc.googlesyndication.com",
    "youtube.com/ptracking",
    "youtube.com/pagead",
    "google-analytics.com",
    "dmxleo.com",
)


ADS_BLOCKED_KEY = "browser/ads_blocked"


BLOCK_COUNT_FLUSH_MS = 5000


def host_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def is_blocked(url: QUrl, rules) -> bool:
    """
    True when url's host is a listed domain or below it; a rule with a path ("youtube.com/pagead") also needs that
    path prefix.
    """
    host, path = url.host(), url.path()
    for rule in rules:
        domain, _, prefix = rule.partition("/")
        if host_matches(host, domain) and (not prefix or path.startswith("/" + prefix)):
            return True
    return False


class RequestInterceptor(QWebEngineUrlRequestInterceptor):
    """
    Handles AdBlocking by intercepting network requests to known advertising
    and tracking domains, User Agent spoofing for WhatsApp,
    and surgical Header injection for Monkeytype/Firebase auth.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.blocked_domains = list(BLOCKED_DOMAINS)
        self.spoofed_ua = WHATSAPP_HEADER_UA.encode()
        self.blocked_count = 0
        self._flushed_count = 0
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(BLOCK_COUNT_FLUSH_MS)
        self._flush_timer.timeout.connect(self.flush_block_count)
        self._flush_timer.start()
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.flush_block_count)

    def flush_block_count(self) -> None:
        """Adds the requests blocked since the last flush to the stored total; runs on the UI thread."""
        count = self.blocked_count
        delta = count - self._flushed_count
        if delta <= 0:
            return
        self._flushed_count = count
        settings = app_settings()
        settings.setValue(ADS_BLOCKED_KEY, settings.value(ADS_BLOCKED_KEY, 0, type=int) + delta)

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        """Blocks requests to listed domains and adjusts headers where a site needs it."""
        if (
            info.resourceType()
            == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeServiceWorker
        ):
            info.block(True)
            return

        if not local_load_allowed(info.firstPartyUrl(), info.requestUrl(), info.resourceType()):
            info.block(True)
            return

        url = info.requestUrl()
        if is_blocked(url, self.blocked_domains):
            info.block(True)
            self.blocked_count += 1
            return

        if host_matches(url.host(), WHATSAPP_HOST):
            info.setHttpHeader(b"User-Agent", self.spoofed_ua)
