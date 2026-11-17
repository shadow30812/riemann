import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from riemann.ui.reader.mixins.annotations import AnnotationsMixin, CommentViewDialog

if not QApplication.instance():
    _qapp = QApplication(sys.argv)



class DummyAnnotationReader(AnnotationsMixin):
    def pdf_path(self):
        return self.current_path

    def __init__(self):
        self.current_path = "/fake/path/doc.pdf"
        self.annotations = {}
        self._map_to_unrotated = lambda rx, ry: (rx, ry)
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


def test_get_annotation_path(reader, tmp_path, monkeypatch):
    from riemann.core import annotation_store

    monkeypatch.setattr(annotation_store, "_shared", annotation_store.AnnotationStore(str(tmp_path / "annotations")))
    reader._anno_json_cache = None
    path = reader._get_annotation_path()
    assert path.startswith(str(tmp_path / "annotations"))
    assert path.endswith(".json")

    reader.current_path = ""
    assert reader._get_annotation_path() == ""


@patch("os.path.exists", return_value=True)
@patch("builtins.open", new_callable=MagicMock)
def test_load_annotations_existing(mock_open, mock_exists, reader):
    with patch("json.load", return_value={"0": [{"type": "note"}]}), patch.object(
        reader, "_check_pdf_annotations_async"
    ) as check:
        reader.load_annotations()
    assert "0" in reader.annotations
    assert reader.annotations["0"][0]["type"] == "note"
    assert reader.annotations["0"][0]["id"]
    check.assert_called_once_with("/fake/path/doc.pdf")


@patch("os.path.exists", return_value=True)
@patch("builtins.open", new_callable=MagicMock)
def test_load_annotations_new_format(mock_open, mock_exists, reader):
    data = {"pages": {"2": [{"type": "note", "id": "abc"}]}, "pdf": [10, 20]}
    with patch("json.load", return_value=data), patch.object(
        reader, "_check_pdf_annotations_async"
    ):
        reader.load_annotations()
    assert reader.annotations == {"2": [{"type": "note", "id": "abc"}]}
    assert reader._anno_file_sig == [10, 20]


def test_save_annotations(reader):
    reader.annotations = {"1": [{"type": "note", "text": "test"}]}
    reader._anno_file_sig = None
    with patch(
        "riemann.ui.reader.mixins.annotations.write_json_atomic"
    ) as mock_write, patch(
        "riemann.ui.reader.mixins.annotations.store_in_pdf_enabled", return_value=False
    ):
        reader.save_annotations()
        mock_write.assert_called_once_with(
            reader._get_annotation_path(), {"pages": reader.annotations, "pdf": None, "written": None}
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
    for box in (mock_page.cropbox, mock_page.mediabox):
        box.left, box.bottom, box.right, box.top = 0, 0, 600, 800
    mock_page.rotation = 0

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
                "rel_rect": (0.1, 0.2, 0.3, 0.4),
            }
        ]
    }

    hit = reader._get_comment_at_pos(0, QPoint(200, 300), page_w=1000, page_h=1000)
    assert hit is not None
    assert hit["author"] == "Alice"

    miss = reader._get_comment_at_pos(0, QPoint(800, 800), page_w=1000, page_h=1000)
    assert miss is None

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

    with patch("PySide6.QtWidgets.QApplication.clipboard") as mock_clipboard:
        mock_cb_inst = MagicMock()
        mock_clipboard.return_value = mock_cb_inst
        dialog.findChildren(QPushButton)[0].click()
        mock_cb_inst.setText.assert_called_with("Please revise this section.")
