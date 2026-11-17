"""
Tests for per-document render processes (riemann/core/render_process.py):
pages rendered in the child match in-process renders, a stalled tab does not
hold up another, and rendering falls back to the main process when the child
cannot start or dies.
"""

import os
import signal
import sys
import threading
import time

import pytest

from riemann.core import render_process as rp

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus")
ATTENTION = os.path.abspath(os.path.join(CORPUS, "attention.pdf"))
DIFFUSION = os.path.abspath(os.path.join(CORPUS, "latent_diffusion.pdf"))

pytestmark = pytest.mark.skipif(
    not (os.path.exists(ATTENTION) and os.path.exists(DIFFUSION)), reason="benchmark corpus not downloaded"
)


def load(path):
    import riemann_core

    return riemann_core.PdfEngine().load_document(path, None)


@pytest.fixture
def renderers():
    made = []

    def make(path, **kwargs):
        renderer = rp.PageRenderer(load(path), path, None, **kwargs)
        made.append(renderer)
        return renderer

    yield make
    for renderer in made:
        renderer.close()


def test_child_renders_the_same_pixels_as_the_main_process(renderers):
    renderer = renderers(ATTENTION)
    assert renderer.wait_ready(20)
    assert not renderer.in_process
    child = renderer.render_page(1, 1.5, 0)
    local = renderer.doc.render_page(1, 1.5, 0)
    assert isinstance(child, rp.RenderedPage)
    assert (child.width, child.height, child.scale) == (local.width, local.height, local.scale)
    assert bytes(child.data) == bytes(local.data)
    assert renderer.page_count == renderer.doc.page_count


def test_each_document_gets_its_own_process(renderers):
    a, b = renderers(ATTENTION), renderers(DIFFUSION)
    assert a.wait_ready(20) and b.wait_ready(20)
    assert a.process.pid != b.process.pid != os.getpid()


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGSTOP")
def test_a_stalled_tab_does_not_hold_up_another(renderers):
    a, b = renderers(ATTENTION), renderers(DIFFUSION)
    assert a.wait_ready(20) and b.wait_ready(20)
    os.kill(a.process.pid, signal.SIGSTOP)
    try:
        stuck = threading.Thread(target=lambda: a.render_page(0, 2.0, 0), daemon=True)
        stuck.start()
        time.sleep(0.2)
        assert stuck.is_alive()
        started = time.time()
        pages = [b.render_page(i, 1.0, 0) for i in range(3)]
        assert time.time() - started < 5
        assert all(isinstance(p, rp.RenderedPage) and p.width > 0 for p in pages)
    finally:
        os.kill(a.process.pid, signal.SIGCONT)


def test_falls_back_to_the_main_process_when_the_child_cannot_start(renderers, monkeypatch, caplog):
    monkeypatch.setattr(rp, "child_command", lambda: [os.path.join(CORPUS, "no-such-program")])
    renderer = renderers(ATTENTION)
    assert renderer.process.failed
    assert renderer.in_process
    page = renderer.render_page(0, 1.0, 0)
    assert page.width > 0 and not isinstance(page, rp.RenderedPage)
    assert any("rendering in-process" in r.getMessage() for r in caplog.records)


def test_falls_back_when_the_child_dies(renderers):
    renderer = renderers(ATTENTION)
    assert renderer.wait_ready(20)
    renderer.process.proc.kill()
    renderer.process.proc.wait(5)
    page = renderer.render_page(0, 1.0, 0)
    assert page.width > 0
    assert renderer.in_process
    assert renderer.render_page(1, 1.0, 0).width > 0


def test_bad_page_raises_like_the_document_does(renderers):
    renderer = renderers(ATTENTION)
    assert renderer.wait_ready(20)
    with pytest.raises(ValueError):
        renderer.render_page(10_000, 1.0, 0)
    assert not renderer.in_process
    assert renderer.render_page(0, 1.0, 0).width > 0


def test_child_exits_when_closed(renderers):
    renderer = renderers(ATTENTION)
    assert renderer.wait_ready(20)
    proc = renderer.process.proc
    renderer.close()
    assert proc.wait(5) is not None


def test_reader_tab_renders_through_its_own_process(qapp, qtbot):
    from riemann.ui.reader.tab import ReaderTab

    tab = ReaderTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    try:
        tab.load_document(ATTENTION)
        end = time.time() + 20
        while time.time() < end and (tab.current_doc is None or tab.page_renderer is None or tab.page_renderer.in_process):
            qapp.processEvents()
            time.sleep(0.01)
        assert tab.page_renderer is not None and not tab.page_renderer.in_process
        assert tab._render_worker._doc is tab.page_renderer
        assert tab.sidebar_nav.doc is tab.page_renderer
        proc = tab.page_renderer.process.proc
    finally:
        tab.cleanup()
        tab.deleteLater()
        qapp.processEvents()
    assert tab.page_renderer is None
    assert proc.wait(5) is not None


def test_frozen_entry_point_serves_pages_without_qt(renderers, monkeypatch):
    entry = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "build_entry.py"))
    monkeypatch.setattr(rp, "child_command", lambda: [sys.executable, entry, rp.CHILD_FLAG])
    renderer = renderers(ATTENTION)
    assert renderer.wait_ready(20), renderer.process.failed
    assert renderer.render_page(0, 1.0, 0).width > 0
    maps = f"/proc/{renderer.process.pid}/maps"
    if os.path.exists(maps):
        assert "libQt6" not in open(maps).read()


def test_page_edits_close_the_render_process_before_rewriting(qapp, qtbot, tmp_path):
    import shutil

    from riemann.core import page_edit
    from riemann.ui.reader.tab import ReaderTab

    path = str(tmp_path / "paper.pdf")
    shutil.copy(ATTENTION, path)
    tab = ReaderTab()
    qtbot.addWidget(tab)
    tab.show()
    try:
        tab.load_document(path)
        end = time.time() + 20
        while time.time() < end and (tab.page_renderer is None or tab.page_renderer.in_process):
            qapp.processEvents()
            time.sleep(0.01)
        proc = tab.page_renderer.process.proc
        seen = {}

        def rotate(p, pages, password=None):
            seen["exited"] = proc.poll() is not None
            return page_edit.rotate_pages(p, pages, 90, password)

        assert tab._edit_pages(rotate, [0])
        assert seen["exited"]
    finally:
        tab.cleanup()
        tab.deleteLater()
        qapp.processEvents()
