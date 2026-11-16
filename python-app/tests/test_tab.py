"""
Notes:

reader_tab: No event loop runs under pytest, so flush the deletion explicitly;
otherwise the tab's QWebEngineView outlives the default profile.
"""

from contextlib import contextmanager
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from riemann.core.constants import ViewMode, ZoomMode
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.reader.widgets import PageWidget



@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@contextmanager
def patched_settings():
    """Patches app_settings everywhere ReaderTab and its mixins create one."""
    mock_settings = MagicMock()
    with patch("riemann.ui.reader.tab.app_settings", mock_settings), patch(
        "riemann.ui.reader.mixins.appearance.app_settings", mock_settings
    ):
        yield mock_settings


def fake_setting(key, default=None, type=None):
    """Every setting reads True except the notes panel, which would narrow the page."""
    return False if key == "reader/notes_visible" else True


@pytest.fixture
def reader_tab(qapp):
    with patched_settings() as mock_settings:
        mock_settings.return_value.value.side_effect = fake_setting
        tab = ReaderTab()
        yield tab
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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
    reader_tab.stack.setCurrentIndex(0)
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


@patch("riemann.ui.reader.mixins.document.QMessageBox.information")
@patch("riemann.ui.reader.mixins.document.QFileDialog.getSaveFileName")
@patch("riemann.ui.reader.mixins.document.shutil.copy2")
def test_save_document(mock_copy, mock_get_save, mock_info, reader_tab):
    reader_tab.current_path = "/tmp/test.pdf"
    mock_get_save.return_value = ("/tmp/saved.pdf", "")

    with patch.object(reader_tab.settings, "value", return_value=""):
        with patch("os.path.exists", return_value=True):
            reader_tab.save_document()
            mock_copy.assert_called_once_with("/tmp/test.pdf", "/tmp/saved.pdf")
            mock_info.assert_called_once()


@patch("riemann.ui.reader.mixins.document.QMessageBox.warning")
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
        assert result is False or result is True
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

    move_event = MagicMock()
    move_event.type.return_value = QEvent.Type.MouseMove
    move_event.pos.return_value = QPoint(100, 100)

    reader_tab.eventFilter(page_widget, move_event)
    page_widget.setCursor.assert_called_with(Qt.CursorShape.PointingHandCursor)

    release_event = MagicMock()
    release_event.type.return_value = QEvent.Type.MouseButtonRelease
    release_event.pos.return_value = QPoint(100, 100)
    release_event.button.return_value = Qt.MouseButton.LeftButton

    with patch.object(reader_tab, "show_external_comment_dialog") as mock_show_dlg:
        reader_tab.eventFilter(page_widget, release_event)
        mock_show_dlg.assert_called_once_with(dummy_comment)




def test_home_page_follows_the_interface_theme(reader_tab):
    assert reader_tab._home_dark is True
    reader_tab.apply_ui_theme(False)
    assert reader_tab._home_dark is False
    assert "#FFFFFF" in reader_tab.home_page_widget.styleSheet()
    reader_tab.apply_ui_theme(True)
    assert "#232428" in reader_tab.home_page_widget.styleSheet()


def test_tab_theme_toggle_switches_the_home_page_and_keeps_it(reader_tab):
    window = MagicMock()
    window.dark_mode = True
    window.history_manager.history = {"pdf": []}
    mode = reader_tab.theme_mode
    with (
        patch.object(reader_tab, "window", return_value=window),
        patch.object(reader_tab, "show_toast"),
    ):
        reader_tab.toggle_theme()
        assert reader_tab._home_dark is False
        assert "#FFFFFF" in reader_tab.home_page_widget.styleSheet()
        assert reader_tab.theme_mode == mode

        reader_tab._populate_home_recents()
        assert reader_tab._home_dark is False

        reader_tab.apply_ui_theme(True)
        assert reader_tab._home_dark is True
        reader_tab._populate_home_recents()
        assert reader_tab._home_dark is True


