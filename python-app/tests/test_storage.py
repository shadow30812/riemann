import json
import os

import pytest

from riemann.core import storage


def test_write_json_atomic_replaces_file_and_leaves_no_temp(tmp_path):
    path = tmp_path / "sub" / "history.json"
    storage.write_json_atomic(str(path), {"a": 1})
    storage.write_json_atomic(str(path), {"a": 2}, indent=2)
    assert json.loads(path.read_text()) == {"a": 2}
    assert os.listdir(path.parent) == ["history.json"]


def test_failed_write_keeps_previous_file(tmp_path):
    path = tmp_path / "bookmarks.json"
    storage.write_json_atomic(str(path), [1, 2, 3])
    with pytest.raises(TypeError):
        storage.write_json_atomic(str(path), {"bad": object()})
    assert json.loads(path.read_text()) == [1, 2, 3]
    assert os.listdir(tmp_path) == ["bookmarks.json"]


def test_managers_work_on_a_fresh_install(tmp_path, monkeypatch):
    from unittest.mock import patch

    target = tmp_path / "does" / "not" / "exist"
    with patch("riemann.core.storage.QStandardPaths.writableLocation", return_value=str(target)):
        from riemann.core.managers import BookmarksManager, LibraryManager

        LibraryManager()
        bookmarks = BookmarksManager()
        bookmarks.add("Example", "https://example.com")
    assert (target / "bookmarks.json").exists()
    assert (target / "riemann_library.db").exists()
