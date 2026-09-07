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
        "riemann.core.managers.QStandardPaths.writableLocation",
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
    with open(manager._persist_path, "r", encoding="utf-8") as f:
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

    # Video 3 of 10 at 50% download progress:
    # Completed fraction = (3 - 1) + 0.5 = 2.5 / 10 = 25%
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

    # Video 3 finished:
    # Completed fraction = (3 - 1) + 1.0 = 3.0 / 10 = 30%
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

