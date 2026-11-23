import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from riemann.core.managers import (
    BookmarksManager,
    DownloadManager,
    HistoryManager,
    LibraryManager,
    YtDlpDownloadManager,
)

if not QApplication.instance():
    app = QApplication(sys.argv)


@pytest.fixture
def mock_app_data(tmp_path):
    with patch(
        "riemann.core.storage.QStandardPaths.writableLocation",
        return_value=str(tmp_path),
    ):
        yield tmp_path


def test_library_manager_metadata(mock_app_data):
    manager = LibraryManager()
    test_path = "/fake/path/doc.pdf"
    data = {
        "title": "Test Title",
        "authors": "John Doe",
        "year": "2023",
        "doi": "10.1234/5678",
        "arxiv_id": "2301.00001",
    }

    manager.save_metadata(test_path, data)
    retrieved = manager.get_metadata(test_path)

    assert retrieved["title"] == "Test Title"
    assert retrieved["authors"] == "John Doe"
    assert retrieved["year"] == "2023"
    assert retrieved["doi"] == "10.1234/5678"
    assert retrieved["arxiv_id"] == "2301.00001"


def test_library_manager_search(mock_app_data):
    manager = LibraryManager()
    manager.save_metadata(
        "/path/1.pdf", {"title": "Alpha", "authors": "Alice", "year": "2020"}
    )
    manager.save_metadata(
        "/path/2.pdf", {"title": "Beta", "authors": "Bob", "year": "2021"}
    )

    res1 = manager.search_library("author:Alice")
    assert len(res1) == 1
    assert res1[0]["title"] == "Alpha"

    res2 = manager.search_library("year:2021")
    assert len(res2) == 1
    assert res2[0]["title"] == "Beta"

    res3 = manager.search_library("Alpha")
    assert len(res3) == 1
    assert res3[0]["authors"] == "Alice"


def test_bookmarks_manager(mock_app_data):
    manager = BookmarksManager()
    manager.add("Google", "https://google.com")

    assert manager.is_bookmarked("https://google.com") is True
    assert len(manager.bookmarks) == 1

    manager.remove("https://google.com")
    assert manager.is_bookmarked("https://google.com") is False
    assert len(manager.bookmarks) == 0


def test_bookmarks_persistence(mock_app_data):
    manager1 = BookmarksManager()
    manager1.add("Test", "https://test.com")

    manager2 = BookmarksManager()
    assert manager2.is_bookmarked("https://test.com") is True


def test_history_manager(mock_app_data):
    manager = HistoryManager()
    manager.add("https://example.com", "web")
    manager.add("file.pdf", "pdf")

    assert "https://example.com" in manager.get_list("web")
    assert "file.pdf" in manager.get_list("pdf")

    model_data = manager.get_model_data()
    assert "https://example.com" in model_data
    assert "google.com" in model_data


def test_history_limit(mock_app_data):
    manager = HistoryManager()
    for i in range(600):
        manager.add(f"site{i}.com", "web")

    assert len(manager.get_list("web")) == 500
    assert "site599.com" in manager.get_list("web")
    assert "site0.com" not in manager.get_list("web")


def test_download_manager_persistence(mock_app_data):
    manager = DownloadManager()
    manager.table = MagicMock()
    manager.table.rowCount.return_value = 1

    file_item = MagicMock()
    file_item.text.return_value = "test.pdf"
    status_item = MagicMock()
    status_item.text.return_value = "Completed"
    path_item = MagicMock()
    path_item.text.return_value = "/down/test.pdf"

    manager.table.item.side_effect = lambda row, col: {
        0: file_item,
        1: status_item,
        2: path_item,
    }.get(col)

    manager._persist_entries()

    assert os.path.exists(manager._persist_path)
    with open(manager._persist_path, encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) == 1
        assert data[0]["file_name"] == "test.pdf"
        assert data[0]["status"] == "Completed"
        assert data[0]["full_path"] == "/down/test.pdf"


def test_ytdlp_download_manager_cumulative_playlist_progress(mock_app_data):
    """Item 8: Cumulative playlist progress across multiple videos."""
    from PySide6.QtWidgets import QProgressBar, QTreeWidget, QTreeWidgetItem

    mgr = YtDlpDownloadManager.__new__(YtDlpDownloadManager)
    mgr.tree = QTreeWidget()

    root_item = QTreeWidgetItem()
    root_prog = QProgressBar()
    task = {
        "worker": "mock_worker",
        "root_item": root_item,
        "root_prog": root_prog,
        "dest_dir": "/tmp",
        "videos": {},
        "is_finished": False,
    }
    mgr.tasks = [task]
    mgr.format_size = MagicMock(return_value="1 MB")
    mgr.format_time = MagicMock(return_value="10s")
    mgr._create_progress_bar = MagicMock(return_value=QProgressBar())

    mgr.update_progress("mock_worker", {
        "status": "downloading",
        "playlist_index": 3,
        "playlist_count": 10,
        "percentage": 50,
        "video_id": "vid_3",
        "filename": "vid_3.mp4",
    })

    assert root_prog.value() == 25
    assert root_item.text(4) == "Video 3 of 10"

    mgr.update_progress("mock_worker", {
        "status": "finished",
        "playlist_index": 3,
        "playlist_count": 10,
        "percentage": 100,
        "video_id": "vid_3",
        "filename": "vid_3.mp4",
    })

    assert root_prog.value() == 30


