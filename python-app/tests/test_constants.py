from riemann.core.constants import ViewMode, ZoomMode


def test_zoom_mode_values():
    assert ZoomMode.MANUAL.value == 0
    assert ZoomMode.FIT_WIDTH.value == 1
    assert ZoomMode.FIT_HEIGHT.value == 2


def test_view_mode_values():
    assert ViewMode.IMAGE.value == 0
    assert ViewMode.REFLOW.value == 1
