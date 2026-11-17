"""
The Help page: it opens from F1 and the Help menu, and its shortcut tables
list every QShortcut the window, a reader, an image or table tab, a browser
tab and the AI panel register, so a new shortcut can't ship undescribed.
"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QMenu

from riemann.app import RiemannWindow
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.system import PAGES
from riemann.ui.system.help_tab import SHORTCUTS, TOPICS, HelpTab, shown_keys
from riemann.ui.viewers import ImageTab, TableTab
from test_browser import _WebEngineTracker


@pytest.fixture
def webengine(qapp):
    tracker = _WebEngineTracker()
    yield tracker
    tracker.dispose()


def own_keys(widget):
    return {
        s.key().toString()
        for s in widget.findChildren(QShortcut, "", Qt.FindChildOption.FindDirectChildrenOnly)
        if not s.key().isEmpty()
    }


def documented_keys():
    return {QKeySequence(keys).toString() for _, rows in SHORTCUTS for alternatives, _ in rows for keys in alternatives}


def test_every_shortcut_the_app_registers_is_on_the_help_page(qtbot, webengine, tmp_path):
    from riemann.ui.sidecar import AiSidecarPanel

    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    settings = MagicMock()
    settings.return_value.value.side_effect = lambda key, default=None, type=None: default
    with patch("riemann.ui.reader.tab.app_settings", settings), patch("riemann.ui.reader.mixins.appearance.app_settings", settings):
        reader = ReaderTab()
    qtbot.addWidget(reader)
    browser = webengine.tab()
    panel = AiSidecarPanel(window)
    qtbot.addWidget(panel)

    image = ImageTab(str(tmp_path / "picture.png"))
    qtbot.addWidget(image)
    (tmp_path / "table.csv").write_text("a,b\n1,2\n")
    table = TableTab(str(tmp_path / "table.csv"))
    qtbot.addWidget(table)

    registered = own_keys(window) | own_keys(reader) | own_keys(image) | own_keys(table) | own_keys(browser) | own_keys(panel)
    missing = registered - documented_keys()
    assert not missing, f"describe these in riemann/ui/system/help_tab.py SHORTCUTS: {sorted(missing)}"
    reader.cleanup()
    image.cleanup()
    table.cleanup()


def test_f1_and_the_help_menu_open_the_help_page_once(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    assert PAGES["help"] is HelpTab
    window.show_help()
    window.show_help("shortcuts")
    tabs, index = window.find_system_tab("help")
    help_tab = tabs.widget(index)
    assert sum(getattr(tabs.widget(i), "page", None) == "help" for i in range(tabs.count())) == 1
    assert help_tab.current_section() == "shortcuts"
    help_menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "Help")
    assert any("F1" in a.text() for a in help_menu.actions())
    assert "F1" in own_keys(window)


def test_every_topic_and_shortcut_is_shown(qtbot):
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show_help()
    tabs, index = window.find_system_tab("help")
    shown = {label.text() for label in tabs.widget(index).findChildren(QLabel)}
    for _, _, _, cards in TOPICS:
        for title, paragraphs in cards:
            assert title in shown
            assert all(text in shown for text in paragraphs)
    for title, rows in SHORTCUTS:
        assert title in shown
        for alternatives, action in rows:
            assert action in shown
            assert all(shown_keys(keys) in shown for keys in alternatives)
    assert "Ctrl+K then K" in shown
