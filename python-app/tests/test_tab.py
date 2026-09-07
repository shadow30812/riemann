import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from riemann.core.constants import ViewMode, ZoomMode
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.reader.widgets import PageWidget

sys.modules["riemann_core"] = MagicMock()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture
def reader_tab(qapp):
    with patch("riemann.ui.reader.tab.QSettings") as mock_settings:
        mock_settings.return_value.value.return_value = True
        tab = ReaderTab()
        yield tab
        tab.deleteLater()


def test_initialization(reader_tab):
    assert reader_tab.engine is not None
    assert reader_tab.current_doc is None
    assert reader_tab.current_path is None
    assert reader_tab.current_page_index == 0
    assert reader_tab.zoom_mode == ZoomMode.FIT_WIDTH
    assert reader_tab.manual_scale == 1.0
    assert reader_tab.facing_mode is False
    assert reader_tab.continuous_scroll is True
    assert reader_tab.view_mode == ViewMode.IMAGE


def test_setup_ui_elements_exist(reader_tab):
    assert reader_tab.toolbar is not None
    assert reader_tab.anno_toolbar is not None
    assert reader_tab.search_bar is not None
    assert reader_tab.ai_search_bar is not None
    assert reader_tab.stack is not None
    assert reader_tab.scroll is not None
    assert getattr(reader_tab, "_web_placeholder", None) is not None
    assert reader_tab.home_page_widget is not None


def test_toggle_view_mode(reader_tab):
    assert reader_tab.view_mode == ViewMode.IMAGE

    with patch.object(reader_tab, "update_view") as mock_update:
        reader_tab.toggle_view_mode()
        assert reader_tab.view_mode == ViewMode.REFLOW
        assert reader_tab.stack.currentIndex() == 1
        assert reader_tab.btn_reflow.isChecked() is True
        if hasattr(reader_tab, "_rebuild_debounce_timer"):
            reader_tab._rebuild_debounce_timer.timeout.emit()
        mock_update.assert_called_once()

        reader_tab.toggle_view_mode()
        assert reader_tab.view_mode == ViewMode.IMAGE
        assert reader_tab.stack.currentIndex() == 0
        assert reader_tab.btn_reflow.isChecked() is False
        if hasattr(reader_tab, "_rebuild_debounce_timer"):
            reader_tab._rebuild_debounce_timer.timeout.emit()
        assert mock_update.call_count == 2


def test_toggle_facing_mode(reader_tab):
    assert reader_tab.facing_mode is False
    with (
        patch.object(reader_tab, "rebuild_layout") as mock_rebuild,
        patch.object(reader_tab, "update_view") as mock_update,
    ):
        reader_tab.toggle_facing_mode()
        assert reader_tab.facing_mode is True
        assert reader_tab.btn_facing.isChecked() is True
        if hasattr(reader_tab, "_rebuild_debounce_timer"):
            reader_tab._rebuild_debounce_timer.timeout.emit()
        mock_rebuild.assert_called_once()
        mock_update.assert_called_once()


def test_toggle_scroll_mode(reader_tab):
    assert reader_tab.continuous_scroll is True
    with (
        patch.object(reader_tab, "rebuild_layout") as mock_rebuild,
        patch.object(reader_tab, "update_view") as mock_update,
    ):
        reader_tab.toggle_scroll_mode()
        assert reader_tab.continuous_scroll is False
        assert reader_tab.btn_scroll_mode.isChecked() is False
        if hasattr(reader_tab, "_rebuild_debounce_timer"):
            reader_tab._rebuild_debounce_timer.timeout.emit()
        mock_rebuild.assert_called_once()
        mock_update.assert_called_once()


def test_next_and_prev_view(reader_tab):
    reader_tab.current_doc = MagicMock()
    reader_tab.current_doc.page_count = 5
    reader_tab.current_page_index = 0

    with (
        patch.object(reader_tab, "update_view"),
        patch.object(reader_tab, "ensure_visible"),
    ):
        reader_tab.next_view()
        assert reader_tab.current_page_index == 1

        reader_tab.facing_mode = True
        reader_tab.next_view()
        assert reader_tab.current_page_index == 3

        reader_tab.prev_view()
        assert reader_tab.current_page_index == 1


def test_apply_zoom_string(reader_tab):
    with patch.object(reader_tab, "on_zoom_changed_internal") as mock_zoom:
        reader_tab.apply_zoom_string("Fit Width")
        assert reader_tab.zoom_mode == ZoomMode.FIT_WIDTH
        mock_zoom.assert_called_once()

        reader_tab.apply_zoom_string("Fit Height")
        assert reader_tab.zoom_mode == ZoomMode.FIT_HEIGHT
        assert mock_zoom.call_count == 2

        reader_tab.apply_zoom_string("150%")
        assert reader_tab.zoom_mode == ZoomMode.MANUAL
        assert reader_tab.manual_scale == 1.5
        assert mock_zoom.call_count == 3


def test_zoom_step(reader_tab):
    initial_scale = reader_tab.manual_scale
    with patch.object(reader_tab, "on_zoom_changed_internal"):
        reader_tab.zoom_step(1.1)
        assert reader_tab.manual_scale == initial_scale * 1.1
        assert reader_tab.zoom_mode == ZoomMode.MANUAL


def test_toggle_theme(reader_tab):
    initial_theme = reader_tab.theme_mode
    with (
        patch.object(reader_tab, "apply_theme") as mock_apply,
        patch.object(reader_tab, "update_view") as mock_update,
    ):
        reader_tab.toggle_theme()
        assert reader_tab.theme_mode != initial_theme
        mock_apply.assert_called_once()
        if hasattr(reader_tab, "_rebuild_debounce_timer"):
            reader_tab._rebuild_debounce_timer.timeout.emit()
        mock_update.assert_called_once()


