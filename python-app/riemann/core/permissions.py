"""
Site permissions for the browser: what each website may do, with defaults.

Two kinds of setting live here:

- Permissions a page asks for at the moment it needs them (camera,
  microphone, screen sharing, location, notifications, clipboard, pointer lock,
  local fonts). Qt reports these through QWebEnginePage.permissionRequested;
  the browser answers from this store, or asks the user when the answer is
  "ask".
- Content settings applied to a page before it loads (pop-ups, autoplay,
  JavaScript, images, insecure content on secure pages).

Every setting has a default, and any site (scheme://host[:port]) can override
it. Defaults favour safety where a mistake costs privacy (capture devices,
location, reading the clipboard: ask; pop-ups and insecure content: block) and
convenience where it does not (autoplay, JavaScript and images: allow).

Copying from a page with a click always works in Chromium without any
permission; "clipboard" here covers reading the clipboard and writing to it
without a click.

The store is a JSON file in the app data folder, written atomically. An
incognito window gets a SitePermissions with persist=False: it reads the saved
decisions but keeps its own in memory and forgets them when closed.
"""

import json
import os
from dataclasses import dataclass

from PySide6.QtCore import QObject, QUrl, Signal

from .storage import app_data_dir, write_json_atomic

ASK = "ask"
ALLOW = "allow"
BLOCK = "block"


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    request: str
    default: str
    choices: tuple = (ASK, ALLOW, BLOCK)


KINDS = [
    Kind("camera", "Camera", "use your camera", ASK),
    Kind("microphone", "Microphone", "use your microphone", ASK),
    Kind("screen", "Screen sharing", "see your screen", ASK),
    Kind("location", "Location", "know your location", ASK),
    Kind("notifications", "Notifications", "show notifications", ASK),
    Kind("clipboard", "Clipboard", "see and change what you copy", ASK),
    Kind("pointer_lock", "Pointer lock", "hide and lock your mouse pointer", ASK),
    Kind("local_fonts", "Local fonts", "use the fonts installed on your computer", ASK),
    Kind("popups", "Pop-ups and redirects", "open pop-up windows", BLOCK, (ALLOW, BLOCK)),
    Kind("autoplay", "Autoplay", "play sound and video on its own", ALLOW, (ALLOW, BLOCK)),
    Kind("javascript", "JavaScript", "run JavaScript", ALLOW, (ALLOW, BLOCK)),
    Kind("images", "Images", "show images", ALLOW, (ALLOW, BLOCK)),
    Kind("insecure_content", "Insecure content", "load insecure content", BLOCK, (ALLOW, BLOCK)),
]
KIND_BY_KEY = {k.key: k for k in KINDS}
PROMPTED = [k.key for k in KINDS if ASK in k.choices]
CONTENT = [k.key for k in KINDS if ASK not in k.choices]


def site_of(url) -> str:
    """The site key for a URL: scheme://host[:port], lower case; '' when there is none."""
    q = url if isinstance(url, QUrl) else QUrl(str(url))
    scheme = q.scheme().lower()
    host = q.host().lower()
    if scheme not in ("http", "https") or not host:
        return ""
    port = q.port()
    default_port = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" + (f":{port}" if port not in (-1, default_port) else "")


class SitePermissions(QObject):
    """Per-site decisions on top of user-adjustable defaults."""

    changed = Signal()

    def __init__(self, path: str | None = None, persist: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.path = path or os.path.join(app_data_dir(), "site_permissions.json")
        self.persist = persist
        self._defaults: dict[str, str] = {}
        self._sites: dict[str, dict[str, str]] = {}
        self._session: dict[str, dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._defaults = {
            k: v for k, v in (data.get("defaults") or {}).items() if self._valid(k, v)
        }
        self._sites = {}
        for site, entries in (data.get("sites") or {}).items():
            if isinstance(site, str) and isinstance(entries, dict):
                clean = {k: v for k, v in entries.items() if self._valid(k, v)}
                if clean:
                    self._sites[site] = clean

    def save(self) -> None:
        if not self.persist:
            return
        try:
            write_json_atomic(self.path, {"defaults": self._defaults, "sites": self._sites}, indent=2)
        except OSError:
            pass

    @staticmethod
    def _valid(key, value) -> bool:
        kind = KIND_BY_KEY.get(key)
        return kind is not None and value in kind.choices

    def default(self, key: str) -> str:
        return self._defaults.get(key, KIND_BY_KEY[key].default)

    def set_default(self, key: str, value: str) -> None:
        if not self._valid(key, value):
            raise ValueError(f"{value!r} is not a choice for {key!r}")
        if value == KIND_BY_KEY[key].default:
            self._defaults.pop(key, None)
        else:
            self._defaults[key] = value
        self.save()
        self.changed.emit()

    def get(self, url, key: str) -> str:
        """The effective setting for key on the site of url."""
        site = site_of(url)
        for table in (self._session, self._sites):
            value = table.get(site, {}).get(key)
            if value:
                return value
        return self.default(key)

    def set(self, url, key: str, value: str | None) -> None:
        """Stores a site decision; None removes it so the default applies again."""
        site = site_of(url)
        if not site:
            return
        if value is not None and not self._valid(key, value):
            raise ValueError(f"{value!r} is not a choice for {key!r}")
        table = self._sites if self.persist else self._session
        entries = table.setdefault(site, {})
        if value is None:
            entries.pop(key, None)
        else:
            entries[key] = value
        if not entries:
            table.pop(site, None)
        self.save()
        self.changed.emit()

    def clear_site(self, url) -> None:
        site = site_of(url) or str(url)
        removed = self._sites.pop(site, None) is not None
        removed = self._session.pop(site, None) is not None or removed
        if removed:
            self.save()
            self.changed.emit()

    def sites(self) -> dict[str, dict[str, str]]:
        """Every site with a stored decision, merged with this session's."""
        merged = {s: dict(e) for s, e in self._sites.items()}
        for site, entries in self._session.items():
            merged.setdefault(site, {}).update(entries)
        return dict(sorted(merged.items()))


_shared: SitePermissions | None = None


def shared() -> SitePermissions:
    """The store used by every normal (not incognito) browser tab."""
    global _shared
    if _shared is None:
        _shared = SitePermissions()
    return _shared


def kinds_for_permission(permission_type) -> list[str]:
    """The settings a QWebEnginePermission type maps to; empty when unsupported."""
    from PySide6.QtWebEngineCore import QWebEnginePermission

    T = QWebEnginePermission.PermissionType
    table = {
        T.MediaAudioCapture: ["microphone"],
        T.MediaVideoCapture: ["camera"],
        T.MediaAudioVideoCapture: ["camera", "microphone"],
        T.DesktopVideoCapture: ["screen"],
        T.DesktopAudioVideoCapture: ["screen"],
        T.Geolocation: ["location"],
        T.Notifications: ["notifications"],
        T.ClipboardReadWrite: ["clipboard"],
        T.MouseLock: ["pointer_lock"],
        T.LocalFontsAccess: ["local_fonts"],
    }
    return table.get(permission_type, [])


def decide(store: SitePermissions, url, keys: list[str]) -> str:
    """Combines several settings for one request: block wins, then ask, then allow."""
    if not keys:
        return BLOCK
    values = [store.get(url, k) for k in keys]
    if BLOCK in values:
        return BLOCK
    if ASK in values:
        return ASK
    return ALLOW
