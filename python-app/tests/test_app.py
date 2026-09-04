from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QMessageBox, QWidget
from riemann.app import (
    LibrarySearchDialog,
    RiemannWindow,
    SettingsDialog,
    get_resource_path,
)


class DummySettings:
    """Provides safe Python types back to PySide6 methods like restoreGeometry."""

    def __init__(self, *args, **kwargs):
        self._data = {}

    def value(self, key, default_val=None, type=None):
        return self._data.get(key, default_val)

    def setValue(self, key, val):
        self._data[key] = val

    def sync(self):
        pass


class DummyHistoryManager:
    """Provides an actual list to prevent QStringListModel segfaults."""

    def __init__(self):
        self.history = {"web": [], "pdf": []}
        self.save = MagicMock()

    def get_model_data(self):
        return []

    def get_list(self, item_type):
        return self.history.get(item_type, [])

    def add(self, *args):
        pass


class DummyBookmarksManager:
    def __init__(self):
        self.bookmarks = []


class DummyLibraryManager:
    def __init__(self):
        self.search_library = MagicMock(return_value=[])


class DummyDownloadManager(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.table = MagicMock()
        self.downloads = MagicMock()
        self._persist_entries = MagicMock()


class DummyBrowserTab(QWidget):
    def __init__(
        self, url="", profile=None, dark_mode=False, incognito=False, *args, **kwargs
    ):
        super().__init__()
        self.incognito = incognito
        self.txt_url = MagicMock()
        self.completer = MagicMock()

        self.web = MagicMock()
        self.web.url.return_value = QUrl("https://example.com")
        self.web.title.return_value = "Test Title"

    def toggle_theme(self):
        pass


class DummyReaderTab(QWidget):
    signatures_detected = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.current_path = None

    def load_document(self, path, restore_state=False):
        self.current_path = path

    def toggle_theme(self):
        pass


@pytest.fixture(autouse=True)
def isolated_app_environment(qtbot):
    """
    Automatically patches Heavy UI & WebEngine components for every test
    so Chromium threads never spin up and safe types are passed to C++.
    """
    with (
        patch("riemann.app.BrowserTab", DummyBrowserTab),
        patch("riemann.app.ReaderTab", DummyReaderTab),
        patch("riemann.app.HistoryManager", DummyHistoryManager),
        patch("riemann.app.BookmarksManager", DummyBookmarksManager),
        patch("riemann.app.DownloadManager", DummyDownloadManager),
        patch("riemann.app.LibraryManager", DummyLibraryManager),
        patch("riemann.app.QSettings", DummySettings),
        patch("riemann.app.QWebEngineProfile"),
        patch("riemann.app.QWebEnginePage"),
    ):
        yield


def test_get_resource_path():
    path = get_resource_path("assets/icons/icon.ico")
    assert "assets" in path
    assert "icon.ico" in path


def test_riemann_window_init_normal(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    assert window.incognito is False
    assert "Riemann" in window.windowTitle()
    assert window.tabs_main.count() == 2


def test_riemann_window_init_incognito(qtbot):
    window = RiemannWindow(incognito=True, restore_session=False)
    qtbot.addWidget(window)

    assert window.incognito is True
    assert "Incognito" in window.windowTitle()


def test_new_pdf_tab(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    initial_count = window.tabs_main.count()
    window.new_pdf_tab()
    assert window.tabs_main.count() == initial_count + 1


def test_new_browser_tab(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    initial_count = window.tabs_main.count()
    window.new_browser_tab("https://example.com")
    assert window.tabs_main.count() == initial_count + 1


def test_close_tab(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    initial_count = window.tabs_main.count()

    window.tabs_main.widget(0).current_path = "/fake/path.pdf"
    window.close_tab(0)

    assert window.tabs_main.count() == initial_count - 1
    assert len(window.closed_tabs_stack) == 1


def test_restore_last_closed_tab(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    window.closed_tabs_stack.append({"type": "web", "data": "https://test.com"})
    initial_count = window.tabs_main.count()

    window.restore_last_closed_tab()

    assert window.tabs_main.count() == initial_count + 1
    assert len(window.closed_tabs_stack) == 0


def test_toggle_split_view(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    window.tabs_side.show = MagicMock()
    window.toggle_split_view()

    window.tabs_side.show.assert_called_once()
    assert window.tabs_side.count() == 1
    assert window.tabs_main.count() == 1


@patch("riemann.app.QMessageBox.information")
def test_settings_dialog_clear_history(mock_msgbox, qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    window.history_manager.history = {"web": ["https://old.com"]}
    dialog = SettingsDialog(window)
    qtbot.addWidget(dialog)

    dialog.clear_history()

    assert window.history_manager.history["web"] == []
    window.history_manager.save.assert_called()
    mock_msgbox.assert_called_once()


@patch("riemann.app.QMessageBox.information")
def test_settings_dialog_clear_downloads(mock_msgbox, qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    dialog = SettingsDialog(window)
    qtbot.addWidget(dialog)

    dialog.clear_downloads()

    dl_manager = window.download_manager_dialog
    dl_manager.table.setRowCount.assert_called_with(0)
    dl_manager.downloads.clear.assert_called_once()
    dl_manager._persist_entries.assert_called_once()
    mock_msgbox.assert_called_once()


def test_library_search_dialog_execute(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    window.library_manager.search_library.return_value = [
        {"title": "Book A", "authors": "Author", "year": "2023", "file_path": "/a.pdf"}
    ]

    dialog = LibrarySearchDialog(window)
    qtbot.addWidget(dialog)

    dialog.search_input.setText("Book A")
    dialog.execute_search()

    assert dialog.results_table.rowCount() == 1
    assert dialog.results_table.item(0, 0).text() == "Book A"


@patch(
    "riemann.app.QFileDialog.getOpenFileNames", return_value=(["/a.pdf", "/b.pdf"], "")
)
@patch("riemann.app.QFileDialog.getSaveFileName", return_value=("/dest.pdf", ""))
@patch("riemann.app.PdfReader")
@patch("riemann.app.PdfWriter")
@patch("riemann.app.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
def test_join_pdfs(
    mock_msgbox, mock_writer_cls, mock_reader_cls, mock_save, mock_open, qtbot
):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    mock_reader = MagicMock()
    mock_reader.pages = [MagicMock()]
    mock_reader_cls.return_value = mock_reader

    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer

    with patch("builtins.open", MagicMock()):
        window.join_pdfs()

    assert mock_writer.add_page.call_count == 2
    mock_writer.write.assert_called_once()
