"""
Notes:

The close-while-running cases run in a child process: without the fix Qt
aborts the process ("QThread: Destroyed while thread is still running"), which
would take the whole pytest run down with it. The child inherits the throwaway
HOME and XDG folders conftest.py set up.
"""

import os
import subprocess
import sys
import textwrap
import threading

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread

from riemann.ui import threads

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD_PRELUDE = """
import os, sys, threading, time
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
from riemann.ui.reader.tab import ReaderTab
from riemann.ui.threads import released_count, wait_for_released
from riemann.ui.reader.render_worker import stop_all_render_workers
gate = threading.Event()
started = threading.Event()

def flush():
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()

def close(tab):
    tab.cleanup()
    tab.deleteLater()
    flush()
    print("released", released_count(), flush=True)
    gate.set()
    wait_for_released()
    stop_all_render_workers()
    print("survived", flush=True)
"""


def run_child(body: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu", RIEMANN_RENDER_PROCESS="0")
    env.pop("LD_LIBRARY_PATH", None)
    script = CHILD_PRELUDE + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script], cwd=APP_DIR, env=env, capture_output=True, text=True, timeout=120)


def test_closing_a_reader_while_metadata_is_fetched_does_not_abort():
    result = run_child(
        """
        def slow_get(*args, **kwargs):
            started.set()
            gate.wait(30)
            raise OSError("offline")

        with patch("requests.Session.get", slow_get):
            tab = ReaderTab()
            from riemann.ui.reader.workers import MetadataExtractionWorker
            tab.metadata_worker = MetadataExtractionWorker("doi 10.1234/abcd.efgh", parent=tab)
            tab.metadata_worker.start()
            started.wait(10)
            close(tab)
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "survived" in result.stdout


def test_closing_a_reader_while_signatures_are_checked_does_not_abort():
    result = run_child(
        """
        from riemann.ui.reader.workers import SignatureValidationWorker

        def slow_run(self):
            started.set()
            gate.wait(30)

        with patch.object(SignatureValidationWorker, "run", slow_run):
            tab = ReaderTab()
            tab.sig_worker = SignatureValidationWorker("missing.pdf", [])
            tab.sig_worker.start()
            started.wait(10)
            close(tab)
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "released 1" in result.stdout


def test_closing_a_reader_while_its_pdf_loads_never_terminates_the_thread():
    result = run_child(
        """
        from PySide6.QtCore import QThread
        from riemann.ui.reader.mixins.document import DocumentLoadWorker
        terminated = []

        class SlowEngine:
            def load_document(self, path, password):
                started.set()
                gate.wait(30)
                raise RuntimeError("gone")

        with patch.object(QThread, "terminate", lambda self: terminated.append(self)):
            tab = ReaderTab()
            tab.load_worker = DocumentLoadWorker(SlowEngine(), "big.pdf", None)
            tab.load_worker.start()
            started.wait(10)
            time.sleep(1.2)
            close(tab)
        print("terminated", len(terminated), flush=True)
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "terminated 0" in result.stdout


class _Gate(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gate = threading.Event()
        self.started_flag = threading.Event()

    def run(self):
        self.started_flag.set()
        while not self.gate.is_set() and not self.isInterruptionRequested():
            self.gate.wait(0.01)


def test_released_thread_leaves_its_owner_and_is_asked_to_stop(qapp):
    owner = QObject()
    worker = _Gate(owner)
    worker.start()
    worker.started_flag.wait(5)
    threads.release(worker)
    assert worker.parent() is None
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert worker.wait(5000)
    QCoreApplication.processEvents()
    assert threads.released_count() == 0


def test_library_tab_releases_its_meaning_search(qapp):
    from types import SimpleNamespace

    from riemann.ui.system.lists import LibraryTab, MeaningSearch

    gate = threading.Event()

    class SlowSearch(MeaningSearch):
        def run(self):
            gate.wait(30)

    worker = SlowSearch("q", [])
    worker.start()
    tab = SimpleNamespace(_worker=worker)
    LibraryTab.release(tab)
    assert tab._worker is None
    assert threads.released_count() == 1
    gate.set()
    threads.wait_for_released()
    assert threads.released_count() == 0


def test_quitting_waits_for_tracked_workers_still_running(qapp):
    worker = threads.track(_Gate())
    worker.start()
    worker.started_flag.wait(5)
    threads.wait_for_released(5000)
    assert worker.isFinished()
