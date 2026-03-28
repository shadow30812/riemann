from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QColorDialog, QInputDialog, QMenu, QWidget
from riemann.ui.components import AnnotationToolbar, DraggableTabBar, DraggableTabWidget


@pytest.fixture
def app(qtbot):
    return qtbot


def test_draggable_tab_widget_init(qtbot):
    widget = DraggableTabWidget()
    qtbot.addWidget(widget)
    assert widget.acceptDrops() is True
    assert widget.isMovable() is False
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
    mock_event.mimeData().text.return_value = "/fake/path.pdf"
    widget.dropEvent(mock_event)
    mock_event.acceptProposedAction.assert_called_once()


def test_draggable_tab_bar_mouse_move_ignore(qtbot):
    bar = DraggableTabBar()
    qtbot.addWidget(bar)
    mock_event = MagicMock(spec=QMouseEvent)
    mock_event.buttons.return_value = Qt.MouseButton.RightButton
    bar.mouseMoveEvent(mock_event)


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
