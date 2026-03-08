import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from riemann.app import (
    LibrarySearchDialog,
    RiemannWindow,
    SettingsDialog,
    get_resource_path,
)

if not QApplication.instance():
    app = QApplication(sys.argv)


class DummyWeb(QObject):
    urlChanged = Signal(QUrl)
    loadFinished = Signal(bool)

    def __init__(self):
        self.urlChanged = MagicMock()
        self.loadFinished = MagicMock()
        self.titleChanged = MagicMock()

    def url(self):
        return QUrl("https://example.com")

    def title(self):
        return "Test Title"


class DummyReader(QWidget):
    signatures_detected = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.current_path = None
        self.load_document = MagicMock()
        self._sig_panel_dismissed = False

    def toggle_theme(self):
        pass


class DummyBrowser(QWidget):
    def __init__(
        self, url="", profile=None, dark_mode=False, incognito=False, *args, **kwargs
    ):
        super().__init__()
        self.web = DummyWeb()
        self.completer = MagicMock()
        self.txt_url = MagicMock()
        self.incognito = incognito

    def toggle_theme(self):
        pass


class DummyQDialog(QWidget):
    def exec(self):
        return 1

    def accept(self):
        pass

    def reject(self):
        pass


@pytest.fixture
def mock_dependencies():
    with (
        patch("riemann.app.HistoryManager"),
        patch("riemann.app.BookmarksManager"),
        patch("riemann.app.DownloadManager"),
        patch("riemann.app.LibraryManager"),
        patch("riemann.app.ReaderTab", new=DummyReader),
        patch("riemann.app.BrowserTab", new=DummyBrowser),
        patch("riemann.app.QSettings"),
    ):
        yield


def test_get_resource_path():
    path = get_resource_path("assets/icon.ico")
    assert "assets" in path
    assert "icon.ico" in path


