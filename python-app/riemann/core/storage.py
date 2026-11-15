"""
Safe persistence helpers for Riemann's own data files.

write_json_atomic writes to a temporary file in the same folder, flushes it to
disk and renames it over the target. A crash or power cut mid-write then leaves
the previous file intact instead of a truncated one, which the loaders would
treat as corrupt and replace with an empty list (losing history, bookmarks or
annotations).

write_text_atomic does the same for plain text (notes files).

app_data_dir returns Qt's AppDataLocation and creates it, so the first save on
a fresh install does not fail because the folder does not exist yet.
"""

import json
import os
import tempfile
from typing import Any

from PySide6.QtCore import QStandardPaths


def app_data_dir() -> str:
    """The application data folder, created if missing."""
    path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    os.makedirs(path, exist_ok=True)
    return path


def write_json_atomic(path: str, data: Any, **dump_kwargs: Any) -> None:
    """
    Replaces path with data as JSON, atomically. Raises OSError if the folder can't be
    created or the file can't be written.
    """
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text_atomic(path: str, text: str) -> None:
    """Replaces path with text (UTF-8), atomically, like write_json_atomic."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".txt", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