def test_home_page_lists_recent_documents_with_their_folder(reader_tab, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    window = MagicMock()
    window.dark_mode = True
    window.history_manager.get_list.return_value = [str(pdf), str(tmp_path / "gone.pdf")]
    with patch.object(reader_tab, "window", return_value=window):
        reader_tab._populate_home_recents()
    assert reader_tab.list_recent.count() == 1
    item = reader_tab.list_recent.item(0)
    assert item.data(Qt.ItemDataRole.UserRole) == str(pdf)
    assert reader_tab.lbl_recent_count.text() == "1"
    assert reader_tab.lbl_recent_empty.isHidden()


def test_preview_banner_follows_the_interface_theme(qapp):
    from riemann.ui.palette import DARK, LIGHT
    from riemann.ui.reader.tab import PreviewReaderTab

    with patched_settings() as mock_settings:
        mock_settings.return_value.value.return_value = True
        tab = PreviewReaderTab()
    try:
        assert DARK["text"] in tab.preview_banner.styleSheet()
        tab.apply_ui_theme(False)
        assert LIGHT["text"] in tab.preview_banner.styleSheet()
    finally:
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_zoom_step_starts_from_the_fit_scale():
    from riemann.ui.reader.tab import ReaderTab, ZoomMode

    class Fake:
        zoom_mode = ZoomMode.FIT_WIDTH
        manual_scale = 0.4
        changed = 0

        def calculate_scale(self):
            return self.manual_scale if self.zoom_mode == ZoomMode.MANUAL else 1.5

        def on_zoom_changed_internal(self):
            self.changed += 1

    fake = Fake()
    ReaderTab.zoom_step(fake, 0.9)
    assert fake.zoom_mode == ZoomMode.MANUAL
    assert abs(fake.manual_scale - 1.35) < 1e-9
    ReaderTab.zoom_step(fake, 0.9)
    assert abs(fake.manual_scale - 1.215) < 1e-9
    fake.manual_scale = 4.9
    ReaderTab.zoom_step(fake, 1.1)
    assert fake.manual_scale == 5.0


def test_toolbar_icons_are_found(reader_tab):
    for name in ("btn_zoom_in", "btn_zoom_out", "btn_search", "btn_print"):
        button = getattr(reader_tab, name, None)
        if button is not None:
            assert not button.icon().isNull(), name


CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "pdf17_spec.pdf")


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_back_and_forward_return_to_the_exact_spot(reader_tab, qapp):
    import time

    def pump(seconds, until=lambda: False):
        end = time.time() + seconds
        while time.time() < end and not until():
            qapp.processEvents()
            time.sleep(0.01)

    reader_tab.resize(1000, 800)
    reader_tab.show()
    reader_tab.load_document(os.path.abspath(CORPUS))
    pump(15, lambda: reader_tab.current_doc is not None)
    reader_tab.jump_to_page(9)
    bar = reader_tab.scroll.verticalScrollBar()
    bar.setValue(bar.value() + 300)
    reader_tab.update_view()
    start = bar.value()
    assert not reader_tab.btn_nav_back.isEnabled()

    reader_tab.navigate_to(400)
    reader_tab.navigate_to(600)
    reader_tab.go_back()
    assert reader_tab.current_page_index == 400
    reader_tab.go_back()
    assert (reader_tab.current_page_index, bar.value()) == (9, start)
    assert reader_tab.btn_nav_forward.isEnabled()
    reader_tab.go_forward()
    assert reader_tab.current_page_index == 400
    reader_tab.cleanup()


ATTENTION = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


