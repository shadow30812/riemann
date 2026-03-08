import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QListWidgetItem, QTabWidget, QWidget
from riemann.core.constants import ViewMode, ZoomMode
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.reader.widgets import PageWidget

sys.modules["riemann_core"] = MagicMock()


@pytest.fixture
def app(qtbot):
    return qtbot


@pytest.fixture
def reader(qtbot):
    with patch("riemann.ui.reader.tab.ReaderTab._init_backend"):
        widget = ReaderTab()
        qtbot.addWidget(widget)
        return widget


def test_init(reader):
    assert reader.current_page_index == 0
    assert reader.dark_mode is not None
    assert reader.continuous_scroll is True


def test_init_shortcuts(reader):
    assert len(reader.findChildren(QTimer)) >= 1


def test_get_tab_widget(reader, qtbot):
    parent_tab = QTabWidget()
    qtbot.addWidget(parent_tab)
    parent_tab.addTab(reader, "Test")
    assert reader._get_tab_widget() == parent_tab


def test_cycle_tab(reader, qtbot):
    parent_tab = QTabWidget()
    qtbot.addWidget(parent_tab)
    parent_tab.addTab(reader, "Test 1")
    parent_tab.addTab(QWidget(), "Test 2")
    parent_tab.setCurrentIndex(0)
    reader.cycle_tab(1)
    assert parent_tab.currentIndex() == 1
    reader.cycle_tab(-1)
    assert parent_tab.currentIndex() == 0


def test_update_tab_title(reader, qtbot):
    parent_tab = QTabWidget()
    qtbot.addWidget(parent_tab)
    parent_tab.addTab(reader, "Old Title")
    mock_window = MagicMock()

    with patch.object(reader, "window", return_value=mock_window):
        reader._update_tab_title("Very Long Title That Needs Truncation Here")

    assert parent_tab.tabText(0) == "Very Long Title That Need.."
    mock_window._update_window_title.assert_called_once()


def test_init_backend(qtbot):
    with patch("riemann.riemann_core.PdfEngine") as mock_engine:
        widget = ReaderTab()
        qtbot.addWidget(widget)
        mock_engine.assert_called_once()


def test_select_all_text_reflow(reader):
    reader.view_mode = ViewMode.REFLOW
    reader.web.page = MagicMock()
    reader.select_all_text()
    reader.web.page().triggerAction.assert_called_with(
        QWebEnginePage.WebAction.SelectAll
    )


@patch.object(ReaderTab, "show_toast")
def test_select_all_text_image(mock_toast, reader):
    reader.view_mode = ViewMode.IMAGE
    reader.select_all_text()
    mock_toast.assert_called_once()


@patch.object(ReaderTab, "_populate_home_recents")
def test_showEvent(mock_pop, reader):
    reader.view_mode = ViewMode.REFLOW
    reader.stack.setCurrentIndex(2)
    with patch.object(reader.web, "setFocus") as mock_focus:
        event = QEvent(QEvent.Type.Show)
        reader.showEvent(event)
        mock_focus.assert_called_once()
        mock_pop.assert_called_once()


def test_populate_home_recents(reader):
    mock_window = MagicMock()
    mock_window.history_manager.history = {"pdf": ["/fake/1.pdf", "/fake/2.pdf"]}
    with patch.object(reader, "window", return_value=mock_window):
        with patch("os.path.exists", return_value=True):
            reader._populate_home_recents()
    assert reader.list_recent.count() == 2


@patch.object(ReaderTab, "_load_markdown")
def test_load_document_md(mock_md, reader):
    reader.load_document("test.md")
    mock_md.assert_called_with("test.md")


@patch.object(ReaderTab, "index_pdf_for_ai")
@patch.object(ReaderTab, "load_annotations")
@patch.object(ReaderTab, "_probe_base_page_size")
def test_load_document_pdf(mock_probe, mock_anno, mock_ai, reader):
    reader.engine = MagicMock()
    reader.engine.load_document.return_value.page_count = 5
    reader.load_document("test.pdf", restore_state=False)
    assert reader.current_page_index == 0
    assert reader.view_mode == ViewMode.IMAGE


@patch("riemann.ui.reader.tab.generate_markdown_html", return_value="<html></html>")
@patch("builtins.open", new_callable=mock_open, read_data="# Test")
def test_load_markdown(mock_file, mock_gen, reader):
    reader._load_markdown("test.md")
    assert reader.view_mode == ViewMode.REFLOW
    assert reader.stack.currentIndex() == 1


@patch("shutil.copy2")
@patch(
    "PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("/fake/dest.pdf", "")
)
@patch("os.path.exists", return_value=True)
def test_save_document_success(mock_exists, mock_dialog, mock_copy, reader):
    reader.current_path = "/fake/src.pdf"
    with patch("PySide6.QtWidgets.QMessageBox.information") as mock_msg:
        reader.save_document()
        mock_copy.assert_called_with("/fake/src.pdf", "/fake/dest.pdf")
        mock_msg.assert_called_once()


