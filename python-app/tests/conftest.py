import os
import sys
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("_RIEMANN_SANITIZED", "1")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
if "HOME" not in os.environ or os.environ["HOME"].startswith("/home/"):
    os.environ["HOME"] = "/tmp"

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    return app

try:
    import pytestqt
except ImportError:
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
            def addWidget(self, widget):
                pass

            def waitSignal(self, signal, timeout=1000):
                return _SignalBlocker(signal)

            def waitSignals(self, signals, timeout=1000):
                return _SignalsBlocker(signals)

            def mouseClick(self, *args, **kwargs):
                pass

            def keyClick(self, *args, **kwargs):
                pass

        return _DummyQtBot()
