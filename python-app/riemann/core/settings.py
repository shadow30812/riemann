"""
One place for Riemann's QSettings.

app_settings returns the store the whole app shares. Its names ("Riemann",
"PDFReader") are kept from the first releases so existing installs keep their
settings. string_list and set_string_list smooth over how QSettings returns
lists: None when unset, a bare string for a one-item list on some platforms,
and a list otherwise.

get_dialog_directory and save_last_directory remember the folder file dialogs
open in ("app/default_dir" when set, else the last folder used).

Defaults that used to be hard-coded with personal data (the autocomplete sites,
the AI subject labels) are user settings now; the defaults here are generic.
"""

import os

from PySide6.QtCore import QSettings, QStandardPaths

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


def app_settings() -> QSettings:
    """The settings store shared by every part of Riemann."""
    return QSettings(ORGANISATION, APPLICATION)


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
    default_dir = settings.value("app/default_dir", "", type=str)
    if default_dir and os.path.exists(default_dir):
        return default_dir
    last_dir = settings.value("app/last_dir", "", type=str)
    if last_dir and os.path.exists(last_dir):
        return last_dir
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)


def save_last_directory(settings: QSettings, file_path: str) -> None:
    """Remembers the folder of file_path (or file_path itself, if a folder) for the next dialog."""
    if file_path:
        directory = os.path.dirname(file_path) if os.path.isfile(file_path) else file_path
        if os.path.exists(directory):
            settings.setValue("app/last_dir", directory)
