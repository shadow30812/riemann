"""
The mini player on stand-in windows (QWidgets with mocked tab widgets).

Each MiniAudioPlayer starts a poll timer, and a stand-in window and its
player keep each other alive, so only the garbage collector frees them. Left
polling, they ran during later tests, and a collection inside a poll freed a
window that QApplication.topLevelWidgets had just returned: a segfault in CI.
clean_windows deletes every stand-in at teardown and checks that no player
is left polling.
"""

import gc
import sys
from unittest.mock import MagicMock, patch
import pytest
import shiboken6
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

if not QApplication.instance():
    _qapp = QApplication(sys.argv)

from riemann.app import RiemannWindow
from riemann.core.mini_player import MiniAudioPlayer


STAND_INS: list = []


def polling_players() -> list:
    return [
        o for o in gc.get_objects()
        if isinstance(o, MiniAudioPlayer) and shiboken6.isValid(o) and o.poll_timer.isActive()
    ]


@pytest.fixture(autouse=True)
def clean_windows():
    RiemannWindow._all_open_windows = []
    yield
    RiemannWindow._all_open_windows = []
    while STAND_INS:
        widget = STAND_INS.pop()
        if shiboken6.isValid(widget):
            shiboken6.delete(widget)
    assert not polling_players()


def stand_in() -> QWidget:
    widget = QWidget()
    STAND_INS.append(widget)
    return widget


def make_window():
    win = stand_in()
    win.dark_mode = False
    win.tabs_main = MagicMock(spec=QTabWidget)
    win.tabs_main.count.return_value = 0
    win.tabs_main.isVisible.return_value = False
    win.tabs_main.hasFocus.return_value = False
    win.tabs_main.currentWidget.return_value = None

    win.tabs_side = MagicMock(spec=QTabWidget)
    win.tabs_side.count.return_value = 0
    win.tabs_side.isVisible.return_value = False
    win.tabs_side.hasFocus.return_value = False
    win.tabs_side.currentWidget.return_value = None
    return win


def create_mock_browser(recently_audible=False):
    b = stand_in()
    b.web = MagicMock()
    page = MagicMock()
    page.recentlyAudible.return_value = recently_audible
    b.web.page.return_value = page
    return b


def test_is_browser_valid():
    win = make_window()
    player = MiniAudioPlayer(win)

    assert player._is_browser_valid(None) is False
    assert player._is_browser_valid("not_a_widget") is False

    mock_b = create_mock_browser()
    assert player._is_browser_valid(mock_b) is True

    no_web = stand_in()
    assert player._is_browser_valid(no_web) is False


def test_get_all_app_browsers_across_multiple_windows():
    win1 = make_window()
    win2 = make_window()

    b1 = create_mock_browser()
    b2 = create_mock_browser()
    reader = stand_in()

    win1.tabs_main.count.return_value = 2
    win1.tabs_main.widget.side_effect = lambda idx: [b1, reader][idx]

    win1.tabs_side.count.return_value = 1
    win1.tabs_side.widget.side_effect = lambda idx: [b2][idx]

    b3 = create_mock_browser()
    win2.tabs_main.count.return_value = 1
    win2.tabs_main.widget.side_effect = lambda idx: [b3][idx]

    win2.tabs_side.count.return_value = 0

    player = MiniAudioPlayer(win1)
    with patch.object(QApplication, "topLevelWidgets", return_value=[win1, win2]):
        browsers = player._get_all_app_browsers()

    assert len(browsers) == 3
    assert b1 in browsers
    assert b2 in browsers
    assert b3 in browsers
    assert reader not in browsers


def test_find_active_browser_prioritizes_audible():
    win = make_window()
    player = MiniAudioPlayer(win)

    silent_b = create_mock_browser(recently_audible=False)
    audible_b = create_mock_browser(recently_audible=True)

    player._get_all_app_browsers = MagicMock(return_value=[silent_b, audible_b])

    active = player.find_active_browser()
    assert active == audible_b


def test_find_active_browser_preserves_active_on_tab_switch():
    win = make_window()
    player = MiniAudioPlayer(win)

    active_b = create_mock_browser(recently_audible=False)
    player.active_browser = active_b
    player._get_all_app_browsers = MagicMock(return_value=[active_b])

    non_browser_tab = stand_in()
    win.tabs_main.isVisible.return_value = True
    win.tabs_main.hasFocus.return_value = True
    win.tabs_main.currentWidget.return_value = non_browser_tab

    result = player.find_active_browser()
    assert result == active_b


def test_player_controls_execution():
    win = make_window()
    player = MiniAudioPlayer(win)

    active_b = create_mock_browser()
    player.active_browser = active_b

    player.toggle_playback()
    assert active_b.web.page().runJavaScript.called

    player.slider_pressed()
    assert player._is_seeking is True

    player.position_slider.setValue(45)
    player.slider_released()
    assert active_b.web.page().runJavaScript.call_count >= 2

    assert player.format_time(0) == "00:00"
    assert player.format_time(65) == "01:05"
    assert player.format_time(3665) == "1:01:05"


def test_player_controls_without_browser_no_crash():
    win = make_window()
    player = MiniAudioPlayer(win)
    player.active_browser = None
    player.find_active_browser = MagicMock(return_value=None)

    player.toggle_playback()
    player.slider_pressed()
    player.slider_released()
    player.poll_media_state()
    assert player.isVisible() is False