@pytest.mark.skipif(not os.path.exists(ATTENTION), reason="benchmark corpus not downloaded")
def test_deleting_a_page_moves_annotations_with_it(reader_tab, qapp, tmp_path, monkeypatch):
    import shutil
    import time

    from PySide6.QtWidgets import QMessageBox

    from riemann.core import annotation_store, pdf_annotations

    monkeypatch.setattr(annotation_store, "_shared", annotation_store.AnnotationStore(str(tmp_path / "store")))
    monkeypatch.setattr("riemann.ui.reader.mixins.pages.QMessageBox.question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    def pump(seconds, until=lambda: False):
        end = time.time() + seconds
        while time.time() < end and not until():
            qapp.processEvents()
            time.sleep(0.01)

    path = str(tmp_path / "paper.pdf")
    shutil.copyfile(ATTENTION, path)
    reader_tab.resize(1000, 800)
    reader_tab.show()
    reader_tab.load_document(path)
    pump(15, lambda: reader_tab.current_doc is not None)
    pump(1)
    reader_tab.annotations = {"2": [{"id": "n1", "type": "note", "rel_pos": [0.5, 0.5], "text": "keep me", "color": "#ff0000"}]}
    reader_tab.save_annotations()
    reader_tab.flush_annotations_to_pdf()
    reader_tab._store_annotations_in_pdf(blocking=True)

    reader_tab.delete_pages([0])
    pump(15, lambda: reader_tab.current_doc is not None and reader_tab.current_doc.page_count == 14)
    assert reader_tab.current_doc.page_count == 14
    assert list(reader_tab.annotations) == ["1"]
    assert pdf_annotations.read_annotations(path)["1"][0]["text"] == "keep me"
    reader_tab.cleanup()


def test_form_entries_are_saved_into_the_pdf(reader_tab, qapp, tmp_path, monkeypatch):
    import time

    from PySide6.QtWidgets import QCheckBox, QLineEdit

    from form_fixtures import make_form_pdf
    from riemann.core import annotation_store
    from riemann.core.pdf_forms import read_form_values

    monkeypatch.setattr(annotation_store, "_shared", annotation_store.AnnotationStore(str(tmp_path / "store")))
    monkeypatch.setattr("riemann.ui.reader.mixins.forms.store_in_pdf_enabled", lambda: True)

    def pump(seconds, until=lambda: False):
        end = time.time() + seconds
        while time.time() < end and not until():
            qapp.processEvents()
            time.sleep(0.01)

    path = make_form_pdf(str(tmp_path / "form.pdf"))
    reader_tab.resize(1000, 900)
    reader_tab.show()
    reader_tab.load_document(path)
    pump(15, lambda: reader_tab.current_doc is not None and reader_tab.form_widgets.get(0))
    controls = reader_tab.form_widgets[0]
    next(c for c in controls if isinstance(c, QLineEdit)).setText("Grace Hopper")
    next(c for c in controls if isinstance(c, QCheckBox)).setChecked(True)
    reader_tab.flush_forms_to_pdf()
    assert read_form_values(path)["name"] == "Grace Hopper"
    assert read_form_values(path)["agree"] is True
    reader_tab.cleanup()


def test_new_toast_replaces_the_one_on_screen(reader_tab):
    from PySide6.QtWidgets import QLabel

    reader_tab.show_toast("first")
    reader_tab.show_toast("second")
    shown = [
        label.text()
        for label in reader_tab.findChildren(QLabel)
        if label.text() in ("first", "second") and not label.isHidden()
    ]
    assert shown == ["second"]


def _icon_is_light(icon) -> bool:
    image = icon.pixmap(20, 20).toImage()
    lightness = [
        image.pixelColor(x, y).lightness()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 128
    ]
    return sum(lightness) / len(lightness) > 128


def test_reader_chrome_follows_the_page_theme_not_the_app_theme(reader_tab):
    from PySide6.QtWidgets import QToolButton
    from riemann.ui.palette import DARK

    reader_tab.theme_mode = 1
    reader_tab.apply_theme()
    reader_tab.apply_ui_theme(False)

    assert reader_tab.sidebar_nav.dark_mode is True
    assert DARK["canvas"] in reader_tab.scroll.styleSheet()
    assert "QScrollBar" in reader_tab.sidebar_nav.styleSheet()
    flyouts = reader_tab.toolbar_layout.rail.findChildren(QToolButton, "railFlyout")
    assert flyouts and all(_icon_is_light(b.icon()) for b in flyouts)


def test_page_number_box_is_outlined_in_both_page_themes(reader_tab):
    for mode in (0, 1):
        reader_tab.theme_mode = mode
        reader_tab.apply_theme()
        sheet = reader_tab.toolbar.styleSheet()
        assert "QLineEdit {" in sheet and "border: 1px solid" in sheet.split("QLineEdit {", 1)[1].split("}")[0]


def test_full_screen_button_reaches_the_window(reader_tab):
    from PySide6.QtWidgets import QWidget

    class Window(QWidget):
        calls = 0

        def toggle_reader_fullscreen(self):
            Window.calls += 1

    window = Window()
    with patch("riemann.app.RiemannWindow", Window), patch.object(reader_tab, "window", return_value=window):
        reader_tab.btn_fullscreen.click()
    assert Window.calls == 1


def test_scroll_arriving_after_the_document_closed_is_ignored(reader_tab):
    reader_tab.current_doc = None
    reader_tab._virtual_enabled = True
    reader_tab._ignore_scroll = False
    reader_tab.real_scroll_handler()


def test_sidebar_button_follows_the_page_theme(reader_tab):
    reader_tab.theme_mode = 1
    reader_tab.apply_theme()
    assert _icon_is_light(reader_tab.btn_toggle_sidebar.icon())
    reader_tab.theme_mode = 0
    reader_tab.apply_theme()
    assert not _icon_is_light(reader_tab.btn_toggle_sidebar.icon())


def test_every_toolbar_icon_follows_the_page_theme(reader_tab):
    from PySide6.QtWidgets import QAbstractButton

    reader_tab.theme_mode = 1
    reader_tab.apply_theme()
    containers = [reader_tab.toolbar, reader_tab.toolbar_layout.parking]
    if reader_tab.toolbar_layout.rail is not None:
        containers.append(reader_tab.toolbar_layout.rail)
    dark_icons = sorted(
        b.toolTip() or b.objectName()
        for c in containers
        for b in c.findChildren(QAbstractButton)
        if not b.icon().isNull() and not _icon_is_light(b.icon())
    )
    assert dark_icons == []
