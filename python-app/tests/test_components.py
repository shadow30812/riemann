from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QColorDialog, QInputDialog, QMenu, QWidget
from riemann.ui.components import AnnotationToolbar, DraggableTabBar, DraggableTabWidget


@pytest.fixture
def app(qtbot):
    return qtbot


def test_draggable_tab_widget_init(qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)
    assert widget.acceptDrops() is True
    assert widget.isMovable() is True
    assert widget.tabBar().isMovable() is True
    assert isinstance(widget.tabBar(), DraggableTabBar)


def test_draggable_tab_widget_drag_enter(qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)
    mock_event = MagicMock(spec=QDragEnterEvent)
    mock_event.mimeData().hasText.return_value = True
    widget.dragEnterEvent(mock_event)
    mock_event.accept.assert_called_once()


def test_draggable_tab_widget_drag_enter_ignore(qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)
    mock_event = MagicMock(spec=QDragEnterEvent)
    mock_event.mimeData().hasText.return_value = False
    mock_event.mimeData().hasFormat.return_value = False
    widget.dragEnterEvent(mock_event)
    mock_event.ignore.assert_called_once()


@patch("os.path.exists", return_value=True)
@patch("riemann.ui.reader.ReaderTab")
def test_draggable_tab_widget_drop(mock_reader_tab, mock_exists, qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)

    dummy_tab = QWidget()
    dummy_tab.load_document = MagicMock()
    mock_reader_tab.return_value = dummy_tab

    mock_event = MagicMock(spec=QDropEvent)
    mock_event.mimeData().hasFormat.return_value = False
    mock_event.mimeData().text.return_value = "/fake/path.pdf"
    widget.dropEvent(mock_event)
    mock_event.acceptProposedAction.assert_called_once()


@patch.object(QMenu, "exec")
@patch.object(QInputDialog, "getText", return_value=("New Name", True))
def test_draggable_tab_bar_context_menu(mock_get_text, mock_exec, qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)
    child = QWidget()
    widget.addTab(child, "Old Name")
    bar = widget.tabBar()
    mock_event = MagicMock()
    mock_event.pos.return_value = QPoint(5, 5)
    mock_event.globalPos.return_value = QPoint(10, 10)
    mock_action = MagicMock()
    mock_exec.return_value = mock_action

    with patch.object(QMenu, "addAction", return_value=mock_action):
        bar.contextMenuEvent(mock_event)


def test_annotation_toolbar_init(qtbot):
    toolbar = AnnotationToolbar()
    qtbot.addWidget(toolbar)
    assert toolbar.btn_nav.isChecked() is True


def test_annotation_toolbar_signals(qtbot):
    toolbar = AnnotationToolbar()
    qtbot.addWidget(toolbar)
    with qtbot.waitSignal(toolbar.tool_changed, timeout=1000) as blocker:
        toolbar.btn_pen.click()
    assert blocker.args == ["pen"]


def test_annotation_toolbar_undo_redo(qtbot):
    toolbar = AnnotationToolbar()
    qtbot.addWidget(toolbar)
    with qtbot.waitSignal(toolbar.undo_requested, timeout=1000):
        toolbar.btn_undo.click()
    with qtbot.waitSignal(toolbar.redo_requested, timeout=1000):
        toolbar.btn_redo.click()


@patch.object(QColorDialog, "getColor")
def test_annotation_toolbar_color_picker(mock_get_color, qtbot):
    toolbar = AnnotationToolbar()
    qtbot.addWidget(toolbar)
    mock_color = MagicMock()
    mock_color.isValid.return_value = True
    mock_color.name.return_value = "#ff0000"
    mock_get_color.return_value = mock_color
    with qtbot.waitSignal(toolbar.color_changed, timeout=1000) as blocker:
        toolbar._pick_color()
    assert blocker.args == ["#ff0000"]


def test_annotation_toolbar_thickness(qtbot):
    toolbar = AnnotationToolbar()
    qtbot.addWidget(toolbar)
    with qtbot.waitSignal(toolbar.thickness_changed, timeout=1000) as blocker:
        toolbar.spin_thick.setValue(10)
    assert blocker.args == [10]


