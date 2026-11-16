"""Tests for presentation mode (riemann/ui/reader/presentation.py)."""

import os

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


def key(widget, k, text=""):
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, text))


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_presentation_keys_laser_and_blanking(qapp):
    import riemann_core

    from riemann.ui.reader.presentation import PresentationWindow

    doc = riemann_core.PdfEngine().load_document(os.path.abspath(CORPUS), None)
    w = PresentationWindow(doc, 2)
    w.resize(1280, 800)
    w.show()
    w.go(2)
    image = w.grab()
    assert not image.isNull()
    centre = image.toImage().pixelColor(640, 400)
    assert centre.lightness() > 200

    key(w, Qt.Key.Key_Right)
    assert w.page == 3
    key(w, Qt.Key.Key_Left)
    key(w, Qt.Key.Key_Left)
    assert w.page == 1
    key(w, Qt.Key.Key_1, "1")
    key(w, Qt.Key.Key_2, "2")
    key(w, Qt.Key.Key_Return)
    assert w.page == 11
    key(w, Qt.Key.Key_End)
    key(w, Qt.Key.Key_Right)
    assert w.page == w.count - 1

    key(w, Qt.Key.Key_L)
    w.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(100, 100), QPointF(100, 100), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    dot = w.grab().toImage().pixelColor(100, 100)
    assert dot.red() > 200 and dot.green() < 120

    key(w, Qt.Key.Key_B)
    assert w.grab().toImage().pixelColor(640, 400).lightness() < 10
    key(w, Qt.Key.Key_B)
    key(w, Qt.Key.Key_Escape)
    assert not w.isVisible()


def mouse(widget, kind, x, y, button=Qt.MouseButton.LeftButton):
    held = button if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    event = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, held, Qt.KeyboardModifier.NoModifier)
    {
        QEvent.Type.MouseButtonPress: widget.mousePressEvent,
        QEvent.Type.MouseMove: widget.mouseMoveEvent,
        QEvent.Type.MouseButtonRelease: widget.mouseReleaseEvent,
    }[kind](event)


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_presentation_zooms_around_the_pointer_and_pans(qapp):
    import riemann_core

    from riemann.ui.reader.presentation import PresentationWindow

    doc = riemann_core.PdfEngine().load_document(os.path.abspath(CORPUS), None)
    w = PresentationWindow(doc, 0)
    w.resize(1280, 800)
    w.show()
    w.go(0)
    fitted = w.page_rect()

    anchor = QPointF(fitted.x() + fitted.width() * 0.25, fitted.y() + fitted.height() * 0.3)
    w.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, anchor, anchor, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier, "="))
    assert w.zoom == pytest.approx(1.25)
    zoomed = w.page_rect()
    assert zoomed.width() == pytest.approx(fitted.width() * 1.25)
    assert zoomed.center().x() == pytest.approx(w.width() / 2)
    assert (anchor.y() - zoomed.y()) / zoomed.height() == pytest.approx(0.3, abs=0.01)
    pix = w._pixmap(0)
    assert pix.width() / pix.devicePixelRatio() == pytest.approx(zoomed.width(), abs=2)

    for _ in range(10):
        w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier, "="))
    assert w.zoom == w.MAX_ZOOM

    before = w.page_rect().topLeft()
    mouse(w, QEvent.Type.MouseButtonPress, 600, 400)
    mouse(w, QEvent.Type.MouseMove, 650, 430)
    mouse(w, QEvent.Type.MouseButtonRelease, 650, 430)
    assert w.page == 0
    assert w.page_rect().topLeft() != before

    w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier, "-"))
    assert w.zoom == pytest.approx(w.MAX_ZOOM / 1.25)
    w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier, "0"))
    assert w.zoom == 1.0
    assert w.page_rect() == fitted

    w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier, "="))
    mouse(w, QEvent.Type.MouseButtonPress, 600, 400)
    mouse(w, QEvent.Type.MouseButtonRelease, 600, 400)
    assert w.page == 1 and w.zoom == 1.0
    w.close()
