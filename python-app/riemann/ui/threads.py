"""
Background QThreads that may still be running when the widget that started them closes.

Qt aborts the whole process when a running QThread is destroyed, and
QThread.terminate kills a thread wherever it is, including inside PDFium with
the riemann_core lock held, which leaves the lock taken for every other tab.
So a closing tab never deletes or terminates its workers; it hands each one to
release, which disconnects the signals that would reach the tab, asks the
thread to stop (requestInterruption), takes it out of the tab's object tree and
keeps it alive here until it finishes on its own; then it is deleted.

Quitting does not close tabs one by one, so workers are also registered with
track when they start. wait_for_released runs on aboutToQuit and again at
interpreter exit; it asks every tracked thread to stop and waits for those
still running, released or tracked, each up to RELEASE_WAIT_MS. Workers that make
network requests check isInterruptionRequested between requests, so they end
within one request timeout.

track also keeps the worker alive until it has finished. A tab clears its
worker attribute in the slot of the worker's last signal, and that signal is
emitted at the end of run, before run returns: when the slot ran first, the
tab held the last reference, and the thread was destroyed while still running,
from inside its own run. Both track and release let a worker go only after
finished, on the main thread (a queued connection), and after wait, because
finished is emitted while the thread still counts as running.
"""

import atexit
import warnings
from typing import Optional

import shiboken6
from PySide6.QtCore import QThread, Qt, SignalInstance

RELEASE_WAIT_MS = 10000

_RELEASED: "set[QThread]" = set()
_TRACKED: "set[QThread]" = set()


def track(thread: QThread) -> QThread:
    """Registers a worker so quitting waits for it and it outlives its owner's reference; returns the thread."""
    _TRACKED.add(thread)
    thread.finished.connect(lambda t=thread: _untrack(t), Qt.ConnectionType.QueuedConnection)
    return thread


def _untrack(thread: QThread) -> None:
    if shiboken6.isValid(thread):
        thread.wait()
    _TRACKED.discard(thread)


def _forget(thread: QThread) -> None:
    if shiboken6.isValid(thread):
        thread.wait()
        thread.deleteLater()
    _RELEASED.discard(thread)


def release(thread: Optional[QThread], *signals: SignalInstance) -> None:
    """Detaches thread from its owner and lets it finish in the background; signals are disconnected first."""
    if thread is None or not shiboken6.isValid(thread):
        return
    for signal in signals:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
    if not thread.isRunning():
        thread.setParent(None)
        thread.deleteLater()
        return
    thread.requestInterruption()
    thread.setParent(None)
    _RELEASED.add(thread)
    thread.finished.connect(lambda t=thread: _forget(t), Qt.ConnectionType.QueuedConnection)
    if thread.isFinished():
        _forget(thread)


def released_count() -> int:
    return sum(1 for t in _RELEASED if shiboken6.isValid(t) and t.isRunning())


def wait_for_released(timeout_ms: int = RELEASE_WAIT_MS) -> None:
    threads = [t for t in list(_RELEASED) + list(_TRACKED) if shiboken6.isValid(t)]
    for thread in threads:
        thread.requestInterruption()
    for thread in threads:
        if shiboken6.isValid(thread):
            thread.wait(timeout_ms)
    _RELEASED.clear()


atexit.register(wait_for_released)
