import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget
from riemann.core import window_snap
from riemann.core.window_snap import WindowSnapper


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture
def snapper(qapp):
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1000, 800)
    screen.availableGeometry.return_value = QRect(0, 30, 1000, 770)
    win = QWidget()
    with patch.object(window_snap.QGuiApplication, "screenAt", return_value=screen):
        yield WindowSnapper(win)
    win.deleteLater()


@pytest.mark.parametrize(
    "point, zone",
    [
        (QPoint(2, 400), "left"),
        (QPoint(997, 400), "right"),
        (QPoint(500, 797), "bottom"),
        (QPoint(500, 2), "maximize"),
        (QPoint(60, 2), "top"),
        (QPoint(960, 2), "top"),
        (QPoint(500, 400), None),
    ],
)
def test_zone_for_pointer_position(snapper, point, zone):
    assert snapper._zone_at(point) == zone


def test_target_rects_use_available_area(snapper):
    p = QPoint(1, 1)
    assert snapper._target_rect("left", p) == QRect(0, 30, 500, 770)
    assert snapper._target_rect("right", p) == QRect(500, 30, 500, 770)
    assert snapper._target_rect("top", p) == QRect(0, 30, 1000, 385)
    assert snapper._target_rect("bottom", p) == QRect(0, 415, 1000, 385)
    assert snapper._target_rect("maximize", p) == QRect(0, 30, 1000, 770)


def test_snapping_is_opt_in_on_wayland(monkeypatch):
    monkeypatch.setattr(window_snap.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert window_snap.snapping_default() is False
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert window_snap.snapping_default() is True


def test_prefer_xwayland_respects_explicit_platform(monkeypatch):
    monkeypatch.setattr(window_snap.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
    assert window_snap.prefer_xwayland_for_snapping() is False
