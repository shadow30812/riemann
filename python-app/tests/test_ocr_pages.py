"""
Tests for OCR of a page range: RiemannDocument.ocr_pages (one Tesseract run
per batch), OcrPagesThread (batching, progress, failure and cancel), and the
reader's "Recognise text in pages…" action with its save-as-text window.
"""

import os
import shutil
import time
from unittest.mock import patch

import pytest

from riemann.ui.reader.workers import OcrPagesThread

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus")
ATTENTION = os.path.abspath(os.path.join(CORPUS, "attention.pdf"))

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None or not os.path.exists(ATTENTION), reason="needs tesseract and the corpus"
)


class FakeDoc:
    def __init__(self, fail_on_call=None, on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call
        self.on_call = on_call

    def ocr_pages(self, pages, scale, languages):
        self.calls.append(list(pages))
        if self.on_call:
            self.on_call()
        if len(self.calls) == self.fail_on_call:
            raise RuntimeError("Tesseract execution failed")
        return [f"text {p}" for p in pages]


def run(thread, qapp):
    seen = {"progress": [], "done": None}
    thread.progress.connect(lambda done, total: seen["progress"].append((done, total)))
    thread.finished_ocr.connect(lambda results, problem: seen.__setitem__("done", (results, problem)))
    thread.start()
    assert thread.wait(10_000)
    qapp.processEvents()
    return seen


def test_thread_reads_pages_in_batches_of_eight(qapp):
    doc = FakeDoc()
    seen = run(OcrPagesThread(doc, list(range(20)), 1.0, "eng"), qapp)
    assert [len(c) for c in doc.calls] == [8, 8, 4]
    assert seen["progress"] == [(8, 20), (16, 20), (20, 20)]
    results, problem = seen["done"]
    assert problem == ""
    assert [tuple(r) for r in results] == [(p, f"text {p}") for p in range(20)]


def test_thread_keeps_the_pages_read_before_a_failure(qapp):
    doc = FakeDoc(fail_on_call=2)
    results, problem = run(OcrPagesThread(doc, list(range(20)), 1.0, "eng"), qapp)["done"]
    assert len(doc.calls) == 2
    assert [p for p, _ in results] == list(range(8))
    assert "Tesseract" in problem


def test_thread_stops_after_the_current_batch_when_cancelled(qapp):
    holder = {}
    doc = FakeDoc(on_call=lambda: holder["thread"].requestInterruption())
    thread = OcrPagesThread(doc, list(range(20)), 1.0, "eng")
    holder["thread"] = thread
    results, problem = run(thread, qapp)["done"]
    assert len(doc.calls) == 1
    assert len(results) == 8
    assert problem == "Cancelled"


@needs_tesseract
def test_ocr_pages_matches_ocr_page_page_by_page():
    import riemann_core

    doc = riemann_core.PdfEngine().load_document(ATTENTION, None)
    batch = doc.ocr_pages([2, 0], 1.5)
    assert batch == [doc.ocr_page(2, 1.5), doc.ocr_page(0, 1.5)]
    assert "Attention" in batch[1]
    assert doc.ocr_pages([], 1.0) == []
    with pytest.raises(ValueError):
        doc.ocr_pages([0, 60_000], 1.0)
    with pytest.raises(RuntimeError):
        doc.ocr_pages([0], 1.0, "eng;rm")


@needs_tesseract
def test_reader_recognises_a_page_range_and_saves_the_text(qapp, qtbot, tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox, QMenu, QPlainTextEdit

    from riemann.ui.reader.tab import ReaderTab

    tab = ReaderTab()
    qtbot.addWidget(tab)
    tab.show()
    try:
        tab.load_document(ATTENTION)
        end = time.time() + 20
        while time.time() < end and tab.current_doc is None:
            qapp.processEvents()
            time.sleep(0.01)
        menu = QMenu()
        tab.build_pages_menu(menu, [0])
        assert any(a.text() == "Recognise text in page 1…" for a in menu.actions())

        with patch("riemann.ui.reader.mixins.ai.QInputDialog.getText", return_value=("1-2", True)):
            tab.perform_ocr_pages([0])
        tab._ocr_thread.wait(60_000)
        end = time.time() + 5
        while time.time() < end and getattr(tab, "_ocr_text_dialog", None) is None:
            qapp.processEvents()
            time.sleep(0.01)
        dialog = tab._ocr_text_dialog
        text = dialog.findChild(QPlainTextEdit).toPlainText()
        assert text.startswith("--- Page 1 ---\n")
        assert "--- Page 2 ---" in text and "Attention" in text

        dest = str(tmp_path / "out.txt")
        with patch("riemann.ui.reader.mixins.ai.QFileDialog.getSaveFileName", return_value=(dest, "")):
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        with open(dest, encoding="utf-8") as f:
            assert f.read() == text
        dialog.close()
    finally:
        tab.cleanup()
        tab.deleteLater()
        qapp.processEvents()


def test_closing_the_tab_mid_run_drops_the_results(qapp):
    import threading

    from riemann.ui.reader.mixins import ai

    gate = threading.Event()
    doc = FakeDoc(on_call=lambda: gate.wait(5))

    class Host(ai.AiMixin):
        _ai_reply = None
        shown = []

        def _show_ocr_text(self, results):
            self.shown.append(results)

    host = Host()
    thread = OcrPagesThread(doc, list(range(20)), 1.0, "eng")
    thread.finished_ocr.connect(lambda results, problem: host._show_ocr_text(results))
    host._ocr_thread = thread
    thread.start()
    host._kill_ai_engine()
    assert thread in ai._ORPHANED_THREADS
    gate.set()
    assert thread.wait(10_000)
    qapp.processEvents()
    assert len(doc.calls) <= 1
    assert host.shown == []
