"""
One place for Riemann's QSettings.

app_settings returns the store the whole app shares. Its names ("Riemann",
"PDFReader") are kept from the first releases so existing installs keep their
settings. favourites_settings, browser_settings and favicon_cache are the
three smaller stores kept apart on purpose.

Keys read or written by more than one module are named here (the *_KEY
constants), with any default two modules must agree on. Their strings are what
existing installs have stored, so they never change; a key one module owns
stays a literal in that module. tests/test_settings_keys.py enforces both. string_list and set_string_list smooth over how QSettings returns
lists: None when unset, a bare string for a one-item list on some platforms,
and a list otherwise.

get_dialog_directory and save_last_directory remember the folder file dialogs
open in ("app/default_dir" when set, else the last folder used).

Defaults that used to be hard-coded with personal data (the autocomplete sites,
the AI subject labels) are user settings now; the defaults here are generic.
"""

import os
import urllib.parse

from PySide6.QtCore import QSettings, QStandardPaths

from .endpoints import DEFAULT_SEARCH_URL

ORGANISATION = "Riemann"
APPLICATION = "PDFReader"

POPULAR_SITES_KEY = "browser/popular_sites"
DEFAULT_POPULAR_SITES = [
    "google.com",
    "youtube.com",
    "wikipedia.org",
    "github.com",
    "arxiv.org",
    "scholar.google.com",
    "stackoverflow.com",
    "reddit.com",
    "mail.google.com",
    "linkedin.com",
    "chatgpt.com",
    "gemini.google.com",
]

AI_TAGS_KEY = "ai/subject_tags"
OCR_LANGUAGES_KEY = "ocr/languages"

DARK_MODE_KEY = "darkMode"
UI_SCALE_KEY = "app/ui_scale"
DEFAULT_UI_SCALE = 100
DEFAULT_DIR_KEY = "app/default_dir"
LAST_DIR_KEY = "app/last_dir"
VERTICAL_TABS_KEY = "app/vertical_tabs"
TABS_COLLAPSED_KEY = "app/tabs_collapsed"
AUTOSCROLL_SPEED_KEY = "app/autoscroll_speed"
DEFAULT_AUTOSCROLL_SPEED = 5
CLEAN_EXIT_KEY = "session/clean_exit"
HOMEPAGE_NAME_KEY = "homepage/custom_name"
NOTES_VISIBLE_KEY = "reader/notes_visible"
SIDEBAR_VISIBLE_KEY = "reader/sidebar_visible"
SIDEBAR_MODE_KEY = "reader/sidebar_mode"
OFFICE_PATH_KEY = "office/libreoffice_path"

SEARCH_URL_KEY = "browser/search_url"

FAVOURITES_STORE = "Favorites"
FAVOURITE_FOLDERS_KEY = "folders"
BROWSER_STORE = "BrowserSettings"
FAVICON_STORE = "FaviconCache"


def app_settings() -> QSettings:
    """The settings store shared by every part of Riemann."""
    return QSettings(ORGANISATION, APPLICATION)


def favourites_settings() -> QSettings:
    """The favourite folders, kept apart from app_settings."""
    return QSettings(ORGANISATION, FAVOURITES_STORE)


def browser_settings() -> QSettings:
    """Per-site browser state such as zoom levels."""
    return QSettings(ORGANISATION, BROWSER_STORE)


def favicon_cache() -> QSettings:
    """Site icons by host, for the start page when it is offline."""
    return QSettings(ORGANISATION, FAVICON_STORE)


def search_template() -> str:
    """
    The search engine setting: an http(s) address with {query} where the words
    go. Anything else falls back to the default.
    """
    template = str(app_settings().value(SEARCH_URL_KEY, DEFAULT_SEARCH_URL) or "")
    if "{query}" not in template or not template.startswith(("https://", "http://")):
        return DEFAULT_SEARCH_URL
    return template


def search_url(text: str) -> str:
    """The web search for text."""
    return search_template().replace("{query}", urllib.parse.quote_plus(text.strip()))


def string_list(key: str, default: list[str] | None = None) -> list[str]:
    """A list-of-strings setting, or a copy of default when unset or unreadable."""
    value = app_settings().value(key)
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return list(default or [])


def set_string_list(key: str, values: list[str]) -> None:
    """Stores a list of strings, dropping blanks and duplicates but keeping order."""
    seen: list[str] = []
    for v in values:
        v = str(v).strip()
        if v and v not in seen:
            seen.append(v)
    app_settings().setValue(key, seen)


def get_dialog_directory(settings: QSettings) -> str:
    """The folder a file dialog should start in."""
    default_dir = settings.value(DEFAULT_DIR_KEY, "", type=str)
    if default_dir and os.path.exists(default_dir):
        return default_dir
    last_dir = settings.value(LAST_DIR_KEY, "", type=str)
    if last_dir and os.path.exists(last_dir):
        return last_dir
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)


def save_last_directory(settings: QSettings, file_path: str) -> None:
    """Remembers the folder of file_path (or file_path itself, if a folder) for the next dialog."""
    if file_path:
        directory = os.path.dirname(file_path) if os.path.isfile(file_path) else file_path
        if os.path.exists(directory):
            settings.setValue(LAST_DIR_KEY, directory)
