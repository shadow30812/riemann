import importlib
import os
import pkgutil
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from riemann.app import (
    LibrarySearchDialog,
    RiemannWindow,
    asset_path,
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

    def get_entries(self, item_type):
        return [{"path": p, "timestamp": ""} for p in self.history.get(item_type, [])]

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

    def confirm_leave(self):
        return True


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

    def resolve_page_edits(self):
        return True



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


def window_modules():
    """riemann.app and every riemann.window module, where window code looks names up."""
    import riemann.app
    import riemann.window

    names = [m.name for m in pkgutil.iter_modules(riemann.window.__path__)]
    return [riemann.app] + [importlib.import_module(f"riemann.window.{n}") for n in names]


@pytest.fixture(autouse=True)
def isolated_app_environment(qtbot):
    """
    Automatically patches Heavy UI & WebEngine components for every test
    so Chromium threads never spin up and safe types are passed to C++.
    """
    DummySettings._shared_data.clear()
    RiemannWindow._all_open_windows.clear()
    with ExitStack() as stack:
        for name, replacement in (
            ("BrowserTab", DummyBrowserTab),
            ("ReaderTab", DummyReaderTab),
            ("PreviewReaderTab", DummyPreviewReaderTab),
            ("HistoryManager", DummyHistoryManager),
            ("BookmarksManager", DummyBookmarksManager),
            ("DownloadManager", DummyDownloadManager),
            ("LibraryManager", DummyLibraryManager),
            ("QSettings", DummySettings),
            ("app_settings", lambda: DummySettings("Riemann", "PDFReader")),
            ("QWebEngineProfile", MagicMock()),
            ("QWebEnginePage", MagicMock()),
        ):
            for module in window_modules():
                if hasattr(module, name):
                    stack.enter_context(patch.object(module, name, replacement))
        yield
    RiemannWindow._all_open_windows.clear()
    DummySettings._shared_data.clear()




def test_asset_path():
    path = asset_path("icons", "windows-icon.ico")
    assert path.endswith(os.path.join("assets", "icons", "windows-icon.ico"))
    assert os.path.isfile(path)


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


def open_settings(window):
    with patch("riemann.ui.system.settings_tab.app_settings", return_value=window.settings):
        window.show_settings()
    tabs, index = window.find_system_tab("settings")
    return tabs.widget(index)


def test_settings_clear_history(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.history_manager.history = {"web": ["https://old.com"]}
    tab = open_settings(window)
    tab.clear_history()
    assert window.history_manager.history["web"] == []
    window.history_manager.save.assert_called()


def test_settings_clear_downloads(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    tab = open_settings(window)
    tab.clear_downloads()
    dl_manager = window.download_manager_dialog
    dl_manager.table.setRowCount.assert_called_with(0)
    dl_manager.downloads.clear.assert_called_once()
    dl_manager._persist_entries.assert_called_once()


def test_system_pages_open_once_per_window(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    before = window.tabs_main.count()
    open_settings(window)
    open_settings(window)
    window.show_history()
    window.show_site_permissions()
    assert window.tabs_main.count() == before + 2
    tabs, index = window.find_system_tab("settings")
    assert tabs.currentIndex() == index
    assert tabs.widget(index).current_section() == "permissions"
    assert tabs.tabBar().tabData(index) == "system"


def test_system_tabs_are_saved_in_sessions_and_closed_tab_history(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show_history()
    assert {"type": "system", "data": "history"} in window._serialize_tab_state(window.tabs_main)
    tabs, index = window.find_system_tab("history")
    window.close_tab(index)
    assert window.find_system_tab("history")[0] is None
    window.restore_last_closed_tab()
    assert window.find_system_tab("history")[0] is not None


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
    "PySide6.QtWidgets.QFileDialog.getOpenFileNames", return_value=(["/a.pdf", "/b.pdf"], "")
)
@patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("/dest.pdf", ""))
@patch("pypdf.PdfReader")
@patch("pypdf.PdfWriter")
@patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
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


def test_settings_preview_mode(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.settings.setValue("reader/open_in_preview_mode", True)
    tab = open_settings(window)
    assert tab.cb_preview_mode.isChecked() is True
    tab.cb_preview_mode.setChecked(False)
    assert window.settings.value("reader/open_in_preview_mode") is False


def test_settings_read_aloud_speed_and_voice(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.settings.setValue("reader/read_aloud_rate", 0.3)
    window.settings.setValue("reader/read_aloud_voice", "Bob")
    tab = open_settings(window)
    assert tab.slider_read_rate.value() == 3
    assert tab.combo_voice.voice() == "Bob"
    tab.slider_read_rate.setValue(-5)
    assert window.settings.value("reader/read_aloud_rate") == -0.5
    asked = []
    tab._voice_names = lambda: asked.append(1) or ["Alice", "Bob"]
    tab.combo_voice._loader = tab._voice_names
    assert not asked
    tab.combo_voice.showPopup()
    tab.combo_voice.hidePopup()
    assert asked == [1]
    assert [tab.combo_voice.itemText(i) for i in range(tab.combo_voice.count())] == ["System default", "Alice", "Bob"]
    assert tab.combo_voice.voice() == "Bob"
    tab.combo_voice.setCurrentIndex(1)
    assert window.settings.value("reader/read_aloud_voice") == "Alice"
    tab.combo_voice.setCurrentIndex(0)
    assert window.settings.value("reader/read_aloud_voice") == ""


def test_switch_open_documents_mode(qtbot):
    """Item 2: Live switching between Preview and Extended Reader modes."""
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    with patch("os.path.exists", return_value=True):
        window._add_pdf_tab("/fake/document.pdf", window.tabs_main)
        assert window.tabs_main.count() == 3
        pdf_tab = window.tabs_main.widget(2)
        assert getattr(pdf_tab, "is_preview", False) is False

        window._switch_open_documents_mode(to_preview=True)
        new_tab = window.tabs_main.widget(2)
        assert getattr(new_tab, "is_preview", False) is True
        assert new_tab.current_path == "/fake/document.pdf"

        window._switch_open_documents_mode(to_preview=False)
        reverted_tab = window.tabs_main.widget(2)
        assert getattr(reverted_tab, "is_preview", False) is False
        assert reverted_tab.current_path == "/fake/document.pdf"


def test_settings_mode_switch_prompt(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    with patch("os.path.exists", return_value=True):
        window._add_pdf_tab("/fake/document.pdf", window.tabs_main)
    window.settings.setValue("reader/open_in_preview_mode", False)
    tab = open_settings(window)

    with (
        patch("riemann.ui.system.settings_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
        patch.object(window, "_switch_open_documents_mode") as mock_switch,
    ):
        tab.cb_preview_mode.setChecked(True)
        mock_switch.assert_called_once_with(True)

    with (
        patch("riemann.ui.system.settings_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.No),
        patch.object(window, "_switch_open_documents_mode") as mock_switch,
    ):
        tab.cb_preview_mode.setChecked(False)
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

    win_incog = RiemannWindow(incognito=True, restore_session=False)
    win_incog.show()
    qtbot.addWidget(win_incog)
    assert win_incog not in RiemannWindow._all_open_windows

    RiemannWindow._save_all_windows_session()
    saved = win1.settings.value("session/windows", None)
    assert saved is not None
    assert len(saved) >= 2

    RiemannWindow._all_open_windows = []


def test_dual_corner_fullscreen_button(qtbot):
    """Item 4: Fullscreen floating button appears on hover in both top corners."""
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.resize(1000, 700)
    window._reader_fullscreen = True
    window.floating_fs_btn.hide()

    from PySide6.QtCore import QPoint

    with patch.object(window, "mapFromGlobal", return_value=QPoint(50, 30)):
        window._track_mouse_for_fs()
    assert window.floating_fs_btn.pos() == QPoint(20, 20)
    assert not window.floating_fs_btn.isHidden()

    with patch.object(window, "mapFromGlobal", return_value=QPoint(950, 30)):
        window._track_mouse_for_fs()
    expected_x = window.width() - window.floating_fs_btn.width() - 20
    assert window.floating_fs_btn.pos() == QPoint(expected_x, 20)
    assert not window.floating_fs_btn.isHidden()

    window.floating_fs_btn.hide()
    with patch.object(window, "mapFromGlobal", return_value=QPoint(500, 300)):
        window._track_mouse_for_fs()
    assert window.floating_fs_btn.isHidden()




def test_fullscreen_bars_stay_while_the_pointer_is_on_the_reader_toolbar(qtbot):
    from PySide6.QtCore import QPoint

    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.resize(1000, 700)
    window.show()
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    reader = QWidget()
    reader.toolbar = QWidget()
    reader.toolbar.setFixedHeight(90)
    column = QVBoxLayout(reader)
    column.setContentsMargins(0, 0, 0, 0)
    column.addWidget(reader.toolbar)
    column.addStretch(1)
    window.tabs_main.addTab(reader, "doc")
    window.tabs_main.setCurrentWidget(reader)
    QApplication.processEvents()
    window._reader_fullscreen = True
    top = reader.toolbar.mapTo(window, QPoint(0, 0)).y()
    bottom = top + reader.toolbar.height()
    assert bottom > 100
    inside = QPoint(500, bottom - 5)
    with patch.object(window, "mapFromGlobal", return_value=inside):
        assert window._pointer_over_controls()
        window._track_mouse_for_fs()
    assert not window.hover_timer.isActive()
    with patch.object(window, "mapFromGlobal", return_value=QPoint(500, bottom + 60)):
        assert not window._pointer_over_controls()
        window._track_mouse_for_fs()
    assert window.hover_timer.isActive()
    window.hover_timer.stop()
    window._reader_fullscreen = False


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





def test_toggle_split_view_twice_merges_back(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show()

    window.toggle_split_view()
    assert window._split_view_active()
    assert window.tabs_side.count() == 1

    window.toggle_split_view()
    assert not window._split_view_active()
    assert window.tabs_side.count() == 0
    assert window.tabs_main.count() == 2


def test_move_single_tab_to_other_pane_keeps_main_pane_populated(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show()
    window.close_tab(1)
    assert window.tabs_main.count() == 1

    window.move_tab_to_other_pane(window.tabs_main, 0)
    assert window.tabs_side.count() == 1
    assert window.tabs_main.count() == 1


def test_auto_save_refreshes_the_record_used_for_crash_recovery(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.new_browser_tab("https://example.com")

    window.settings.setValue("session/windows", [{"main_tabs": [], "side_tabs": []}])
    window._auto_save_session()

    saved = window.settings.value("session/windows", [])
    assert saved and any(t.get("type") == "web" for t in saved[0]["main_tabs"])
    assert window.settings.value("session/clean_exit", True) is False


def test_document_kind_by_extension_and_magic(tmp_path):
    fake_pdf = tmp_path / "scan.bin"
    fake_pdf.write_bytes(b"%PDF-1.7 rest")
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    assert RiemannWindow._document_kind("a.PDF") == "pdf"
    assert RiemannWindow._document_kind("a.md") == "markdown"
    assert RiemannWindow._document_kind("a.htm") == "html"
    assert RiemannWindow._document_kind(str(fake_pdf)) == "pdf"
    assert RiemannWindow._document_kind(str(notes)) == "text"


def test_text_files_are_told_from_binary_by_content(tmp_path):
    from riemann.core.doctypes import SNIFF_BYTES, document_kind

    makefile = tmp_path / "Makefile"
    makefile.write_text("all:\n\tcc main.c\n")
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n")
    cut_char = tmp_path / "long.log"
    cut_char.write_bytes(b"a" * (SNIFF_BYTES - 1) + "é".encode() + b"rest")
    image = tmp_path / "photo.txt"
    image.write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR")
    latin1 = tmp_path / "old.txt"
    latin1.write_bytes("caf\xe9 cr\xe8me".encode("latin-1"))

    assert document_kind(str(makefile)) == "text"
    assert document_kind(str(source)) == "text"
    assert document_kind(str(cut_char)) == "text"
    assert document_kind(str(image)) == "binary"
    assert document_kind(str(latin1)) == "binary"


def test_preview_mode_setting_applies_to_any_opened_document(qtbot, tmp_path):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.settings.setValue("reader/open_in_preview_mode", True)

    md = tmp_path / "notes.md"
    md.write_text("# Title\n\nBody")
    md_tab = window._open_document(str(md))
    assert getattr(md_tab, "is_preview", False) is True

    with patch("os.path.exists", return_value=True):
        pdf_tab = window._open_document("/fake/document.pdf")
    assert pdf_tab.is_preview is True

    serialized = window._serialize_tab_state(window.tabs_main)
    assert {"type": "doc", "data": str(md), "preview": True} in serialized


def test_double_clicking_a_preview_tab_opens_full_mode(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    with patch("os.path.exists", return_value=True):
        preview = window._open_document("/fake/document.pdf", preview=True)
        idx = window.tabs_main.indexOf(preview)
        window._on_tab_bar_double_clicked(window.tabs_main, idx)

    promoted = window.tabs_main.widget(idx)
    assert promoted is not preview
    assert promoted.is_preview is False
    assert promoted.current_path == "/fake/document.pdf"


def test_folder_view_preview_is_added_to_history(qtbot, tmp_path):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    page = tmp_path / "page.html"
    page.write_text("<p>hi</p>")

    with patch.object(window.history_manager, "add") as mock_add:
        window._on_file_single_clicked(str(pdf))
        window._on_file_single_clicked(str(notes))
        window._on_file_single_clicked(str(page))

    assert mock_add.call_args_list[0].args == (str(pdf), "pdf")
    assert mock_add.call_args_list[1].args == (str(notes), "pdf")
    assert mock_add.call_args_list[2].args == (str(page), "web")


def test_double_clicking_a_file_in_the_folder_view_opens_full_mode(qtbot, tmp_path):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.settings.setValue("reader/open_in_preview_mode", True)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    for f in (first, second):
        f.write_bytes(b"%PDF-1.7")

    window._on_file_double_clicked(str(first))
    opened = window.tabs_main.currentWidget()
    assert opened.current_path == str(first)
    assert opened.is_preview is False

    window._on_file_single_clicked(str(second))
    preview = window.preview_tab_widget
    window._on_file_double_clicked(str(second))
    promoted = window.tabs_main.currentWidget()
    assert promoted is not preview
    assert promoted.current_path == str(second)
    assert promoted.is_preview is False
    assert window.preview_tab_widget is None


def test_dropping_a_folder_opens_folder_session(qtbot, tmp_path):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)

    event = MagicMock()
    url = QUrl.fromLocalFile(str(tmp_path))
    event.mimeData.return_value.urls.return_value = [url]
    with patch.object(window, "open_folder") as mock_open_folder:
        window.dropEvent(event)
    mock_open_folder.assert_called_once_with(str(tmp_path))


def test_collapse_tabs_hides_both_strips_and_survives_fullscreen(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show()
    window.toggle_split_view()

    window.btn_collapse_tabs.setChecked(True)
    assert window.tabs_main.tabBar().isHidden()
    assert window.tabs_side.tabBar().isHidden()
    assert window.action_collapse_tabs.isChecked()
    assert window.settings.value("app/tabs_collapsed") is True

    window.new_pdf_tab()
    assert window.tabs_main.tabBar().isHidden()

    window.toggle_reader_fullscreen()
    window.exit_fullscreen()
    assert window.tabs_main.tabBar().isHidden()

    window.toggle_tabs_collapsed()
    assert not window.tabs_main.tabBar().isHidden()
    assert not window.tabs_side.tabBar().isHidden()
    assert not window.btn_collapse_tabs.isChecked()


def test_frozen_build_never_uses_itself_as_the_venv_python():
    """A packaged Riemann is sys.executable, and Nuitka refuses '-m venv' on itself."""
    import sys

    from riemann.core import dependencies

    with (
        patch.object(dependencies, "_is_frozen", return_value=True),
        patch.object(dependencies.shutil, "which", return_value=None),
    ):
        with pytest.raises(RuntimeError):
            dependencies._find_base_python()

    with patch.object(dependencies, "_is_frozen", return_value=False):
        assert dependencies._find_base_python() == sys.executable


def test_venv_lookups_skip_the_interpreter_once_cached(tmp_path):
    """Launches read the venv's version and site folders without starting its Python."""
    import os

    from riemann.core import dependencies

    cfg = tmp_path / "pyvenv.cfg"
    cfg.write_text("home = /usr/bin\ninclude-system-site-packages = true\nversion = 3.11.16\n")
    py = str(tmp_path / "bin" / "python")

    with patch.object(dependencies.subprocess, "check_output") as mock_run:
        assert dependencies._venv_version(str(tmp_path), py) == "3.11"
        mock_run.assert_not_called()

    with patch.object(
        dependencies.subprocess, "check_output", return_value='["/venv/site-packages"]\n'
    ) as mock_run:
        assert dependencies._venv_site_paths(str(tmp_path), py) == ["/venv/site-packages"]
        assert dependencies._venv_site_paths(str(tmp_path), py) == ["/venv/site-packages"]
        assert mock_run.call_count == 1

        st = cfg.stat()
        os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        dependencies._venv_site_paths(str(tmp_path), py)
        assert mock_run.call_count == 2


def test_greeting_name_falls_back_without_pwd():
    from riemann.ui import browser

    name = browser._os_display_name()
    assert isinstance(name, str) and name


def test_ipc_round_trips_awkward_paths():
    from riemann.app import decode_ipc_message, encode_ipc_paths

    paths = ["/tmp/a|b paper.pdf", "/home/u/notes, draft.md", "/srv/dir with spaces"]
    assert decode_ipc_message(encode_ipc_paths(paths)) == paths
    assert decode_ipc_message(encode_ipc_paths(paths[:1])) == paths[:1]
    assert decode_ipc_message(encode_ipc_paths([])) == []
    assert decode_ipc_message(b"/old/a.pdf|/old/b.pdf") == ["/old/a.pdf", "/old/b.pdf"]


def test_single_instance_name_is_per_user(monkeypatch):
    import getpass

    from riemann.app import single_instance_name

    monkeypatch.setattr(getpass, "getuser", lambda: "alice")
    alice = single_instance_name()
    monkeypatch.setattr(getpass, "getuser", lambda: "bob")
    assert single_instance_name() != alice
    assert alice.startswith("RiemannSingleInstance-")


def test_single_instance_socket_survives_a_stale_file(qapp, tmp_path):
    import os
    import uuid

    from PySide6.QtCore import QDir
    from PySide6.QtNetwork import QLocalServer

    from riemann.app import hand_off_to_running_instance, listen_for_instances

    name = f"riemann-test-{uuid.uuid4().hex[:8]}"
    stale = os.path.join(QDir.tempPath(), name)
    with open(stale, "w") as f:
        f.write("left by a crash")
    assert not hand_off_to_running_instance(name, b"x\0", attempts=1)

    server = QLocalServer()
    assert listen_for_instances(server, name)
    received = []
    server.newConnection.connect(lambda: received.append(server.nextPendingConnection()))
    assert hand_off_to_running_instance(name, b"paper.pdf\0", attempts=1)
    qapp.processEvents()
    assert received
    server.close()


def test_theme_stylesheet_is_found(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.dark_mode = True
    window.enforce_global_stylesheet()
    assert "QToolTip" in window.styleSheet()
    window.dark_mode = False
    window.enforce_global_stylesheet()
    assert "#F4F4F1" in window.styleSheet()


def test_links_open_beside_in_the_split_pane(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show()
    assert window.tabs_side.isHidden()
    window.open_url_beside("https://arxiv.org/abs/1706.03762")
    assert not window.tabs_side.isHidden()
    assert window.tabs_side.count() == 1


def test_metrics_label_grows_to_fit_its_text(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    QApplication.processEvents()
    with patch("riemann.window.chrome.time.strftime", return_value="Wednesday 09:42:40 AM " * 4):
        window._update_global_metrics()
    QApplication.processEvents()
    label = window.metrics_label
    assert label.width() >= label.sizeHint().width()
    corner = window.menu_corner_widget
    assert corner.width() >= corner.sizeHint().width()
    assert corner.geometry().right() < window.menuBar().width()


def test_window_toast_replaces_the_one_on_screen(qtbot):
    from PySide6.QtWidgets import QLabel

    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show_toast("first")
    window.show_toast("second")
    shown = [
        label.text()
        for label in window.findChildren(QLabel)
        if label.text() in ("first", "second") and not label.isHidden()
    ]
    assert shown == ["second"]


def test_empty_tab_in_preview_mode_shows_the_preview_eye(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.settings.setValue("reader/open_in_preview_mode", True)
    window.new_pdf_tab()
    idx = window.tabs_main.currentIndex()
    assert window.tabs_main.widget(idx).is_preview
    shown = window.tabs_main.tabIcon(idx).pixmap(16, 16).toImage()
    eye = window._preview_icon().pixmap(16, 16).toImage()
    assert shown == eye


def test_second_instance_does_not_take_the_socket_from_a_live_one(qapp):
    import uuid

    from PySide6.QtNetwork import QLocalServer

    from riemann.app import hand_off_to_running_instance, listen_for_instances

    name = f"riemann-test-{uuid.uuid4().hex[:8]}"
    first = QLocalServer()
    assert listen_for_instances(first, name)
    assert first.socketOptions() & QLocalServer.SocketOption.UserAccessOption
    second = QLocalServer()
    assert not listen_for_instances(second, name)
    assert os.path.exists(first.fullServerName())
    received = []
    first.newConnection.connect(lambda: received.append(first.nextPendingConnection()))
    assert hand_off_to_running_instance(name, b"paper.pdf\0", attempts=1)
    qapp.processEvents()
    assert received
    first.close()
    second.close()


def test_running_instance_opens_only_files_it_can_show(tmp_path):
    from riemann.window.instance import encode_ipc_paths, handed_over_paths

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    unnamed = tmp_path / "download"
    unnamed.write_bytes(b"%PDF-1.7\n")
    script = tmp_path / "run.sh"
    script.write_text("echo hi")
    makefile = tmp_path / "Makefile"
    makefile.write_text("all:\n")
    unlisted = tmp_path / "notes.unknownext"
    unlisted.write_text("looks like text, but its name isn't listed")
    folder = tmp_path / "papers"
    folder.mkdir()
    message = encode_ipc_paths(
        [str(pdf), str(unnamed), str(script), str(makefile), str(unlisted), str(folder), str(tmp_path / "missing.pdf")]
    )
    files, folders = handed_over_paths(message)
    assert files == [str(pdf), str(unnamed), str(script), str(makefile)]
    assert folders == [str(folder)]


def test_reopened_reader_tab_returns_to_its_own_page(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    tab = window.tabs_main.widget(0)
    tab.current_path = "/fake/a.pdf"
    tab.current_doc = MagicMock(page_count=80)
    tab.current_page_index = 39
    window.close_tab(0)
    window.settings.setValue("lastPage", 2)
    opened = []
    with patch.object(window, "_open_document", lambda *a, **k: opened.append(k)):
        window.restore_last_closed_tab()
    assert opened[0]["page"] == 39


def test_windows_share_one_persistent_web_profile(qtbot, monkeypatch):
    from PySide6.QtWebEngineCore import QWebEngineProfile

    import riemann.app

    monkeypatch.setattr(riemann.app, "QWebEngineProfile", QWebEngineProfile)
    monkeypatch.setattr(riemann.app, "_persistent", None)
    first = riemann.app.persistent_profile()
    assert riemann.app.persistent_profile() is first
    assert first.parent() is QApplication.instance()
    assert not first.isOffTheRecord()
    with patch.object(riemann.app, "persistent_profile", return_value=first):
        windows = [RiemannWindow(incognito=False, restore_session=False) for _ in range(2)]
    for w in windows:
        qtbot.addWidget(w)
    assert windows[0].web_profile is windows[1].web_profile is first


def real_reader(window, qtbot):
    """An empty full-mode reader (the real class, not the dummy) as the window's current tab."""
    from riemann.ui.reader.tab import ReaderTab as RealReaderTab

    reader = RealReaderTab()
    idx = window.tabs_main.addTab(reader, "New Tab")
    window.tabs_main.setCurrentIndex(idx)
    return reader, idx


def test_a_file_opened_from_an_empty_readers_start_page_follows_preview_mode(qtbot, tmp_path):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.settings.setValue("reader/open_in_preview_mode", True)
    window.history_manager.add = MagicMock()
    path = str(tmp_path / "paper.pdf")
    with open(path, "wb") as f:
        f.write(b"%PDF-1.7\n")
    reader, idx = real_reader(window, qtbot)

    reader.txt_open_path.setText(path)
    reader._on_home_path_entered()

    tab = window.tabs_main.widget(idx)
    assert tab is not reader and tab.is_preview and tab.current_path == path
    eye = window._preview_icon().pixmap(16).toImage()
    assert window.tabs_main.tabIcon(idx).pixmap(16).toImage() == eye
    assert window.tabs_main.currentIndex() == idx
    window.history_manager.add.assert_called_with(os.path.abspath(path), "pdf")


def test_a_file_opened_from_a_readers_open_dialog_is_recorded_in_history(qtbot, tmp_path):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.history_manager.add = MagicMock()
    path = str(tmp_path / "notes.txt")
    with open(path, "w") as f:
        f.write("plain text\n")
    reader, _ = real_reader(window, qtbot)
    reader.current_path = str(tmp_path / "shown.pdf")

    with (
        patch("riemann.ui.reader.mixins.document.QFileDialog.getOpenFileNames", return_value=([path], "")),
        patch.object(reader, "load_document") as load,
    ):
        reader.open_pdf_dialog()

    load.assert_called_once_with(path)
    window.history_manager.add.assert_called_with(os.path.abspath(path), "pdf")
    reader.current_path = None
    reader.cleanup()


def test_document_history_sorts_files_into_classes(qtbot, tmp_path):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    names = ["paper.pdf", "README.md", "notes.txt", "build.py", "Makefile", "gone.pdf"]
    window.history_manager.history = {"web": [], "pdf": [str(tmp_path / n) for n in names], "folder": []}
    window.show_history()
    tabs, index = window.find_system_tab("history")
    page = tabs.widget(index)
    tree = page.trees["pdf"]
    types = {tree.topLevelItem(i).text(0): tree.topLevelItem(i).text(1) for i in range(tree.topLevelItemCount())}
    assert types == {
        "paper.pdf": "PDF", "README.md": "Markdown", "notes.txt": "Text",
        "build.py": "Code", "Makefile": "Code", "gone.pdf": "PDF",
    }

    page.tabs.setCurrentIndex([k for k, _ in page.KINDS].index("pdf"))
    assert not page.doc_class.isHidden()
    page.doc_class.setCurrentIndex(page.doc_class.findData("Code"))
    shown = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount()) if not tree.topLevelItem(i).isHidden()]
    assert shown == ["build.py", "Makefile"]
    page.search.setText("make")
    shown = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount()) if not tree.topLevelItem(i).isHidden()]
    assert shown == ["Makefile"]

    page.tabs.setCurrentIndex([k for k, _ in page.KINDS].index("web"))
    assert page.doc_class.isHidden()


def test_quitting_asks_about_page_edits_and_cancel_keeps_the_app_open(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    with patch("os.path.exists", return_value=True):
        window._add_pdf_tab("/fake/edited.pdf", window.tabs_main)
    reader = window.tabs_main.widget(window.tabs_main.count() - 1)
    reader.edit_scratch = "/cache/scratch.pdf"
    reader.resolve_page_edits = MagicMock(return_value=False)

    with patch("riemann.app.QApplication") as app, patch.object(RiemannWindow, "_save_all_windows_session") as save:
        window.exit_application()
    reader.resolve_page_edits.assert_called_once()
    app.instance.return_value.quit.assert_not_called()
    save.assert_not_called()

    reader.resolve_page_edits = MagicMock(return_value=True)
    with patch("riemann.app.QApplication") as app, patch.object(RiemannWindow, "_save_all_windows_session"):
        window.exit_application()
    app.instance.return_value.quit.assert_called_once()


def test_a_web_tab_whose_page_is_answered_stay_stays_open(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    tab = window.new_browser_tab("https://example.com")
    tab.confirm_leave = MagicMock(return_value=False)
    window.close_tab(window.tabs_main.indexOf(tab))
    tab.confirm_leave.assert_called_once()
    assert window.tabs_main.indexOf(tab) != -1

    tab.confirm_leave = MagicMock(return_value=True)
    window.close_tab(window.tabs_main.indexOf(tab))
    assert window.tabs_main.indexOf(tab) == -1


def test_quitting_lets_web_pages_ask_once(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    tab = window.new_browser_tab("https://example.com")
    tab.confirm_leave = MagicMock(return_value=False)
    with patch("riemann.app.QApplication") as app, patch.object(RiemannWindow, "_save_all_windows_session"):
        window.exit_application()
    app.instance.return_value.quit.assert_not_called()

    tab.confirm_leave = MagicMock(return_value=True)
    with patch("riemann.app.QApplication") as app, patch.object(RiemannWindow, "_save_all_windows_session"):
        window.exit_application()
        app.instance.return_value.quit.assert_called_once()
        window.closeEvent(QCloseEvent())
    tab.confirm_leave.assert_called_once()
