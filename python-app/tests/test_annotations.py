from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from riemann.ui.reader.mixins.annotations import AnnotationsMixin


class DummyAnnotationReader(AnnotationsMixin):
    def __init__(self):
        self.current_path = "/fake/path/doc.pdf"
        self.annotations = {}
        self.undo_stack = []
        self.redo_stack = []
        self.rendered_pages = set()
        self.pen_color = "#000000"

        self.anno_toolbar = MagicMock()
        self.btn_annotate = MagicMock()
        self.current_tool = "nav"

    def setCursor(self, cursor):
        self.cursor = cursor

    def render_visible_pages(self):
        pass


@pytest.fixture
def reader():
    return DummyAnnotationReader()


def test_get_annotation_path(reader):
    path = reader._get_annotation_path()
    assert "riemann" in path
    assert "annotations" in path
    assert path.endswith(".json")

    reader.current_path = ""
    assert reader._get_annotation_path() == ""


@patch("os.path.exists", return_value=True)
@patch("builtins.open", new_callable=MagicMock)
def test_load_annotations_existing(mock_open, mock_exists, reader):
    mock_open.return_value.__enter__.return_value.read.return_value = (
        '{"0": [{"type": "note"}]}'
    )
    with patch("json.load", return_value={"0": [{"type": "note"}]}):
        reader.load_annotations()
    assert "0" in reader.annotations
    assert reader.annotations["0"][0]["type"] == "note"


@patch("builtins.open", new_callable=MagicMock)
def test_save_annotations(mock_open, reader):
    reader.annotations = {"1": [{"type": "note", "text": "test"}]}
    with patch("json.dump") as mock_json_dump:
        reader.save_annotations()
        mock_json_dump.assert_called_once_with(
            reader.annotations, mock_open.return_value.__enter__.return_value
        )


def test_toggle_annotation_mode(reader):
    reader.toggle_annotation_mode(True)
    reader.anno_toolbar.setVisible.assert_called_with(True)
    reader.btn_annotate.setChecked.assert_called_with(True)
    assert reader.current_tool == "nav"

    reader.toggle_annotation_mode(False)
    assert reader.current_tool == "nav"
    assert reader.cursor == Qt.CursorShape.ArrowCursor


def test_set_tool(reader):
    reader.set_tool("eraser")
    assert reader.current_tool == "eraser"
    assert reader.cursor == Qt.CursorShape.ForbiddenCursor

    reader.set_tool("pen")
    assert reader.current_tool == "pen"
    assert reader.cursor == Qt.CursorShape.CrossCursor


@patch.object(DummyAnnotationReader, "save_annotations")
@patch.object(DummyAnnotationReader, "refresh_page_render")
def test_undo_redo_annotation(mock_refresh, mock_save, reader):
    reader.annotations = {"0": [{"type": "note", "text": "first"}]}
    reader.undo_stack.append(("add", 0, 0))

    reader.undo_annotation()
    assert len(reader.annotations["0"]) == 0
    assert len(reader.redo_stack) == 1
    mock_save.assert_called_once()
    mock_refresh.assert_called_once_with(0)

    mock_save.reset_mock()
    mock_refresh.reset_mock()

    reader.redo_annotation()
    assert len(reader.annotations["0"]) == 1
    assert reader.annotations["0"][0]["text"] == "first"
    assert len(reader.undo_stack) == 1
    mock_save.assert_called_once()
    mock_refresh.assert_called_once_with(0)


@patch.object(DummyAnnotationReader, "save_annotations")
@patch.object(DummyAnnotationReader, "refresh_page_render")
def test_add_anno_data(mock_refresh, mock_save, reader):
    reader._add_anno_data(2, {"type": "note", "text": "new note"})
    assert "2" in reader.annotations
    assert len(reader.annotations["2"]) == 1
    assert reader.undo_stack[-1] == ("add", 2, 0)
    mock_save.assert_called_once()
    mock_refresh.assert_called_once_with(2)
