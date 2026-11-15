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
