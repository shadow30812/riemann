import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication
from riemann.ui.explorer import FolderHomeTab
from riemann.ui.palette import DARK, LIGHT, short_folder, tinted_pixmap


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_both_palettes_define_the_same_tokens():
    assert DARK.keys() == LIGHT.keys()


def test_short_folder_abbreviates_home_and_long_paths():
    home = os.path.expanduser("~")
    assert short_folder(os.path.join(home, "papers")) == "~/papers"
    assert short_folder("/mnt/data/projects/riemann/benchmarks/corpus") == "…/riemann/benchmarks/corpus"
    assert short_folder("/srv/docs") == "/srv/docs"


def test_tinted_pixmap_renders_an_icon(qapp):
    pm = tinted_pixmap("folder", "#ff0000", 16)
    assert not pm.isNull()
    assert tinted_pixmap("no-such-icon", "#ff0000", 16).isNull()


def test_folder_home_lists_favourites_and_recents_and_switches_theme(qapp, tmp_path):
    fav = tmp_path / "fav"
    recent = tmp_path / "recent"
    fav.mkdir()
    recent.mkdir()
    window = MagicMock()
    window.dark_mode = True
    window.history_manager.get_list.return_value = [str(recent), str(fav)]
    with patch("riemann.ui.explorer.QSettings") as settings:
        settings.return_value.value.return_value = [str(fav)]
        tab = FolderHomeTab(None, dark_mode=True)
        with patch.object(tab, "window", return_value=window):
            tab._populate_lists()
            assert tab.list_favs.count() == 1
            assert tab.list_recents.count() == 1
            assert tab.lbl_favs_empty.isHidden()

            tab.apply_ui_theme(False)
            assert tab.dark_mode is False
            assert LIGHT["surface"] in tab.styleSheet()
    tab.deleteLater()


def test_folder_home_tab_theme_toggle_survives_being_shown_again(qapp):
    from PySide6.QtGui import QShowEvent

    window = MagicMock()
    window.dark_mode = True
    window.history_manager.get_list.return_value = []
    with patch("riemann.ui.explorer.QSettings") as settings:
        settings.return_value.value.return_value = []
        tab = FolderHomeTab(None, dark_mode=True)
        with patch.object(tab, "window", return_value=window):
            tab.toggle_theme()
            assert tab.dark_mode is False
            assert LIGHT["surface"] in tab.styleSheet()

            tab.showEvent(QShowEvent())
            assert tab.dark_mode is False

            tab.apply_ui_theme(True)
            tab.showEvent(QShowEvent())
            assert tab.dark_mode is True
    tab.deleteLater()


def _luminance(hex_colour):
    from PySide6.QtGui import QColor

    c = QColor(hex_colour)

    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())


@pytest.mark.parametrize("theme", [DARK, LIGHT])
@pytest.mark.parametrize("kind", ["success", "warning", "danger", "accent"])
def test_status_banner_text_is_readable(theme, kind):
    a, b = _luminance(theme[kind]), _luminance(theme["accent_text"])
    ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
    assert ratio >= 4.5, (kind, theme[kind], round(ratio, 2))


def test_no_colour_literals_outside_the_palette():
    import re

    root = os.path.join(os.path.dirname(__file__), "..", "riemann")
    allowed = {os.path.join("ui", "palette.py"), os.path.join("ui", "tooltip.py")}
    pattern = re.compile(r"""["'][^"'\n]*(#[0-9A-Fa-f]{3,8}\b|rgba?\()|QColor\(\s*\d""")
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("assets", "__pycache__")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            if os.path.relpath(path, root) in allowed:
                continue
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    if pattern.search(line):
                        found.append(f"{os.path.relpath(path, root)}:{number}: {line.strip()}")
    assert found == []


def test_signature_banner_follows_the_reader_theme(qapp, qtbot):
    from riemann.ui.reader.tab import ReaderTab

    tab = ReaderTab()
    qtbot.addWidget(tab)
    try:
        tab.theme_mode = 1
        tab._style_signature_banner("success")
        assert DARK["success"] in tab.signature_banner.styleSheet()
        tab.theme_mode = 0
        tab.apply_theme()
        assert LIGHT["success"] in tab.signature_banner.styleSheet()
        tab.update_signature_banner("UNKNOWN_IDENTITY", "Unknown signer")
        assert LIGHT["warning"] in tab.signature_banner.styleSheet()
    finally:
        tab.cleanup()
        tab.deleteLater()
