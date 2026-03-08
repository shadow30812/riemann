from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, QRect
from riemann.ui.reader.widgets import PageWidget


def test_pagewidget_init():
    widget = PageWidget()
    assert widget.temp_points == []
    assert widget.markup_rects == []
    assert widget.signature_overlays == []


def test_pagewidget_set_temp_stroke():
    widget = PageWidget()
    points = [QPoint(0, 0), QPoint(10, 10)]
    widget.update = MagicMock()

    widget.set_temp_stroke(points, "#ff0000", 2, True)
    assert widget.temp_points == points
    assert widget.temp_pen.color().alpha() == 80
    assert widget.temp_pen.width() == 6
    assert widget.temp_pen.color().name() == "#ff0000"
    widget.update.assert_called_once()

    widget.update.reset_mock()
    widget.set_temp_stroke(points, "#00ff00", 3, False)
    assert widget.temp_pen.color().alpha() == 255
    assert widget.temp_pen.width() == 3
    widget.update.assert_called_once()


@patch("riemann.ui.reader.widgets.QPainter")
def test_pagewidget_paintEvent(mock_qpainter_class):
    widget = PageWidget()
    mock_painter = MagicMock()
    mock_qpainter_class.return_value = mock_painter

    widget.temp_points = [QPoint(1, 1), QPoint(2, 2)]
    widget.markup_rects = [QRect(0, 0, 10, 10)]
    widget.signature_overlays = [
        {"rect": QRect(5, 5, 20, 20), "status": "VALID", "subject": "Test Sub"}
    ]

    mock_event = MagicMock()

    with patch("PySide6.QtWidgets.QLabel.paintEvent"):
        widget.paintEvent(mock_event)

    mock_painter.setPen.assert_called()
    mock_painter.setBrush.assert_called()
    mock_painter.drawPolyline.assert_called_once()
    mock_painter.drawRect.assert_called()
    mock_painter.drawText.assert_called()
    mock_painter.end.assert_called_once()


def test_pagewidget_set_signature_overlays():
    widget = PageWidget()
    widget.update = MagicMock()

    overlays = [{"rect": QRect(), "status": "VALID", "subject": "John"}]
    widget.set_signature_overlays(overlays)

    assert widget.signature_overlays == overlays
    widget.update.assert_called_once()