@patch(
    "PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("/fake/dest.md", "")
)
def test_export_annotations(mock_dialog, reader):
    reader.current_path = "/fake/src.pdf"
    reader.annotations = {"0": [{"type": "note", "text": "test note"}]}
    with patch("builtins.open", new_callable=mock_open) as mock_file:
        with patch("riemann.ui.reader.tab.QApplication.setOverrideCursor"):
            with patch("riemann.ui.reader.tab.QApplication.restoreOverrideCursor"):
                reader.export_annotations()
    mock_file().write.assert_any_call("- **Note:** test note\n")


def test_defer_scroll_update(reader):
    with patch.object(reader.scroll_timer, "start") as mock_start:
        reader.defer_scroll_update(100)
        assert reader.txt_page.text() == "1"
        mock_start.assert_called_once()


@patch.object(ReaderTab, "on_scroll_changed")
def test_real_scroll_handler(mock_scroll, reader):
    reader.real_scroll_handler()
    mock_scroll.assert_called_once()


@patch.object(ReaderTab, "render_visible_pages")
def test_on_scroll_changed(mock_render, reader):
    mock_widget = MagicMock()
    mock_widget.mapTo.return_value = QPoint(0, 100)
    mock_widget.height.return_value = 200
    reader.page_widgets = {1: mock_widget}
    reader.current_doc = MagicMock()

    reader.on_scroll_changed(50)
    assert reader.current_page_index == 1
    mock_render.assert_called_once()


def test_ensure_visible(reader):
    mock_widget = MagicMock()
    reader.page_widgets = {2: mock_widget}
    with patch.object(reader.scroll, "ensureWidgetVisible") as mock_ensure:
        reader.ensure_visible(2)
        mock_ensure.assert_called_with(mock_widget, 0, 0)


@patch.object(ReaderTab, "update_view")
def test_next_prev_view(mock_update, reader):
    reader.current_doc = MagicMock()
    reader.current_doc.page_count = 5
    reader.current_page_index = 0
    reader.facing_mode = False

    reader.next_view()
    assert reader.current_page_index == 1

    reader.prev_view()
    assert reader.current_page_index == 0


@patch.object(ReaderTab, "update_view")
def test_toggle_view_mode(mock_update, reader):
    reader.view_mode = ViewMode.IMAGE
    reader.toggle_view_mode()
    assert reader.view_mode == ViewMode.REFLOW
    assert reader.stack.currentIndex() == 1


@patch.object(ReaderTab, "update_view")
@patch.object(ReaderTab, "rebuild_layout")
def test_toggle_facing_mode(mock_rebuild, mock_update, reader):
    reader.facing_mode = False
    reader.toggle_facing_mode()
    assert reader.facing_mode is True
    mock_rebuild.assert_called_once()


@patch.object(ReaderTab, "update_view")
@patch.object(ReaderTab, "rebuild_layout")
def test_toggle_scroll_mode(mock_rebuild, mock_update, reader):
    reader.continuous_scroll = True
    reader.toggle_scroll_mode()
    assert reader.continuous_scroll is False
    mock_rebuild.assert_called_once()


@patch(
    "PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=("/fake/new.pdf", "")
)
@patch.object(ReaderTab, "load_document")
def test_open_pdf_dialog(mock_load, mock_dialog, reader):
    reader.open_pdf_dialog()
    mock_load.assert_called_with("/fake/new.pdf")


def test_scroll_page(reader):
    reader.scroll.verticalScrollBar().setValue(100)
    reader.scroll.viewport().setHeight(500)
    reader.scroll_page(1)
    assert reader.scroll.verticalScrollBar().value() > 100


@patch.object(ReaderTab, "ensure_visible")
@patch.object(ReaderTab, "update_view")
def test_on_page_input_return(mock_update, mock_ensure, reader):
    reader.current_doc = MagicMock()
    reader.current_doc.page_count = 10
    reader.txt_page.setText("5")
    reader.on_page_input_return()
    assert reader.current_page_index == 4


def test_show_toast(reader):
    reader.show_toast("Test Toast")
    assert hasattr(reader, "lbl_toast")
    assert reader.lbl_toast.isVisible() is True
    assert reader.lbl_toast.text() == "Test Toast"


@patch.object(ReaderTab, "toggle_reader_fullscreen")
def test_keyPressEvent_escape(mock_fullscreen, reader):
    reader._reader_fullscreen = True
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
    )
    reader.keyPressEvent(event)
    mock_fullscreen.assert_called_once()


@patch.object(ReaderTab, "next_view")
def test_keyPressEvent_right(mock_next, reader):
    reader.view_mode = ViewMode.IMAGE
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier
    )
    reader.keyPressEvent(event)
    mock_next.assert_called_once()


