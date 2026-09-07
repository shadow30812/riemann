from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QSettings, QUrl, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QWidget
from riemann.app import (
    LibrarySearchDialog,
    RiemannWindow,
    SettingsDialog,
    get_resource_path,
)
from riemann.ui.browser import BrowserTab


class DummySettings:
    """Provides safe Python types back to PySide6 methods like restoreGeometry."""
    _shared_data = {}

    def __init__(self, *args, **kwargs):
        self._data = DummySettings._shared_data

    def value(self, key, default_val=None, type=None):
        return self._data.get(key, default_val)

    def setValue(self, key, val):
        self._data[key] = val

    def sync(self):
        pass

    def clear(self):
        self._data.clear()



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
        self.is_preview = False
        self.scroll = None

    def load_document(self, path, restore_state=False):
        self.current_path = path

    def toggle_theme(self):
        pass

    def cleanup(self):
        pass



class DummyPreviewReaderTab(DummyReaderTab):
    signatures_detected = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.current_path = None
        self.is_preview = True
        self.scroll = None


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
    DummySettings._shared_data.clear()
    RiemannWindow._all_open_windows.clear()
    with (
        patch("riemann.app.BrowserTab", DummyBrowserTab),
        patch("riemann.app.ReaderTab", DummyReaderTab),
        patch("riemann.app.PreviewReaderTab", DummyPreviewReaderTab),
        patch("riemann.app.HistoryManager", DummyHistoryManager),
        patch("riemann.app.BookmarksManager", DummyBookmarksManager),
        patch("riemann.app.DownloadManager", DummyDownloadManager),
        patch("riemann.app.LibraryManager", DummyLibraryManager),
        patch("riemann.app.QSettings", DummySettings),
        patch("riemann.app.QWebEngineProfile"),
        patch("riemann.app.QWebEnginePage"),
    ):
        yield
    RiemannWindow._all_open_windows.clear()
    DummySettings._shared_data.clear()




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


def test_settings_dialog_preview_mode(qtbot):
    """Item 2: Preview mode checkbox in SettingsDialog."""
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    window.settings.setValue("reader/open_in_preview_mode", True)
    dlg = SettingsDialog(window)
    qtbot.addWidget(dlg)
    assert dlg.cb_pdf_preview_mode.isChecked() is True

    window.settings.setValue("reader/open_in_preview_mode", False)
    dlg2 = SettingsDialog(window)
    qtbot.addWidget(dlg2)
    assert dlg2.cb_pdf_preview_mode.isChecked() is False


def test_switch_open_documents_mode(qtbot):
    """Item 2: Live switching between Preview and Extended Reader modes."""
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    with patch("os.path.exists", return_value=True):
        # Open a reader tab
        window._add_pdf_tab("/fake/document.pdf", window.tabs_main)
        assert window.tabs_main.count() == 3  # 2 default tabs + 1 pdf
        pdf_tab = window.tabs_main.widget(2)
        assert getattr(pdf_tab, "is_preview", False) is False

        # Switch to preview mode
        window._switch_open_documents_mode(to_preview=True)
        new_tab = window.tabs_main.widget(2)
        assert getattr(new_tab, "is_preview", False) is True
        assert new_tab.current_path == "/fake/document.pdf"

        # Switch back to full reader mode
        window._switch_open_documents_mode(to_preview=False)
        reverted_tab = window.tabs_main.widget(2)
        assert getattr(reverted_tab, "is_preview", False) is False
        assert reverted_tab.current_path == "/fake/document.pdf"