def test_build_open_with_menu(qtbot, tmp_path):
    from riemann.ui.components import build_open_with_menu

    def labels(menu):
        return [a.text() for a in menu.actions()]

    assert "System Default Browser" in labels(build_open_with_menu(None, "https://example.com"))
    assert "System File Manager" in labels(build_open_with_menu(None, str(tmp_path)))

    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert "System Default Application" in labels(build_open_with_menu(None, str(pdf)))
    assert labels(build_open_with_menu(None, str(tmp_path / "missing.pdf"))) == ["Nothing to open"]


def test_tab_bar_drop_index(qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)
    for name in ("A", "B", "C"):
        widget.addTab(QWidget(), name)
    widget.resize(600, 200)
    widget.show()
    bar = widget.tabBar()
    assert bar.drop_index(bar.tabRect(0).topLeft()) == 0
    assert bar.drop_index(bar.tabRect(1).center() + QPoint(5, 0)) == 2
    assert bar.drop_index(QPoint(bar.width() - 1, 5)) == 3


def test_drop_back_into_same_strip_moves_tab(qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)
    pages = [QWidget() for _ in range(3)]
    for page, name in zip(pages, "ABC"):
        widget.addTab(page, name)
    DraggableTabBar._dragged_widget = pages[0]
    DraggableTabBar._dragged_source = widget
    assert DraggableTabBar.insert_dragged_tab(widget, 3)
    assert [widget.tabText(i) for i in range(3)] == ["B", "C", "A"]
    assert widget.currentWidget() is pages[0]


def test_drop_into_other_strip_takes_tab_out_of_source(qtbot):
    source, target = DraggableTabWidget(), DraggableTabWidget()
    qtbot.addWidget(source)
    qtbot.addWidget(target)
    page = QWidget()
    source.addTab(page, "A")
    source.addTab(QWidget(), "B")
    target.addTab(QWidget(), "C")
    DraggableTabBar._dragged_widget = page
    DraggableTabBar._dragged_source = source
    DraggableTabBar._dragged_title = "A"
    assert DraggableTabBar.insert_dragged_tab(target, 0)
    assert [source.tabText(i) for i in range(source.count())] == ["B"]
    assert target.indexOf(page) == 0
    DraggableTabBar._dragged_source = None


def test_switching_to_vertical_tabs_and_back_measures_the_tabs_again(qtbot):
    tabs = DraggableTabWidget()
    qtbot.addWidget(tabs)
    for title in ("New Tab", "attention.pdf"):
        tabs.addTab(QWidget(), title)
    tabs.resize(900, 600)
    tabs.show()
    bar = tabs.tabBar()
    horizontal = [bar.tabRect(i).size() for i in range(bar.count())]

    tabs.set_vertical_tabs(True)
    assert all(bar.tabRect(i).width() == DraggableTabBar.VERTICAL_TAB_WIDTH for i in range(bar.count()))
    assert bar.width() >= DraggableTabBar.VERTICAL_TAB_WIDTH

    tabs.set_vertical_tabs(False)
    assert [bar.tabRect(i).size() for i in range(bar.count())] == horizontal


def test_find_field_repaints_whole_after_the_caret_blinks(qapp, qtbot):
    import time

    from PySide6.QtCore import QCoreApplication

    from riemann.ui.components import RepaintingLineEdit

    painted = []

    class Field(RepaintingLineEdit):
        def paintEvent(self, event):
            painted.append(event.rect())
            super().paintEvent(event)

    field = Field()
    qtbot.addWidget(field)
    field.resize(300, 30)
    field.setText("attention")
    field.show()
    field.activateWindow()
    field.setFocus()
    end = time.time() + 1.5
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    partial = [i for i, rect in enumerate(painted) if rect != field.rect()]
    assert partial, "the caret never blinked"
    for i in partial:
        assert field.rect() in painted[i + 1 :], painted


def test_reader_find_boxes_repaint_whole(qapp):
    from riemann.ui.components import RepaintingLineEdit
    from riemann.ui.reader.tab import ReaderTab

    tab = ReaderTab()
    try:
        assert isinstance(tab.txt_search, RepaintingLineEdit)
        assert isinstance(tab.txt_ai_search, RepaintingLineEdit)
    finally:
        tab.cleanup()
        tab.deleteLater()