@patch.object(ReaderTab, "on_zoom_changed_internal")
def test_wheelEvent_zoom(mock_zoom, reader):
    event = QWheelEvent(
        QPoint(0, 0),
        QPoint(0, 0),
        QPoint(0, 120),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    reader.manual_scale = 1.0
    reader.wheelEvent(event)
    assert reader.zoom_mode == ZoomMode.MANUAL
    assert reader.manual_scale == 1.1


@patch.object(ReaderTab, "apply_zoom_string")
def test_on_zoom_selected(mock_apply, reader):
    reader.combo_zoom.setCurrentText("75%")
    reader.on_zoom_selected(3)
    mock_apply.assert_called_with("75%")


@patch.object(ReaderTab, "apply_zoom_string")
def test_on_zoom_text_entered(mock_apply, reader):
    reader.combo_zoom.lineEdit().setText("130%")
    reader.on_zoom_text_entered()
    mock_apply.assert_called_with("130%")


@patch.object(ReaderTab, "on_zoom_changed_internal")
def test_apply_zoom_string(mock_internal, reader):
    reader.apply_zoom_string("Fit Width")
    assert reader.zoom_mode == ZoomMode.FIT_WIDTH
    reader.apply_zoom_string("200%")
    assert reader.zoom_mode == ZoomMode.MANUAL
    assert reader.manual_scale == 2.0


@patch.object(ReaderTab, "_update_all_widget_sizes")
@patch.object(ReaderTab, "update_view")
def test_on_zoom_changed_internal(mock_update, mock_sizes, reader):
    reader.zoom_mode = ZoomMode.FIT_WIDTH
    reader.on_zoom_changed_internal()
    mock_sizes.assert_called_once()
    mock_update.assert_called_once()
    assert reader.combo_zoom.currentText() == "Fit Width"


@patch.object(ReaderTab, "on_zoom_changed_internal")
def test_zoom_step(mock_internal, reader):
    reader.manual_scale = 1.0
    reader.zoom_step(1.5)
    assert reader.manual_scale == 1.5
    assert reader.zoom_mode == ZoomMode.MANUAL


@patch.object(ReaderTab, "update_view")
def test_toggle_theme(mock_update, reader):
    initial = reader.dark_mode
    reader.toggle_theme()
    assert reader.dark_mode != initial
    mock_update.assert_called_once()


@patch("os.path.exists", return_value=True)
@patch("os.path.isfile", return_value=True)
@patch.object(ReaderTab, "load_document")
def test_on_home_path_entered(mock_load, mock_isfile, mock_exists, reader):
    reader.txt_open_path.setText('"/fake/path.pdf"')
    reader._on_home_path_entered()
    mock_load.assert_called_with("/fake/path.pdf")


@patch("os.path.exists", return_value=True)
@patch.object(ReaderTab, "load_document")
def test_on_recent_item_clicked(mock_load, mock_exists, reader):
    item = QListWidgetItem()
    item.setData(Qt.ItemDataRole.UserRole, "/fake/recent.pdf")
    reader._on_recent_item_clicked(item)
    mock_load.assert_called_with("/fake/recent.pdf")


@patch.object(ReaderTab, "process_snip")
def test_eventFilter_snip(mock_process, reader):
    reader.is_snipping = True
    page = PageWidget()
    page.setProperty("pageIndex", 0)

    press = QEvent(QEvent.Type.MouseButtonPress)
    press.pos = MagicMock(return_value=QPoint(10, 10))
    reader.eventFilter(page, press)
    assert reader.snip_band is not None

    release = QEvent(QEvent.Type.MouseButtonRelease)
    reader.snip_band.setGeometry(QRect(10, 10, 50, 50))
    reader.eventFilter(page, release)
    mock_process.assert_called_once()


@patch.object(ReaderTab, "create_new_annotation")
def test_eventFilter_annotation(mock_create, reader):
    reader.anno_toolbar.setVisible(True)
    reader.current_tool = "note"
    page = PageWidget()
    page.setProperty("pageIndex", 0)
    page.resize(100, 100)

    with patch.object(reader, "handle_annotation_click", return_value=False):
        press = QEvent(QEvent.Type.MouseButtonPress)
        press.pos = MagicMock(return_value=QPoint(50, 50))
        reader.eventFilter(page, press)
        mock_create.assert_called_with(0, 0.5, 0.5)


@patch.object(ReaderTab, "_add_anno_data")
def test_eventFilter_drawing(mock_add, reader):
    reader.anno_toolbar.setVisible(True)
    reader.current_tool = "pen"
    page = PageWidget()
    page.setProperty("pageIndex", 0)
    page.resize(100, 100)

    press = QEvent(QEvent.Type.MouseButtonPress)
    press.pos = MagicMock(return_value=QPoint(10, 10))
    reader.eventFilter(page, press)

    release = QEvent(QEvent.Type.MouseButtonRelease)
    reader.eventFilter(page, release)
    mock_add.assert_called_once()
