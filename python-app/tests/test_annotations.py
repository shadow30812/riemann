import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from riemann.ui.reader.mixins.annotations import AnnotationsMixin, CommentViewDialog

if not QApplication.instance():
    _qapp = QApplication(sys.argv)



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


@patch("os.path.isfile", return_value=True)
@patch("pypdf.PdfReader")
def test_load_external_comments(mock_pdf_reader_cls, mock_isfile, reader):
    mock_annot = MagicMock()
    mock_annot.get_object.return_value = {
        "/Contents": "This is an Acrobat comment",
        "/T": "AuthorName",
        "/M": "D:20260101120000",
        "/Subtype": "/Text",
        "/Rect": [100, 200, 150, 250],
    }
    mock_page = MagicMock()
    mock_page.__contains__.side_effect = lambda key: key == "/Annots"
    mock_page.__getitem__.side_effect = lambda key: [mock_annot] if key == "/Annots" else None
    mock_page.cropbox.left = 0
    mock_page.cropbox.bottom = 0
    mock_page.cropbox.width = 600
    mock_page.cropbox.height = 800

    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf_reader_cls.return_value = mock_pdf

    reader._load_external_comments("/fake/commented.pdf")

    assert 0 in reader.external_comments
    assert len(reader.external_comments[0]) == 1
    c = reader.external_comments[0][0]
    assert c["author"] == "AuthorName"
    assert c["contents"] == "This is an Acrobat comment"
    assert c["date"] == "2026-01-01 12:00:00"
    assert c["page"] == 0
    assert "rel_rect" in c
    assert c["rel_rect"][0] == 100 / 600.0


@patch("os.path.isfile", return_value=True)
@patch("pypdf.PdfReader")
def test_load_external_comments_handles_no_annots(mock_pdf_reader_cls, mock_isfile, reader):
    mock_page = MagicMock()
    mock_page.__contains__.return_value = False

    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf_reader_cls.return_value = mock_pdf

    reader._load_external_comments("/fake/clean.pdf")
    assert reader.external_comments == {}


def test_get_comment_at_pos(reader):
    from PySide6.QtCore import QPoint
    reader.external_comments = {
        0: [
            {
                "page": 0,
                "author": "Alice",
                "contents": "A note",
                "rel_rect": (0.1, 0.2, 0.3, 0.4),  # x1=0.1, y1=0.2, x2=0.3, y2=0.4
            }
        ]
    }

    # Hit inside bounding box (page size 1000x1000, point at 200, 300 -> rx=0.2, ry=0.3)
    hit = reader._get_comment_at_pos(0, QPoint(200, 300), page_w=1000, page_h=1000)
    assert hit is not None
    assert hit["author"] == "Alice"

    # Miss far away (800, 800 -> rx=0.8, ry=0.8)
    miss = reader._get_comment_at_pos(0, QPoint(800, 800), page_w=1000, page_h=1000)
    assert miss is None

    # Miss wrong page
    miss_page = reader._get_comment_at_pos(1, QPoint(200, 300), page_w=1000, page_h=1000)
    assert miss_page is None


def test_comment_view_dialog():
    from PySide6.QtWidgets import QPushButton, QTextEdit
    comment_data = {
        "author": "Reviewer",
        "date": "2026-03-01",
        "page": 2,
        "contents": "Please revise this section.",
    }
    dialog = CommentViewDialog(comment_data)
    txt_edit = dialog.findChild(QTextEdit)
    assert txt_edit is not None
    assert txt_edit.toPlainText() == "Please revise this section."

    # Test copy to clipboard
    with patch("PySide6.QtWidgets.QApplication.clipboard") as mock_clipboard:
        mock_cb_inst = MagicMock()
        mock_clipboard.return_value = mock_cb_inst
        dialog.findChildren(QPushButton)[0].click()
        mock_cb_inst.setText.assert_called_with("Please revise this section.")


