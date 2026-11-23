"""
Notes:

Every test session gets its own HOME and XDG config, data and cache folders,
removed at exit, so no test reads or writes a real Riemann profile or another
run's leftovers.

qapp: BrowserTab attaches a CaptionsServer to the app; it is normally stopped
on aboutToQuit, which never fires under pytest. Its running QThread would
otherwise abort the interpreter on exit.

_delete_leftover_widgets: at the end of the session every top-level widget
still alive (windows, reader tabs made on their own) is deleted while the
application still runs. A reader's web page left alive until Qt WebEngine
releases its profile at exit crashed the interpreter after the tests passed.

qtbot (fallback when pytest-qt is missing): addWidget remembers the widget, and
teardown stops its timers and hides it. Windows left polling (the mini player
checks every top-level widget twice a second) crashed later tests that were
deleting widgets.
"""

import atexit
import importlib.util
import os
import shutil
import sys
import tempfile

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("_RIEMANN_SANITIZED", "1")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
_SANDBOX = tempfile.mkdtemp(prefix="riemann-tests-")
for _var, _sub in (
    ("HOME", "home"),
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_CACHE_HOME", "cache"),
):
    os.environ[_var] = os.path.join(_SANDBOX, _sub)
    os.makedirs(os.environ[_var], exist_ok=True)
atexit.register(shutil.rmtree, _SANDBOX, True)

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    yield app
    server = getattr(app, "captions_server", None)
    if server is not None:
        server.stop()
        del app.captions_server

@pytest.fixture(scope="session", autouse=True)
def _delete_leftover_widgets():
    yield
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if not shiboken6.isValid(widget):
            continue
        for timer in widget.findChildren(QTimer):
            timer.stop()
        cleanup = getattr(widget, "cleanup", None)
        if callable(cleanup) and getattr(widget, "current_path", None) is not None:
            try:
                cleanup()
            except Exception:
                pass
        widget.deleteLater()
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


if importlib.util.find_spec("pytestqt") is None:
    @pytest.fixture
    def qtbot(qapp):
        """Fallback qtbot fixture when pytest-qt is not installed."""
        class _SignalBlocker:
            def __init__(self, signal):
                self.signal = signal
                self.args = []
                def slot(*args):
                    self.args = list(args)
                try:
                    self.signal.connect(slot)
                except Exception:
                    pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

        class _SignalsBlocker:
            def __init__(self, signals):
                self.signals = signals

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

        class _DummyQtBot:
            def __init__(self):
                self.widgets = []

            def addWidget(self, widget):
                self.widgets.append(widget)

            def waitSignal(self, signal, timeout=1000):
                return _SignalBlocker(signal)

            def waitSignals(self, signals, timeout=1000):
                return _SignalsBlocker(signals)

            def mouseClick(self, *args, **kwargs):
                pass

            def keyClick(self, *args, **kwargs):
                pass

        bot = _DummyQtBot()
        yield bot
        for widget in bot.widgets:
            if not shiboken6.isValid(widget):
                continue
            for timer in widget.findChildren(QTimer):
                timer.stop()
            widget.hide()
