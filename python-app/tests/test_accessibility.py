"""
The accessibility aids: names for screen readers (ui/accessibility.py), the
reading ruler (ui/reader/mixins/ruler.py), the high-contrast theme and the
Accessibility section of Settings that switches them.

Screen readers name a control by its accessible name, and Qt gives an
icon-only button none, so one test opens a window with a reader and a browser
tab and checks that every control a screen reader would announce has a name.
"""

import os
import time

import pytest
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPushButton,
    QScrollBar,
    QTabBar,
    QWidget,
)

from riemann.app import RiemannWindow
from riemann.ui import accessibility
from riemann.ui.accessibility import NAMED, describe_tip, name_from_tooltip
from riemann.ui.tooltip import TooltipSkin

CORPUS_PDF = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


def wait_for(condition, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _announced(widget) -> bool:
    if widget.objectName().startswith("qt_") or type(widget) is QAbstractButton:
        return False
    if isinstance(widget, QScrollBar) or isinstance(widget.parentWidget(), (QTabBar, QComboBox, QAbstractSpinBox)):
        return False
    return not (isinstance(widget, QAbstractButton) and widget.text().replace("&", "").strip())


def _unnamed(root):
    return sorted(
        f"{type(w).__name__} {w.objectName() or ''} in {type(w.parentWidget()).__name__}".strip()
        for w in root.findChildren(QWidget)
        if isinstance(w, NAMED) and _announced(w) and not w.accessibleName()
    )


def test_tooltips_become_names_and_shortcuts_descriptions(qapp):
    assert describe_tip("AI Semantic Search (Ctrl+I)") == ("AI Semantic Search", "Shortcut: Ctrl+I")
    assert describe_tip("<b>Rotate</b><br>Turns the page") == ("Rotate", "Turns the page")
    button = QPushButton()
    button.setToolTip("Download Video via yt-dlp")
    name_from_tooltip(button)
    assert button.accessibleName() == "Download Video via yt-dlp"
    button.setToolTip("Cancel Download")
    name_from_tooltip(button)
    assert button.accessibleName() == "Cancel Download"
    button.setAccessibleName("Named in code")
    button.setToolTip("Something else")
    name_from_tooltip(button)
    assert button.accessibleName() == "Named in code"
    field = QLineEdit()
    field.setPlaceholderText("Find in page...")
    name_from_tooltip(field)
    assert field.accessibleName() == "Find in page..."


def test_a_changed_tooltip_renames_the_button(qapp):
    TooltipSkin.install(qapp)
    button = QPushButton()
    TooltipSkin.instance.attach(button)
    button.setToolTip("Download Video via yt-dlp")
    QApplication.processEvents()
    assert button.accessibleName() == "Download Video via yt-dlp"
    button.setToolTip("Cancel Download")
    QApplication.processEvents()
    assert button.accessibleName() == "Cancel Download"


def test_every_control_in_a_reader_and_a_browser_tab_has_a_name(qapp, qtbot):
    TooltipSkin.install(qapp)
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    window.show()
    reader = window._open_document(os.path.abspath(CORPUS_PDF), preview=False)
    assert wait_for(lambda: reader.current_doc is not None)
    window.new_browser_tab(url="about:blank")
    wait_for(lambda: False, timeout=0.5)
    assert _unnamed(window) == []
    window.close()


def test_names_can_be_turned_off_and_back_on(qapp):
    button = QPushButton()
    button.setToolTip("Zoom In")
    name_from_tooltip(button)
    assert button.accessibleName() == "Zoom In"
    named = QPushButton()
    named.setAccessibleName("Named in code")
    try:
        accessibility.set_enabled(False)
        assert button.accessibleName() == ""
        assert named.accessibleName() == "Named in code"
    finally:
        accessibility.set_enabled(True)
    assert button.accessibleName() == "Zoom In"


def test_text_lines_follow_reading_order_and_columns():
    from PySide6.QtCore import QPoint, QRect

    from riemann.ui.reader.mixins.ruler import line_at, text_lines

    def word(text, x, y):
        return [(ch, QRect(x + i * 8, y, 8, 12), 0) for i, ch in enumerate(text)]

    chars = [*word("Left one", 10, 10), (" ", QRect(), 0), *word("Left two", 10, 30)]
    chars += word("Right one", 300, 10) + word("Right two", 300, 30)
    lines = text_lines(chars)
    assert len(lines) == 4
    assert lines[0].left() == 10 and lines[2].left() == 300
    assert line_at(lines, QPoint(320, 35)) == 3
    assert line_at(lines, QPoint(20, 14)) == 0
    assert line_at(lines, QPoint(20, 200)) == 1


def test_reading_ruler_follows_the_pointer_and_the_keys(qapp, qtbot, monkeypatch):
    from PySide6.QtCore import QPoint, QSettings

    from riemann.ui.reader.tab import ReaderTab

    store = QSettings("RiemannTest", "Ruler")
    store.clear()
    monkeypatch.setattr("riemann.ui.reader.mixins.ruler.app_settings", lambda: store)
    tab = ReaderTab()
    qtbot.addWidget(tab)
    tab.resize(1000, 800)
    tab.show()
    tab.load_document(os.path.abspath(CORPUS_PDF))
    assert wait_for(lambda: tab.current_doc is not None and 0 in tab.page_widgets)
    wait_for(lambda: False, timeout=0.5)
    page = tab.page_widgets[0]
    assert not tab.ruler_enabled()
    tab.ruler_follow_pointer(0, QPoint(page.width() // 2, page.height() // 3))
    assert getattr(page, "ruler_band", None) is None

    tab.toggle_reading_ruler()
    assert store.value("accessibility/reading_ruler", type=bool)
    first = page.ruler_band
    assert first is not None and first.height() < page.height() / 10
    tab.ruler_step(1)
    second = page.ruler_band
    assert second.top() > first.top()
    tab.ruler_step(-1)
    assert page.ruler_band == first

    lines = tab._ruler_lines(0)
    target = lines[len(lines) // 2]
    tab.ruler_follow_pointer(0, target.center())
    assert page.ruler_band.contains(target)

    tab.set_reading_ruler(False)
    assert page.ruler_band is None
    tab.cleanup()
    store.clear()


@pytest.fixture
def window(qapp, qtbot):
    from riemann.ui import palette

    TooltipSkin.install(qapp)
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)
    yield win
    palette.set_high_contrast(False)
    accessibility.set_enabled(True)
    win.close()


def test_settings_has_an_accessibility_section_that_reaches_open_readers(window):
    from PySide6.QtWidgets import QCheckBox

    from riemann.core.settings import READING_RULER_KEY, SCREEN_READER_NAMES_KEY

    reader = window._open_document(os.path.abspath(CORPUS_PDF), preview=False)
    assert wait_for(lambda: reader.current_doc is not None)
    window.open_system_tab("settings") if hasattr(window, "open_system_tab") else None
    from riemann.ui.system.settings_tab import SettingsTab

    settings = SettingsTab(window)
    assert "Accessibility" in [settings.nav.item(i).text() for i in range(settings.nav.count())]
    for box in (settings.cb_screen_reader, settings.cb_read_words, settings.cb_ruler):
        assert isinstance(box, QCheckBox)
    settings.cb_ruler.setChecked(True)
    assert window.settings.value(READING_RULER_KEY, type=bool)
    assert reader.ruler_enabled()
    settings.cb_ruler.setChecked(False)
    assert not reader.ruler_enabled()
    settings.cb_screen_reader.setChecked(False)
    assert not window.settings.value(SCREEN_READER_NAMES_KEY, True, type=bool)
    assert reader.btn_next.accessibleName() == ""
    settings.cb_screen_reader.setChecked(True)
    assert reader.btn_next.accessibleName() == "Next Page"
    settings.deleteLater()


def test_high_contrast_turns_dark_mode_on_and_goes_pure_black(window):
    from riemann.ui.palette import CONTRAST, tokens

    window.dark_mode = False
    window.restyle_ui()
    window.set_high_contrast(True)
    assert window.dark_mode
    assert "high-contrast theme" in window.styleSheet()
    assert tokens(True) is CONTRAST
    window.set_high_contrast(False)
    assert "high-contrast theme" not in window.styleSheet()