def test_settings_dialog_mode_switch_prompt(qtbot):
    """Item 2: Prompt with explicit buttons when mode changes with open documents."""
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    with patch("os.path.exists", return_value=True):
        window._add_pdf_tab("/fake/document.pdf", window.tabs_main)

    window.settings.setValue("reader/open_in_preview_mode", False)

    # 1. User accepts switch -> calls _switch_open_documents_mode
    with (
        patch("riemann.app.SettingsDialog") as mock_settings_dlg_cls,
        patch("riemann.app.QMessageBox") as mock_msgbox_cls,
        patch.object(window, "_switch_open_documents_mode") as mock_switch,
    ):
        mock_dlg = MagicMock()
        mock_dlg.exec.return_value = 1  # Accepted
        mock_dlg.spin_sidebar.value.return_value = 240
        mock_dlg.spin_tab_limit.value.return_value = 10
        mock_dlg.spin_history_limit.value.return_value = 100
        mock_dlg.spin_autoscroll.value.return_value = 2
        mock_dlg.cb_floating_fs.isChecked.return_value = True
        mock_dlg.cb_pdf_preview_mode.isChecked.return_value = True
        mock_dlg.slider_scale.value.return_value = 100
        mock_settings_dlg_cls.return_value = mock_dlg

        btn_switch = MagicMock()
        btn_keep = MagicMock()
        mock_box_inst = MagicMock()
        mock_box_inst.addButton.side_effect = [btn_switch, btn_keep]
        mock_box_inst.clickedButton.return_value = btn_switch
        mock_msgbox_cls.return_value = mock_box_inst

        window.show_settings()
        mock_switch.assert_called_once_with(True)

    # 2. User rejects switch (keep current) -> does NOT call _switch_open_documents_mode
    with (
        patch("riemann.app.SettingsDialog") as mock_settings_dlg_cls,
        patch("riemann.app.QMessageBox") as mock_msgbox_cls,
        patch.object(window, "_switch_open_documents_mode") as mock_switch,
    ):
        mock_dlg = MagicMock()
        mock_dlg.exec.return_value = 1
        mock_dlg.spin_sidebar.value.return_value = 240
        mock_dlg.spin_tab_limit.value.return_value = 10
        mock_dlg.spin_history_limit.value.return_value = 100
        mock_dlg.spin_autoscroll.value.return_value = 2
        mock_dlg.cb_floating_fs.isChecked.return_value = True
        mock_dlg.cb_pdf_preview_mode.isChecked.return_value = True
        mock_dlg.slider_scale.value.return_value = 100
        mock_settings_dlg_cls.return_value = mock_dlg

        mock_box_inst = MagicMock()
        mock_box_inst.addButton.side_effect = [btn_switch, btn_keep]
        mock_box_inst.clickedButton.return_value = btn_keep
        mock_msgbox_cls.return_value = mock_box_inst

        window.show_settings()
        mock_switch.assert_not_called()



def test_multi_window_session_tracking_and_save(qtbot):
    """Item 3: Multi-window tracking in _all_open_windows and session serialization."""
    RiemannWindow._all_open_windows = []

    win1 = RiemannWindow(incognito=False, restore_session=False)
    win1.show()
    qtbot.addWidget(win1)
    assert win1 in RiemannWindow._all_open_windows

    win2 = RiemannWindow(incognito=False, restore_session=False)
    win2.show()
    qtbot.addWidget(win2)
    assert len(RiemannWindow._all_open_windows) == 2

    # Incognito window should NOT be tracked in _all_open_windows
    win_incog = RiemannWindow(incognito=True, restore_session=False)
    win_incog.show()
    qtbot.addWidget(win_incog)
    assert win_incog not in RiemannWindow._all_open_windows

    # Test saving all windows session
    RiemannWindow._save_all_windows_session()
    saved = win1.settings.value("session/windows", None)
    assert saved is not None
    assert len(saved) >= 2

    # Clean up
    RiemannWindow._all_open_windows = []


def test_dual_corner_fullscreen_button(qtbot):
    """Item 4: Fullscreen floating button appears on hover in both top corners."""
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.resize(1000, 700)
    window._reader_fullscreen = True
    window.floating_fs_btn.hide()

    from PySide6.QtCore import QPoint

    # Hover near top-left (e.g. local x=50, y=30)
    with patch.object(window, "mapFromGlobal", return_value=QPoint(50, 30)):
        window._track_mouse_for_fs()
    assert window.floating_fs_btn.pos() == QPoint(20, 20)
    assert not window.floating_fs_btn.isHidden()

    # Hover near top-right (e.g. local x=950, y=30)
    with patch.object(window, "mapFromGlobal", return_value=QPoint(950, 30)):
        window._track_mouse_for_fs()
    expected_x = window.width() - window.floating_fs_btn.width() - 20
    assert window.floating_fs_btn.pos() == QPoint(expected_x, 20)
    assert not window.floating_fs_btn.isHidden()

    # Hover in center (e.g. local x=500, y=300) should not move/show button
    window.floating_fs_btn.hide()
    with patch.object(window, "mapFromGlobal", return_value=QPoint(500, 300)):
        window._track_mouse_for_fs()
    assert window.floating_fs_btn.isHidden()




