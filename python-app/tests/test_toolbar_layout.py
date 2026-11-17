"""
Tests for the reader toolbar layouts (riemann/ui/reader/toolbar_layout.py)
and their Settings control, that the reader's bars take every colour from the
palette, and that ReaderTab has everything mixins/host.py declares. Settings go
to a throwaway QSettings store.
"""

import os
import time

import pytest
import shiboken6
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMenu

from riemann.ui.reader import toolbar_layout as tl

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


@pytest.fixture
def store(monkeypatch):
    s = QSettings("RiemannTest", "ToolbarLayout")
    s.clear()
    monkeypatch.setattr(tl, "app_settings", lambda: s)
    yield s
    s.clear()


def pump(qapp, seconds, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def reader(qapp, store):
    from riemann.ui.reader.tab import ReaderTab

    tab = ReaderTab()
    tab.resize(1280, 720)
    tab.show()
    tab.load_document(os.path.abspath(CORPUS))
    pump(qapp, 15, lambda: tab.current_doc is not None)
    pump(qapp, 0.3)
    yield tab
    tab.cleanup()
    tab.deleteLater()
    pump(qapp, 0.1)


def container_of(layout, widget):
    for name in ("bar", "ribbon", "rail", "pill", "parking"):
        holder = getattr(layout, name)
        if holder is not None and (widget is holder or holder.isAncestorOf(widget)):
            return name
    return None


EXPECTED = {tl.BAR: "bar", tl.RAIL: "rail", tl.RIBBON: "ribbon", tl.PILL: "pill", tl.MENU: "parking", tl.HIDDEN: "parking"}


def test_custom_layout_round_trips_and_ignores_invalid_places(store):
    tl.save_custom_layout({"notes": tl.HIDDEN, "save": tl.BAR, "page": tl.RAIL, "nonsense": tl.BAR}, False)
    spec = tl.resolve("custom")
    assert spec["places"]["notes"] == tl.HIDDEN
    assert spec["places"]["save"] == tl.BAR
    assert spec["places"]["page"] == tl.BAR
    assert spec["fullscreen_pill"] is False
    assert spec["menus"] == "rail"


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
@pytest.mark.parametrize("name", ["default", "A", "B", "C", "D", "custom"])
def test_every_button_survives_and_sits_where_its_layout_says(qapp, reader, name):
    if name == "custom":
        tl.save_custom_layout({"save": tl.BAR, "notes": tl.PILL, "ocr": tl.HIDDEN}, True)
    tl.set_layout(name)
    pump(qapp, 0.2)
    layout = reader.toolbar_layout
    places = layout.effective_places()
    ribbon_group = None
    if layout.ribbon is not None:
        ribbon_group = layout.ribbon.findChild(tl.QComboBox, "ribbonPicker").currentText()
    for command in tl.COMMANDS:
        for widget in layout.widgets(command.key):
            assert shiboken6.isValid(widget), command.key
            place = places[command.key]
            want = EXPECTED[place]
            if place == tl.RIBBON and command.group != ribbon_group:
                want = "parking"
            assert container_of(layout, widget) == want, (name, command.key)


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_bar_folds_into_more_when_narrow_and_unfolds_when_wide(qapp, reader):
    tl.set_layout("A")
    pump(qapp, 0.2)
    bar = reader.toolbar_layout.bar
    assert bar.folded == []
    assert not bar.more.isVisibleTo(reader)
    reader.resize(620, 720)
    pump(qapp, 0.3)
    bar = reader.toolbar_layout.bar
    folded = [k for _, keys, _ in bar.folded for k in keys]
    assert "present" in folded and "save" in folded
    assert bar.more.isVisibleTo(reader)
    assert reader.txt_page.isVisibleTo(reader) and reader.btn_toggle_sidebar.isVisibleTo(reader)
    bar._fill_more()
    titles = [a.text() for a in bar.more_menu.actions()]
    assert any(t.startswith("Present") for t in titles)
    assert "File" in [a.text() for a in bar.more_menu.actions() if a.menu()]
    reader.resize(1280, 720)
    pump(qapp, 0.3)
    assert reader.toolbar_layout.bar.folded == []


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_rail_follows_the_toolbar_and_can_be_hidden(qapp, reader, store):
    tl.set_layout("C")
    pump(qapp, 0.2)
    layout = reader.toolbar_layout
    assert layout.rail.isVisibleTo(reader)
    reader.toolbar.hide()
    assert not layout.rail.isVisibleTo(reader)
    reader.toolbar.show()
    assert layout.rail.isVisibleTo(reader)
    layout.hide_rail()
    pump(qapp, 0.2)
    assert reader.toolbar_layout.rail is None
    assert container_of(reader.toolbar_layout, reader.btn_notes) == "parking"
    assert ("Show side rail", reader.toolbar_layout.show_rail) in [
        (t, s) for t, s in reader.toolbar_layout.bar.extra_actions
    ]
    reader.toolbar_layout.show_rail()
    pump(qapp, 0.2)
    assert reader.toolbar_layout.rail is not None


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
@pytest.mark.parametrize("fit,mode", [("Fit Width", "FIT_WIDTH"), ("Fit Height", "FIT_HEIGHT"), ("Auto Fit", "AUTO_FIT")])
def test_fit_modes_refit_when_full_screen_hides_the_bars(qapp, reader, store, fit, mode):
    """Pure full screen hides the toolbar and the side rail: the pages get more room though the tab keeps its size."""
    from riemann.core.constants import ZoomMode

    reader.apply_zoom_string(fit)
    pump(qapp, 0.3)
    before = reader._last_applied_scale
    reader.update_fullscreen_icon(2)
    reader.toolbar.hide()
    assert not reader.toolbar_layout.rail.isVisible()
    pump(qapp, 0.5)
    assert reader.zoom_mode == ZoomMode[mode]
    assert reader._last_applied_scale == pytest.approx(reader.calculate_scale())
    assert reader._last_applied_scale > before
    reader.toolbar.show()
    reader.update_fullscreen_icon(0)
    pump(qapp, 0.5)
    assert reader._last_applied_scale == pytest.approx(before)


def test_full_screen_pill_only_in_default_and_custom(qapp, reader, store):
    tl.set_layout("default")
    pump(qapp, 0.2)
    assert reader.toolbar_layout.pill is None
    reader.toolbar.hide()
    reader.update_fullscreen_icon(2)
    pump(qapp, 0.2)
    layout = reader.toolbar_layout
    assert layout.pill is not None and layout.pill.isVisibleTo(reader)
    assert container_of(layout, reader.txt_page) == "pill"
    reader.update_fullscreen_icon(0)
    reader.toolbar.show()
    pump(qapp, 0.2)
    assert reader.toolbar_layout.pill is None
    assert container_of(reader.toolbar_layout, reader.txt_page) == "bar"

    tl.set_layout("C")
    reader.toolbar.hide()
    reader.update_fullscreen_icon(2)
    pump(qapp, 0.2)
    assert reader.toolbar_layout.pill is None
    reader.update_fullscreen_icon(0)
    reader.toolbar.show()

    tl.set_layout("D")
    pump(qapp, 0.2)
    assert reader.toolbar_layout.pill.isVisibleTo(reader)
    reader.toolbar.hide()
    assert not reader.toolbar_layout.pill.isVisibleTo(reader)


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_menu_entries_mirror_and_click_the_real_button(qapp, reader):
    tl.set_layout("A")
    pump(qapp, 0.2)
    menu = QMenu()
    reader.btn_scroll_mode.setChecked(True)
    action = reader.toolbar_layout.add_command_action(menu, "scroll_mode")
    assert action.isCheckable() and action.isChecked()
    assert action.text().startswith("Continuous scroll")
    clicked = []
    reader.btn_print.clicked.disconnect()
    reader.btn_print.clicked.connect(lambda *_: clicked.append(1))
    reader.toolbar_layout.add_command_action(menu, "print").trigger()
    pump(qapp, 0.1)
    assert clicked == [1]


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_command_palette_still_lists_folded_and_hidden_buttons(qapp, reader):
    from riemann.ui.command_palette import toolbar_commands

    tl.save_custom_layout({"ocr": tl.HIDDEN}, False)
    tl.set_layout("custom")
    pump(qapp, 0.2)
    titles = [c.title for c in toolbar_commands(reader, "Reader")]
    assert any("OCR" in t for t in titles)


def test_settings_editor_lists_every_command_and_seeds_custom(qapp, store, monkeypatch):
    from riemann.ui.system.widgets import ToolbarLayoutEditor

    monkeypatch.setattr("riemann.core.settings.app_settings", lambda: store)
    store.setValue(tl.LAYOUT_KEY, "D")
    editor = ToolbarLayoutEditor()
    assert set(editor.place_combos) == {c.key for c in tl.COMMANDS}
    assert editor.layout_name() == "D"
    assert editor.custom.isHidden()
    seen = []
    tl.notifier().changed.connect(lambda: seen.append(1))
    editor.combo.setCurrentIndex(editor.combo.findData("custom"))
    assert not editor.custom.isHidden()
    assert store.value(tl.LAYOUT_KEY) == "custom"
    assert editor.places()["page"] == tl.PILL
    assert seen
    combo = editor.place_combos["notes"]
    combo.setCurrentIndex(combo.findData(tl.HIDDEN))
    assert tl.custom_layout()["places"]["notes"] == tl.HIDDEN
    editor.deleteLater()


def test_reader_bars_take_their_colours_from_the_palette(qapp, store):
    import re

    from riemann.ui.palette import DARK, LIGHT
    from riemann.ui.reader.tab import ReaderTab

    allowed = {v.lower() for v in list(DARK.values()) + list(LIGHT.values())}
    tab = ReaderTab()
    try:
        for mode in (0, 1):
            tab.theme_mode = mode
            tab.apply_theme()
            tab.show_toast("x")
            for widget in (tab.toolbar, tab.search_bar, tab.ai_search_bar, tab.link_tooltip, tab.scroll_content, tab.lbl_toast):
                sheet = widget.styleSheet()
                for colour in re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", sheet):
                    assert colour.lower() in allowed, (widget.objectName(), colour)
    finally:
        tab.cleanup()
        tab.deleteLater()
        pump(qapp, 0.1)


def test_reader_tab_has_everything_the_mixins_expect(qapp, store):
    from riemann.ui.reader.mixins.host import ReaderHost
    from riemann.ui.reader.tab import ReaderTab

    names = set(ReaderHost.__annotations__) | {
        n for n, v in vars(ReaderHost).items() if callable(v) and not n.startswith("__")
    }
    tab = ReaderTab()
    try:
        missing = sorted(n for n in names if not hasattr(tab, n))
        assert missing == []
    finally:
        tab.cleanup()
        tab.deleteLater()
        pump(qapp, 0.1)


def test_full_screen_pill_hides_when_idle_and_returns_on_input(qapp, reader, store, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QKeyEvent

    monkeypatch.setattr(tl.ToolbarLayout, "PILL_IDLE_MS", 50)
    tl.set_layout("default")
    reader.toolbar.hide()
    reader.update_fullscreen_icon(2)
    pump(qapp, 0.2)
    layout = reader.toolbar_layout
    layout.wake_pill()
    assert layout.pill_shown()

    pump(qapp, 1.5, lambda: layout.pill.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
    assert layout._pill_effect.opacity() < 0.01
    assert not layout.pill_shown()

    bar = reader.scroll.verticalScrollBar()
    bar.setValue(bar.value() + 200)
    assert layout.pill_shown()

    pump(qapp, 1.5, lambda: not layout.pill_shown())
    qapp.sendEvent(reader.scroll, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert layout.pill_shown()

    pump(qapp, 1.5, lambda: not layout.pill_shown())
    monkeypatch.setattr(tl.QCursor, "pos", staticmethod(lambda: QPoint(321, 123)))
    pump(qapp, 0.5, layout.pill_shown)
    assert layout.pill_shown()

    reader.update_fullscreen_icon(0)
    reader.toolbar.show()


def test_layout_d_pill_stays_visible_outside_full_screen(qapp, reader, store, monkeypatch):
    monkeypatch.setattr(tl.ToolbarLayout, "PILL_IDLE_MS", 50)
    tl.set_layout("D")
    pump(qapp, 0.2)
    layout = reader.toolbar_layout
    layout.wake_pill()
    pump(qapp, 0.8)
    assert layout.pill_shown()
