from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject
from riemann.core.constants import ViewMode, ZoomMode
from riemann.ui.reader.mixins.rendering import RenderingMixin


class DummyRenderingReader(RenderingMixin, QObject):
    def __init__(self):
        QObject.__init__(self)
        self.current_doc = MagicMock()
        self._render_worker = None
        self.current_page_index = 0
        self.continuous_scroll = False
        self.facing_mode = False
        self.theme_mode = 0
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
        self.scroll.verticalScrollBar().maximum.return_value = 100
        self.scroll.verticalScrollBar().value.return_value = 50
        self.scroll.viewport().width.return_value = 1030
        self.scroll.viewport().height.return_value = 820

        self.scroll_layout = MagicMock()
        self.scroll_content = MagicMock()
        self.scroll_timer = MagicMock()
        self.txt_page = MagicMock()
        self.lbl_total = MagicMock()
        self.settings = MagicMock()
        self.web = MagicMock()

    def devicePixelRatio(self):
        return 1.0

    def ensure_visible(self, index: int) -> None:
        pass


@pytest.fixture
def reader():
    return DummyRenderingReader()


def test_probe_base_page_size(reader):
    reader.current_doc.page_count = 1
    mock_res = MagicMock()
    mock_res.width = 600
    mock_res.height = 800
    reader.current_doc.render_page.return_value = mock_res

    reader._probe_base_page_size()

    assert reader._cached_base_size == (6000, 8000)
    reader.current_doc.render_page.assert_called_once_with(0, 0.1, 0)


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
    assert 3 not in reader.rendered_pages
    assert 10 in reader.rendered_pages

    mock_render_single.assert_any_call(10, 1.0)


@patch("riemann.ui.reader.mixins.rendering.generate_reflow_html")
def test_update_view_reflow(mock_generate, reader):
    reader.view_mode = ViewMode.REFLOW
    reader.current_doc.get_page_text.return_value = "raw pdf text"
    mock_generate.return_value = "<html>reflowed</html>"
    mock_viewport = MagicMock()
    mock_viewport.width.return_value = 1000
    mock_viewport.height.return_value = 800
    reader.scroll.viewport.return_value = mock_viewport

    reader.update_view()

    reader.web.setHtml.assert_called_with("<html>reflowed</html>")


def test_a_page_that_fails_to_render_is_logged_once_and_reported(qapp, caplog):
    import time

    from PySide6.QtCore import QCoreApplication

    from riemann.ui.reader.render_worker import PageRenderWorker

    class BrokenDoc:
        def render_page(self, idx, scale, theme):
            raise RuntimeError("damaged content stream")

    worker = PageRenderWorker()
    failures = []
    worker.page_failed.connect(lambda gen, idx, msg: failures.append((gen, idx, msg)))
    worker.set_document(BrokenDoc(), "gen-1")
    worker.start()
    try:
        with caplog.at_level("WARNING", logger="riemann"):
            for _ in range(2):
                worker.request(4, 1.0, 0, False)
                deadline = time.monotonic() + 5
                while not failures and time.monotonic() < deadline:
                    QCoreApplication.processEvents()
                    time.sleep(0.01)
            time.sleep(0.1)
            QCoreApplication.processEvents()
    finally:
        worker.stop()
        worker.wait(3000)
    assert failures == [("gen-1", 4, "damaged content stream")]
    assert sum("Could not render page 5" in r.getMessage() for r in caplog.records) == 1


def test_failed_page_says_so_on_its_tile(qapp):
    from types import SimpleNamespace

    from riemann.ui.reader.mixins.rendering import RenderingMixin
    from riemann.ui.reader.widgets import PageWidget

    tile = PageWidget()
    host = SimpleNamespace(page_widgets={2: tile}, _render_owner=lambda: "g")
    RenderingMixin._on_page_failed(host, "g", 2, "damaged content stream")
    assert "Page 3 could not be shown" in tile.text()
    tile.deleteLater()
