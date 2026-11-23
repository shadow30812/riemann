"""Tests for the keycaps tooltip skin in riemann/ui/tooltip.py."""

import time

import pytest
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QLabel, QPushButton

from riemann.ui import tooltip
from riemann.ui.palette import load_ui_font
from riemann.ui.tooltip import KeyCap, TooltipSkin, parse_tip


def pump(app, seconds, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.01)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("AI Semantic Search (Ctrl+I)", ("AI Semantic Search", ["Ctrl", "I"], "")),
        ("Zoom In", ("Zoom In", [], "")),
        ("Zoom in (Ctrl++)", ("Zoom in", ["Ctrl", "+"], "")),
        ("Split view (Ctrl+\\)\nTip: drag a tab", ("Split view", ["Ctrl", "\\"], "Tip: drag a tab")),
        ("Collapse (Ctrl+Shift+B)", ("Collapse", ["Ctrl", "Shift", "B"], "")),
        ("Rotate PDF (90°)", ("Rotate PDF (90°)", [], "")),
        ("Help (F1)", ("Help", ["F1"], "")),
        ("  \n ", ("", [], "")),
    ],
)
def test_parse_tip(text, expected):
    assert parse_tip(text) == expected


def test_bundled_font_loads(qapp):
    assert load_ui_font()


@pytest.fixture
def skin(qapp):
    s = TooltipSkin.install(qapp)
    yield s
    s.hide()


def visible_texts(window):
    return [label.text() for label in window.findChildren(QLabel) if label.isVisibleTo(window)]


def hover(app, widget, local=QPoint(5, 5)):
    app.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, local, widget.mapToGlobal(local)))


def test_tooltip_is_drawn_by_the_skin_not_qt(qapp, skin):
    button = QPushButton("x")
    button.setStyleSheet("color: #9b59b6; font-weight: bold; font-size: 16px;")
    button.setToolTip("AI Semantic Search (Ctrl+I)")
    skin.attach(button)
    button.show()
    hover(qapp, button)
    assert skin.window.isVisible()
    assert not [w for w in qapp.topLevelWidgets() if w.metaObject().className() == "QTipLabel" and w.isVisible()]
    assert "AI Semantic Search" in visible_texts(skin.window)
    assert [k.text() for k in skin.window.findChildren(KeyCap) if k.isVisibleTo(skin.window)] == ["Ctrl", "I"]
    title = next(
        l for l in skin.window.findChildren(QLabel) if l.text() == "AI Semantic Search" and l.isVisibleTo(skin.window)
    )
    assert "#9b59b6" not in title.styleSheet()
    qapp.sendEvent(button, QEvent(QEvent.Type.Leave))
    assert not skin.window.isVisible()
    button.deleteLater()


def test_tooltip_follows_new_text_and_theme(qapp, skin):
    button = QPushButton("y")
    skin.attach(button)
    button.show()
    button.setToolTip("First")
    hover(qapp, button)
    assert skin.window.text == "First"
    button.setToolTip("Second line one\nmore detail")
    hover(qapp, button)
    assert "more detail" in visible_texts(skin.window)

    tooltip.set_dark(False)
    title = next(
        l for l in skin.window.findChildren(QLabel) if l.text() == "Second line one" and l.isVisibleTo(skin.window)
    )
    assert tooltip.COLOURS[False]["text"] in title.styleSheet()
    tooltip.set_dark(True)
    title = next(
        l for l in skin.window.findChildren(QLabel) if l.text() == "Second line one" and l.isVisibleTo(skin.window)
    )
    assert tooltip.COLOURS[True]["text"] in title.styleSheet()
    button.deleteLater()


def test_long_tooltips_wrap(qapp, skin):
    button = QPushButton("z")
    skin.attach(button)
    button.show()
    button.setToolTip("Collapse the tab row (or column, with vertical tabs) to give the page more room")
    hover(qapp, button)
    assert skin.window.isVisible()
    assert skin.window.width() <= tooltip.MAX_TEXT_WIDTH + 2 * tooltip.SHADOW + 40
    button.deleteLater()


def test_tabs_list_rows_and_late_children_are_covered(qapp, skin):
    from PySide6.QtWidgets import QListWidget, QListWidgetItem, QTabBar, QVBoxLayout, QWidget

    root = QWidget()
    layout = QVBoxLayout(root)
    bar = QTabBar()
    bar.addTab("a")
    bar.setTabToolTip(0, "First tab (Ctrl+1)")
    layout.addWidget(bar)
    rows = QListWidget()
    item = QListWidgetItem("paper.pdf")
    item.setToolTip("/home/me/paper.pdf")
    rows.addItem(item)
    layout.addWidget(rows)
    skin.attach(root)
    root.resize(300, 300)
    root.show()
    qapp.processEvents()

    hover(qapp, bar, bar.tabRect(0).center())
    assert skin.window.text == "First tab (Ctrl+1)"
    hover(qapp, rows.viewport(), rows.visualItemRect(item).center())
    assert skin.window.text == "/home/me/paper.pdf"

    late = QPushButton("late", root)
    late.setToolTip("Added later")
    late.show()
    qapp.processEvents()
    hover(qapp, late)
    assert skin.window.text == "Added later"
    root.deleteLater()


def test_web_views_are_left_alone(qapp, skin):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView()
    assert TooltipSkin._skipped(view)
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