def test_ytdlp_download_manager_single_video_progress(mock_app_data):
    """Item 8: Single video progress updates."""
    from PySide6.QtWidgets import QProgressBar, QTreeWidget, QTreeWidgetItem

    mgr = YtDlpDownloadManager.__new__(YtDlpDownloadManager)
    mgr.tree = QTreeWidget()

    root_item = QTreeWidgetItem()
    root_prog = QProgressBar()
    task = {
        "worker": "single_worker",
        "root_item": root_item,
        "root_prog": root_prog,
        "dest_dir": "/tmp",
        "videos": {},
        "is_finished": False,
    }
    mgr.tasks = [task]
    mgr.format_size = MagicMock(return_value="5 MB")
    mgr.format_time = MagicMock(return_value="5s")
    mgr._create_progress_bar = MagicMock(return_value=QProgressBar())

    mgr.update_progress("single_worker", {
        "status": "downloading",
        "percentage": 68,
        "speed": 1024 * 1024,
        "eta": 5,
        "total_bytes": 10 * 1024 * 1024,
    })

    assert root_prog.value() == 68



def test_library_metadata_follows_a_moved_pdf(tmp_path, monkeypatch):
    import hashlib
    import shutil
    import sqlite3

    import pikepdf

    from riemann.core import managers

    monkeypatch.setattr(managers, "app_data_dir", lambda: str(tmp_path))
    lib = managers.LibraryManager()
    a = str(tmp_path / "a.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(a)

    legacy = hashlib.sha256(a.encode()).hexdigest()
    with sqlite3.connect(lib.db_path) as conn:
        conn.execute("INSERT INTO metadata (file_hash, file_path, title) VALUES (?, ?, ?)", (legacy, a, "Old row"))
    assert lib.get_metadata(a)["title"] == "Old row"

    b = str(tmp_path / "moved.pdf")
    shutil.move(a, b)
    data = lib.get_metadata(b)
    assert data["title"] == "Old row"
    assert data["file_path"] == b
    assert lib.search_library("Old")[0]["file_path"] == b


def test_unused_path_never_overwrites(tmp_path):
    from riemann.core.features import unused_path

    target = tmp_path / "talk.srt"
    assert unused_path(str(target)) == str(target)
    target.write_text("x")
    assert unused_path(str(target)) == str(tmp_path / "talk (2).srt")
    (tmp_path / "talk (2).srt").write_text("y")
    assert unused_path(str(target)) == str(tmp_path / "talk (3).srt")


def test_ytdlp_cancel_shows_at_once_and_stops_the_worker(qapp):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QPushButton

    class Worker(QObject):
        progress_details = Signal(dict)
        done = Signal(bool, str)
        is_cancelled = False

        def stop(self):
            self.is_cancelled = True

    mgr = YtDlpDownloadManager()
    worker = Worker()
    mgr.add_download(worker, "Talk", "/tmp", False)
    task = mgr.tasks[0]
    button = mgr.tree.itemWidget(task["root_item"], 5).findChild(QPushButton)
    button.click()
    assert worker.is_cancelled
    assert task["root_item"].text(4) == "Cancelling…"
    assert not button.isEnabled()
    mgr.update_progress(worker, {"status": "downloading", "percentage": 50, "video_id": "v", "filename": "v.mp4"})
    assert task["videos"] == {}
    worker.done.emit(False, "Download cancelled.")
    assert task["is_finished"]
    mgr.close()
    mgr.deleteLater()


def _contrast(a, b):
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _percentage_contrast(bar, value, background_x):
    bar.setValue(value)
    QApplication.processEvents()
    img = bar.grab().toImage()
    background = img.pixelColor(background_x(img.width()), img.height() // 2)
    return max(
        _contrast(img.pixelColor(x, y), background)
        for x in range(img.width() // 2 - 15, img.width() // 2 + 15)
        for y in range(2, img.height() - 2)
    )


def _themed_manager(dark, window_dark=None):
    from PySide6.QtWidgets import QWidget

    from riemann.core.assets import asset_path

    window_dark = dark if window_dark is None else window_dark
    with open(asset_path("theme", "modern_dark.css" if window_dark else "modern_light.css"), encoding="utf-8") as f:
        sheet = f.read()
    host = QWidget()
    host.dark_mode = dark
    host.setStyleSheet(sheet)
    mgr = YtDlpDownloadManager(host)
    bar = mgr._create_progress_bar(16)
    bar.setParent(mgr)
    bar.resize(150, 16)
    mgr.show()
    return host, mgr, bar


@pytest.mark.parametrize("dark", [False, True])
def test_ytdlp_progress_percentage_is_readable_on_the_empty_and_the_filled_bar(qapp, dark):
    """At a few percent the text sits on the empty bar, past half on the accent fill."""
    host, mgr, bar = _themed_manager(dark)
    assert _percentage_contrast(bar, 3, lambda w: w - 20) >= 4.5
    assert _percentage_contrast(bar, 90, lambda w: 20) >= 4.5
    mgr.close()
    host.deleteLater()


def test_ytdlp_progress_bars_follow_a_change_of_interface_theme(qapp):
    host, mgr, bar = _themed_manager(False, window_dark=True)
    host.dark_mode = True
    mgr.apply_theme(True)
    assert _percentage_contrast(bar, 3, lambda w: w - 20) >= 4.5
    assert _percentage_contrast(bar, 90, lambda w: 20) >= 4.5
    mgr.close()
    host.deleteLater()