def test_riemann_window_init_incognito(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    assert window.incognito is True
    assert "Riemann (Incognito)" in window.windowTitle()
    assert window.tabs_main.count() == 2


def test_riemann_window_init_normal(mock_dependencies):
    window = RiemannWindow(incognito=False, restore_session=False)
    assert window.incognito is False
    assert "Riemann" in window.windowTitle()
    assert window.tabs_main.count() == 2


def test_new_pdf_tab(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    initial_count = window.tabs_main.count()
    window.new_pdf_tab()
    assert window.tabs_main.count() == initial_count + 1


def test_new_browser_tab(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    initial_count = window.tabs_main.count()
    window.new_browser_tab("https://example.com")
    assert window.tabs_main.count() == initial_count + 1


def test_toggle_split_view(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)

    window.tabs_side.show = MagicMock()

    window.toggle_split_view()

    window.tabs_side.show.assert_called_once()
    assert window.tabs_side.count() == 1
    assert window.tabs_main.count() == 1


def test_close_tab(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)

    window.tabs_main.widget(0).current_path = "/fake/path.pdf"
    initial_count = window.tabs_main.count()

    window.close_tab(0)
    assert window.tabs_main.count() == initial_count - 1
    assert len(window.closed_tabs_stack) == 1


def test_restore_last_closed_tab(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    window.closed_tabs_stack.append({"type": "web", "data": "https://test.com"})
    initial_count = window.tabs_main.count()

    window.restore_last_closed_tab()
    assert window.tabs_main.count() == initial_count + 1
    assert len(window.closed_tabs_stack) == 0


def test_toggle_reader_fullscreen(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    assert getattr(window, "_reader_fullscreen", False) is False
    assert not window.menuBar().isHidden()

    window.toggle_reader_fullscreen()
    assert getattr(window, "_reader_fullscreen", False) is True
    assert window.menuBar().isHidden()

    window.toggle_reader_fullscreen()
    assert getattr(window, "_reader_fullscreen", False) is False
    assert not window.menuBar().isHidden()


@patch("riemann.app.QMessageBox.information")
def test_settings_dialog_clear_history(mock_msgbox, mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)

    window.history_manager.history = {"web": ["https://old.com"]}
    dialog = SettingsDialog(window)

    dialog.clear_history()

    assert window.history_manager.history["web"] == []
    window.history_manager.save.assert_called()
    mock_msgbox.assert_called_once()


@patch("riemann.app.QMessageBox.information")
def test_settings_dialog_clear_downloads(mock_msgbox, mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    dialog = SettingsDialog(window)

    dialog.clear_downloads()

    dl_manager = window.download_manager_dialog
    dl_manager.table.setRowCount.assert_called_with(0)
    dl_manager.downloads.clear.assert_called()
    dl_manager._persist_entries.assert_called_once()
    mock_msgbox.assert_called_once()


@patch("riemann.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes)
@patch("riemann.app.QMessageBox.information")
def test_settings_dialog_clear_all_data(mock_info, mock_question, mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)

    window.history_manager.history = {"web": ["https://old.com"]}
    dialog = SettingsDialog(window)

    dialog.clear_all_data()

    assert window.history_manager.history["web"] == []
    window.download_manager_dialog.table.setRowCount.assert_called_with(0)
    mock_info.assert_called_once()


def test_library_search_dialog_execute(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    window.library_manager.search_library.return_value = [
        {
            "title": "Test 1",
            "authors": "Auth",
            "year": "2020",
            "file_path": "/path/1.pdf",
        }
    ]

    dialog = LibrarySearchDialog(window)
    dialog.search_input.setText("Test")
    dialog.execute_search()

    assert dialog.results_table.rowCount() == 1
    assert dialog.results_table.item(0, 0).text() == "Test 1"
    assert dialog.results_table.item(0, 3).text() == "/path/1.pdf"


@patch("riemann.app.QFileDialog.getOpenFileName", return_value=("/source.pdf", ""))
@patch("riemann.app.QInputDialog.getText", return_value=("1-2", True))
@patch("riemann.app.QFileDialog.getSaveFileName", return_value=("/dest.pdf", ""))
@patch("riemann.app.PdfReader")
@patch("riemann.app.PdfWriter")
@patch("riemann.app.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
def test_split_pdf(
    mock_msgbox,
    mock_writer_cls,
    mock_reader_cls,
    mock_save,
    mock_input,
    mock_open,
    mock_dependencies,
):
    window = RiemannWindow(incognito=True, restore_session=False)

    mock_reader = MagicMock()
    mock_reader.pages = [MagicMock(), MagicMock(), MagicMock()]
    mock_reader_cls.return_value = mock_reader

    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer

    with patch("builtins.open", MagicMock()):
        window.split_pdf()

    assert mock_writer.add_page.call_count == 2


@patch(
    "riemann.app.QFileDialog.getOpenFileNames", return_value=(["/a.pdf", "/b.pdf"], "")
)
@patch("riemann.app.QFileDialog.getSaveFileName", return_value=("/dest.pdf", ""))
@patch("riemann.app.PdfReader")
@patch("riemann.app.PdfWriter")
@patch("riemann.app.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
def test_join_pdfs(
    mock_msgbox,
    mock_writer_cls,
    mock_reader_cls,
    mock_save,
    mock_open,
    mock_dependencies,
):
    window = RiemannWindow(incognito=True, restore_session=False)

    mock_reader = MagicMock()
    mock_reader.pages = [MagicMock()]
    mock_reader_cls.return_value = mock_reader

    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer

    with patch("builtins.open", MagicMock()):
        window.join_pdfs()

    assert mock_writer.add_page.call_count == 2
    mock_writer.write.assert_called_once()


def test_add_to_history(mock_dependencies):
    window = RiemannWindow(incognito=False, restore_session=False)
    window.add_to_history("https://new.com", "web")
    window.history_manager.add.assert_called_with("https://new.com", "web")


def test_incognito_blocks_history(mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)
    window.add_to_history("https://new.com", "web")
    window.history_manager.add.assert_not_called()


@patch("riemann.app.QDialog", DummyQDialog)
@patch("riemann.app.LibrarySearchDialog")
def test_show_dialogs(mock_lib_dlg, mock_dependencies):
    window = RiemannWindow(incognito=True, restore_session=False)

    window.show_history()
    window.show_bookmarks()

    window.show_library_search()
    mock_lib_dlg.return_value.exec.assert_called_once()

    window.show_downloads()
    window.download_manager_dialog.show.assert_called()


@patch(
    "riemann.app.QFileDialog.getOpenFileNames",
    return_value=(["/new1.pdf", "/new2.pdf"], ""),
)
def test_open_pdf_smart(mock_file_dialog, mock_dependencies):
    window = RiemannWindow(incognito=False, restore_session=False)

    window.tabs_main.setCurrentIndex(0)
    initial_count = window.tabs_main.count()

    window.open_pdf_smart()

    assert window.history_manager.add.call_count >= 2
    assert window.tabs_main.count() == initial_count + 1
