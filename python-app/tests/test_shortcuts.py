"""
Shortcuts that two visible widgets share, as reader and browser tabs do in
split view, run in the widget holding the focus (riemann.ui.shortcuts); and
the window registers each of its own key sequences once.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QWidget

from riemann.app import RiemannWindow
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.shortcuts import share_keys


def side_by_side(*panes):
    holder = QWidget()
    row = QHBoxLayout(holder)
    for pane in panes:
        row.addWidget(pane)
    holder.show()
    holder.activateWindow()
    QApplication.processEvents()
    return holder


def press(widget, sequence):
    widget.setFocus()
    QApplication.processEvents()
    combo = QKeySequence(sequence)[0]
    QTest.keyClick(widget, combo.key(), combo.keyboardModifiers())
    QApplication.processEvents()


def test_a_shared_shortcut_runs_in_the_pane_holding_the_focus(qtbot):
    ran = []
    panes = []
    for name in ("left", "right"):
        pane = QLineEdit()
        QShortcut(QKeySequence("Ctrl+F"), pane).activated.connect(lambda n=name: ran.append(n))
        share_keys(pane)
        panes.append(pane)
    holder = side_by_side(*panes)
    qtbot.addWidget(holder)
    press(panes[1], "Ctrl+F")
    press(panes[0], "Ctrl+F")
    assert ran == ["right", "left"]


def test_a_panels_own_shortcut_wins_over_the_windows_while_it_has_the_focus(qtbot):
    ran = []
    holder = QWidget()
    QShortcut(QKeySequence("Ctrl+L"), holder).activated.connect(lambda: ran.append("window"))
    share_keys(holder)
    panel = QLineEdit()
    QShortcut(QKeySequence("Ctrl+L"), panel).activated.connect(lambda: ran.append("panel"))
    share_keys(panel)
    other = QLineEdit()
    row = QHBoxLayout(holder)
    row.addWidget(panel)
    row.addWidget(other)
    holder.show()
    holder.activateWindow()
    qtbot.addWidget(holder)
    press(panel, "Ctrl+L")
    press(other, "Ctrl+L")
    assert ran == ["panel", "window"]


@contextmanager
def plain_settings():
    settings = MagicMock()
    settings.return_value.value.side_effect = lambda key, default=None, type=None: False if key == "reader/notes_visible" else True
    with patch("riemann.ui.reader.tab.app_settings", settings), patch("riemann.ui.reader.mixins.appearance.app_settings", settings):
        yield


def test_find_works_in_either_reader_of_a_split_view(qtbot):
    with plain_settings():
        left, right = ReaderTab(), ReaderTab()
    holder = side_by_side(left, right)
    qtbot.addWidget(holder)
    press(right, "Ctrl+F")
    assert right.search_bar.isVisible() and not left.search_bar.isVisible()
    press(left, "Ctrl+F")
    assert left.search_bar.isVisible()
    for tab in (left, right):
        tab.cleanup()
    holder.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_window_registers_each_of_its_shortcuts_once(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    keys = [
        s.key().toString()
        for s in window.findChildren(QShortcut, "", Qt.FindChildOption.FindDirectChildrenOnly)
    ]
    assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)
