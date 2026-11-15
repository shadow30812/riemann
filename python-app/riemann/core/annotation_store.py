"""
Where Riemann keeps its own copy of each document's annotations.

Annotations are written into the PDF when possible, but Riemann also keeps a
JSON copy: for signed, encrypted and read-only files it is the only copy, and
for the rest it records what was last written so edits made in other apps can
be merged (see riemann.ui.reader.mixins.annotations).

The copies live in <app data>/annotations, the same folder family as history
and bookmarks. Each is named by a key:

- "id-<hex>" from the PDF's permanent identifier (the first /ID string in the
  trailer). It survives renames and moves, and Riemann's incremental saves
  keep it, so a moved paper keeps its notes.
- When a second file with the same identifier appears while the first still
  exists, it is a copy: it gets "id-<hex>-<path hash>", starting from the
  original's annotations and diverging from there.
- Files without an identifier (and non-PDF documents) use "path-<hash>" and
  lose their notes when moved outside Riemann, as before.

index.json maps each path to its key, which makes repeat lookups cheap and
lets a file overwritten by a different document (new identifier) start fresh.

Copies from older versions sat in ~/.local/share/riemann/annotations named by
a hash of the path; they are moved here the first time their file is opened.
"""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from .storage import app_data_dir, write_json_atomic

_ID = re.compile(rb"/ID\s*\[\s*<\s*([0-9A-Fa-f\s]+?)\s*>")
_SCAN = 256 * 1024


def pdf_identifier(path: str) -> Optional[str]:
    """The first /ID string of the PDF's newest trailer, as lower-case hex."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - _SCAN))
            tail = f.read()
            head = b""
            if size > _SCAN:
                f.seek(0)
                head = f.read(_SCAN)
    except OSError:
        return None
    for chunk in (tail, head):
        found = _ID.findall(chunk)
        if found:
            ident = re.sub(rb"\s+", b"", found[-1]).decode("ascii").lower()
            if ident and set(ident) != {"0"}:
                return ident[:64]
    return None


def _path_hash(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def legacy_json_path(pdf_path: str) -> str:
    """Where versions up to 5.2 kept the copy: a hash of the path, in a lower-case folder."""
    return str(Path.home() / ".local" / "share" / "riemann" / "annotations" / f"{_path_hash(pdf_path)}.json")


class AnnotationStore:
    """Maps document paths to their annotation JSON files."""

    def __init__(self, folder: Optional[str] = None) -> None:
        self.folder = folder or os.path.join(app_data_dir(), "annotations")
        self.index_path = os.path.join(self.folder, "index.json")

    def _file(self, key: str) -> str:
        return os.path.join(self.folder, f"{key}.json")

    def _load_index(self) -> dict:
        try:
            with open(self.index_path, encoding="utf-8") as f:
                data = json.load(f)
            paths = data.get("paths") if isinstance(data, dict) else None
            return {str(k): str(v) for k, v in (paths or {}).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_index(self, index: dict) -> None:
        try:
            write_json_atomic(self.index_path, {"paths": index}, indent=1)
        except OSError:
            pass

    def json_path(self, pdf_path: str) -> str:
        """The JSON file for a document, creating index entries and migrating as needed."""
        if not pdf_path:
            return ""
        path = os.path.abspath(pdf_path)
        os.makedirs(self.folder, exist_ok=True)
        index = self._load_index()
        ident = pdf_identifier(path) if path.lower().endswith(".pdf") else None
        known = index.get(path)

        if known and (ident is None or known.startswith(f"id-{ident}")):
            key = known
        elif ident:
            key = f"id-{ident}"
            others = [p for p, k in index.items() if k == key and p != path]
            if any(os.path.exists(p) for p in others):
                key = f"id-{ident}-{_path_hash(path)[:10]}"
                if not os.path.exists(self._file(key)) and os.path.exists(self._file(f"id-{ident}")):
                    shutil.copyfile(self._file(f"id-{ident}"), self._file(key))
            else:
                for p in others:
                    index.pop(p, None)
        else:
            key = f"path-{_path_hash(path)[:32]}"

        target = self._file(key)
        legacy = legacy_json_path(path)
        if not os.path.exists(target) and os.path.exists(legacy):
            try:
                shutil.move(legacy, target)
            except OSError:
                pass
        if index.get(path) != key:
            index[path] = key
            self._save_index(index)
        return target

    def moved(self, old_path: str, new_path: str) -> None:
        """Records that Riemann renamed or moved a document."""
        index = self._load_index()
        key = index.pop(os.path.abspath(old_path), None)
        if key is None:
            return
        index[os.path.abspath(new_path)] = key
        self._save_index(index)


_shared: Optional[AnnotationStore] = None


def shared() -> AnnotationStore:
    global _shared
    if _shared is None:
        _shared = AnnotationStore()
    return _shared
