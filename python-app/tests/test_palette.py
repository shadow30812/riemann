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
            assert tab.list_recents.count() == 1  # favourites are not repeated
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
            tab.toggle_theme()  # N
            assert tab.dark_mode is False
            assert LIGHT["surface"] in tab.styleSheet()

            tab.showEvent(QShowEvent())  # switching back to the tab
            assert tab.dark_mode is False

            tab.apply_ui_theme(True)  # Ctrl+D hands it back to the interface theme
            tab.showEvent(QShowEvent())
            assert tab.dark_mode is True
    tab.deleteLater()