def test_on_page_input_return(reader_tab):
    reader_tab.current_doc = MagicMock()
    reader_tab.current_doc.page_count = 10
    reader_tab.txt_page.setText("5")

    with (
        patch.object(reader_tab, "update_view"),
        patch.object(reader_tab, "ensure_visible"),
    ):
        reader_tab.on_page_input_return()
        assert reader_tab.current_page_index == 4


def test_on_page_input_return_invalid(reader_tab):
    reader_tab.current_doc = MagicMock()
    reader_tab.current_doc.page_count = 10
    reader_tab.current_page_index = 2
    reader_tab.txt_page.setText("15")

    reader_tab.on_page_input_return()
    assert reader_tab.current_page_index == 2
    assert reader_tab.txt_page.text() == "3"


@patch("riemann.ui.reader.tab.QMessageBox.information")
@patch("riemann.ui.reader.tab.QFileDialog.getSaveFileName")
@patch("riemann.ui.reader.tab.shutil.copy2")
def test_save_document(mock_copy, mock_get_save, mock_info, reader_tab):
    reader_tab.current_path = "/tmp/test.pdf"
    mock_get_save.return_value = ("/tmp/saved.pdf", "")

    with patch.object(reader_tab.settings, "value", return_value=""):
        with patch("os.path.exists", return_value=True):
            reader_tab.save_document()
            mock_copy.assert_called_once_with("/tmp/test.pdf", "/tmp/saved.pdf")
            mock_info.assert_called_once()


@patch("riemann.ui.reader.tab.QMessageBox.warning")
def test_save_document_no_doc(mock_warning, reader_tab):
    reader_tab.current_path = None
    reader_tab.save_document()
    mock_warning.assert_called_once()


def test_key_press_event(reader_tab):
    reader_tab.current_doc = MagicMock()
    reader_tab.current_doc.page_count = 5
    reader_tab.current_page_index = 0
    reader_tab.view_mode = ViewMode.IMAGE

    with patch.object(reader_tab, "next_view") as mock_next:
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier
        )
        reader_tab.keyPressEvent(event)
        mock_next.assert_called_once()

    with patch.object(reader_tab, "prev_view") as mock_prev:
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier
        )
        reader_tab.keyPressEvent(event)
        mock_prev.assert_called_once()


def test_event_filter_snipping(reader_tab):
    page_widget = PageWidget()
    reader_tab.is_snipping = True

    press_event = MagicMock(
        type=lambda: QEvent.Type.MouseButtonPress, pos=lambda: QPoint(10, 10)
    )
    result = reader_tab.eventFilter(page_widget, press_event)

    assert result is True
    assert reader_tab.snip_band is not None
    assert reader_tab.snip_start == QPoint(10, 10)


def test_tab_switch_focus_next_prev_child(reader_tab):
    """Item 7: focusNextPrevChild must not advance page."""
    reader_tab.current_page_index = 3
    with (
        patch.object(reader_tab, "next_view") as mock_next,
        patch.object(reader_tab, "prev_view") as mock_prev,
    ):
        result = reader_tab.focusNextPrevChild(True)
        assert result is False or result is True  # normal Qt focus navigation result
        mock_next.assert_not_called()
        mock_prev.assert_not_called()
        assert reader_tab.current_page_index == 3


def test_page_indicator_across_all_display_modes(reader_tab):
    """Item 6: Page indicator popup appears across all display modes (0, 1, 2)."""
    reader_tab.current_doc = MagicMock()
    reader_tab.current_doc.page_count = 10
    reader_tab.current_page_index = 2

    for state in (0, 1, 2):
        reader_tab._fullscreen_state = state
        reader_tab.page_indicator_popup.hide()
        reader_tab._trigger_fullscreen_page_indicator()
        assert not reader_tab.page_indicator_popup.isHidden()
        assert reader_tab.page_indicator_popup.text() == "3 / 10"


def test_fullscreen_page_indicator_fade_lifecycle(reader_tab):
    """Item 6: Opacity fade out animation and cleanup."""
    reader_tab.page_indicator_popup.show()
    assert not reader_tab.page_indicator_popup.isHidden()

    reader_tab._start_page_indicator_fade()
    assert reader_tab._page_indicator_fade_anim is not None

    reader_tab.page_indicator_opacity.setOpacity(0.0)
    reader_tab._on_page_indicator_fade_finished()
    assert reader_tab.page_indicator_popup.isHidden()



def test_comment_interaction_events(reader_tab):
    """Item 5: Comment hover and click on PageWidget."""
    page_widget = PageWidget()
    page_widget.setCursor = MagicMock()
    reader_tab.page_widgets = {0: page_widget}

    dummy_comment = {
        "page": 0,
        "author": "Alice",
        "contents": "Check this",
        "rel_rect": (0.1, 0.1, 0.5, 0.5),
    }
    reader_tab._get_comment_at_pos = MagicMock(return_value=dummy_comment)

    # Hover event (MouseMove)
    move_event = MagicMock()
    move_event.type.return_value = QEvent.Type.MouseMove
    move_event.pos.return_value = QPoint(100, 100)

    reader_tab.eventFilter(page_widget, move_event)
    page_widget.setCursor.assert_called_with(Qt.CursorShape.PointingHandCursor)

    # Click event (MouseButtonRelease)
    release_event = MagicMock()
    release_event.type.return_value = QEvent.Type.MouseButtonRelease
    release_event.pos.return_value = QPoint(100, 100)
    release_event.button.return_value = Qt.MouseButton.LeftButton

    with patch.object(reader_tab, "show_external_comment_dialog") as mock_show_dlg:
        reader_tab.eventFilter(page_widget, release_event)
        mock_show_dlg.assert_called_once_with(dummy_comment)