def test_exit_application(qtbot):
    """Item 3: File -> Exit / Ctrl+Q saves all windows and exits."""
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    mock_app = MagicMock()
    with (
        patch.object(RiemannWindow, "_save_all_windows_session") as mock_save,
        patch("PySide6.QtWidgets.QApplication.instance", return_value=mock_app),
    ):
        window.exit_application()
        mock_save.assert_called_once()
        mock_app.quit.assert_called_once()


def test_clean_exit_flag_written_to_pdfreader_settings(qtbot):
    """Clean exit must write session/clean_exit = True to QSettings('Riemann', 'PDFReader')."""
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)
    assert win.settings.value("session/clean_exit", False, type=bool) is False

    RiemannWindow._save_all_windows_session(clean_exit=True)
    assert win.settings.value("session/clean_exit", False, type=bool) is True

    win2 = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win2)
    assert win2._was_unclean_exit is False


def test_unclean_exit_prompt_no_discards_session_and_opens_fresh_homepage(qtbot):
    """When unclean exit prompt is shown and user clicks 'No', session is cleared and fresh homepages open."""
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)

    dummy_tab = [{"type": "web", "data": "https://example.com"}]
    win.settings.setValue("session/main_tabs", dummy_tab)
    win.settings.setValue("session/windows", [{"main_tabs": dummy_tab, "side_tabs": []}])
    win.settings.setValue("session/clean_exit", False)
    win._was_unclean_exit = True
    win.restore_session = True

    while win.tabs_main.count() > 0:
        win.tabs_main.removeTab(0)

    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as mock_q:
        win._restore_session()
        mock_q.assert_called_once()

    assert win.settings.value("session/main_tabs", []) == []
    assert win.settings.value("session/windows", []) == []

    # Clean default tabs: 1 PDF tab and 1 Browser tab
    assert win.tabs_main.count() == 2


def test_unclean_exit_prompt_yes_restores_tabs(qtbot):
    """When unclean exit prompt is shown and user clicks 'Yes', session tabs are restored."""
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)

    dummy_tab = [{"type": "web", "data": "https://example.com"}]
    win.settings.setValue("session/main_tabs", dummy_tab)
    win.settings.setValue("session/windows", [{"main_tabs": dummy_tab, "side_tabs": []}])
    win.settings.setValue("session/clean_exit", False)
    win._was_unclean_exit = True
    win.restore_session = True

    while win.tabs_main.count() > 0:
        win.tabs_main.removeTab(0)

    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as mock_q:
        win._restore_session()
        mock_q.assert_called_once()

    assert win.tabs_main.count() >= 1
    assert any(hasattr(win.tabs_main.widget(i), "web") for i in range(win.tabs_main.count()))


def test_closing_tabs_with_ctrl_w_does_not_restore_closed_tabs(qtbot):
    """Closing tabs via close_tab updates session immediately and does not restore closed tabs."""
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)

    win.new_browser_tab("https://example.com")
    assert win.tabs_main.count() == 3

    while win.tabs_main.count() > 0:
        win.close_tab(0)

    assert win.settings.value("session/main_tabs", []) == []

    RiemannWindow._save_all_windows_session(clean_exit=True)

    win2 = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win2)
    win2.restore_session = True
    win2._restore_session()

    found_closed = any(
        isinstance(win2.tabs_main.widget(i), BrowserTab)
        and "example.com" in getattr(win2.tabs_main.widget(i).web.url(), "toString", lambda: "")()
        for i in range(win2.tabs_main.count())
    )
    assert found_closed is False


def test_multi_window_sequential_close_preserves_windows(qtbot):
    """Closing windows sequentially within grace period preserves multi-window session."""
    RiemannWindow._all_open_windows = []
    RiemannWindow._recently_closed_windows = []

    win1 = RiemannWindow(incognito=False, restore_session=False)
    win1.new_browser_tab("https://window1.org")
    qtbot.addWidget(win1)

    win2 = RiemannWindow(incognito=False, restore_session=False)
    win2.new_browser_tab("https://window2.org")
    qtbot.addWidget(win2)

    assert len(RiemannWindow._all_open_windows) == 2

    close_event = QCloseEvent()
    win1.closeEvent(close_event)
    assert win1 not in RiemannWindow._all_open_windows
    assert len(RiemannWindow._recently_closed_windows) == 1

    win2.closeEvent(close_event)
    assert win2 not in RiemannWindow._all_open_windows

    saved = win2.settings.value("session/windows", [])
    assert len(saved) >= 2
    assert win2.settings.value("session/clean_exit", False, type=bool) is True

    RiemannWindow._all_open_windows = []
    RiemannWindow._recently_closed_windows = []



