from unittest.mock import MagicMock, patch

import pytest
from riemann.core.constants import ViewMode, ZoomMode
from riemann.ui.reader.mixins.rendering import RenderingMixin


class DummyRenderingReader(RenderingMixin):
    def __init__(self):
        self.current_doc = MagicMock()
        self.current_page_index = 0
        self.continuous_scroll = False
        self.facing_mode = False
        self.dark_mode = False
        self.virtual_threshold = 50
        self._virtual_enabled = False
        self._virtual_range = (0, 0)
        self.zoom_mode = ZoomMode.FIT_WIDTH
        self.view_mode = ViewMode.IMAGE
        self.manual_scale = 1.0
        self._cached_base_size = None

        self.page_widgets = {}
        self.rendered_pages = set()
        self.form_widgets = {}
        self.form_values_cache = {}
        self.annotations = {}
        self.search_result = None

        self.scroll = MagicMock()
        self.scroll_layout = MagicMock()
        self.scroll_content = MagicMock()
        self.txt_page = MagicMock()
        self.lbl_total = MagicMock()
        self.settings = MagicMock()
        self.web = MagicMock()

    def devicePixelRatio(self):
        return 1.0


@pytest.fixture
def reader():
    return DummyRenderingReader()


def test_probe_base_page_size(reader):
    mock_res = MagicMock()
    mock_res.width = 600
    mock_res.height = 800
    reader.current_doc.render_page.return_value = mock_res

    reader._probe_base_page_size()

    assert reader._cached_base_size == (600, 800)
    reader.current_doc.render_page.assert_called_once_with(0, 1.0, 0)


def test_calculate_scale_manual(reader):
    reader.zoom_mode = ZoomMode.MANUAL
    reader.manual_scale = 1.5
    assert reader.calculate_scale() == 1.5


def test_calculate_scale_fit_width(reader):
    reader.zoom_mode = ZoomMode.FIT_WIDTH
    reader._cached_base_size = (500, 700)

    mock_viewport = MagicMock()
    mock_viewport.width.return_value = 1030
    mock_viewport.height.return_value = 820
    reader.scroll.viewport.return_value = mock_viewport

    assert reader.calculate_scale() == 2.0

    reader.facing_mode = True
    assert reader.calculate_scale() == 1.0


@patch("riemann.ui.reader.mixins.rendering.QApplication")
@patch.object(DummyRenderingReader, "_build_standard_layout")
@patch.object(DummyRenderingReader, "_build_virtual_layout")
def test_rebuild_layout_dispatch(mock_virtual, mock_standard, mock_qapp, reader):
    reader.current_doc.page_count = 10

    reader.scroll_layout.count.return_value = 0
    reader.continuous_scroll = True
    reader.virtual_threshold = 50
    reader.rebuild_layout()
    mock_standard.assert_called_once_with(10)
    mock_virtual.assert_not_called()

    mock_standard.reset_mock()

    reader.current_doc.page_count = 100
    reader.rebuild_layout()
    mock_virtual.assert_called_once_with(100)
    mock_standard.assert_not_called()


@patch.object(DummyRenderingReader, "calculate_scale", return_value=1.0)
@patch.object(DummyRenderingReader, "_render_single_page")
def test_render_visible_pages(mock_render_single, mock_calc_scale, reader):
    reader.current_doc.page_count = 20
    reader.current_page_index = 10

    for i in range(20):
        reader.page_widgets[i] = MagicMock()

    reader.rendered_pages = {2, 3}
    reader.render_visible_pages()

    assert 2 not in reader.rendered_pages
    assert 3 in reader.rendered_pages
    assert 10 in reader.rendered_pages

    mock_render_single.assert_any_call(10, 1.0)


@patch("riemann.ui.reader.mixins.rendering.generate_reflow_html")
def test_update_view_reflow(mock_generate, reader):
    reader.view_mode = ViewMode.REFLOW
    reader.current_doc.get_page_text.return_value = "raw pdf text"
    mock_generate.return_value = "<html>reflowed</html>"

    reader.update_view()

    reader.web.setHtml.assert_called_with("<html>reflowed</html>")
