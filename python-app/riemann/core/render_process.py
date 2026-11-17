"""
Page rendering in a separate process per document.

PDFium is not thread-safe, so the Rust core serialises every call through one
lock per process. With all tabs in one process, a slow page in one tab (a
dense map, a scanned poster) held up rendering in every other tab. Each reader
now gets a child process with its own PDFium that opens the same file and
renders its pages, so tabs only wait on themselves. Text, links, forms and the
outline still come from the document in the main process; those calls are
short.

PageRenderer is what the reader hands to its render worker, the sidebar and
printing in place of the document: render_page goes to the child once it has
opened the file, and every other attribute is the document's own. Until the
child is ready, or if it cannot start or dies, render_page renders in-process
as before, so a missing interpreter or a frozen-build quirk costs speed, never
pages. Each failure is logged once.

The child is this module run as a program (python -m
riemann.core.render_process) or, in a frozen build, the executable with
CHILD_FLAG, which build_entry.py checks before importing anything else. It
imports only riemann_core: no Qt. It talks over its stdin and stdout with
length-prefixed frames: a pickled header, then for a page the raw pixel bytes.
Anything else printed to stdout is redirected to stderr first so it cannot
corrupt a frame. The child exits when its stdin closes, so it never outlives
the app, and close() also stops it.

RIEMANN_RENDER_PROCESS=0 in the environment turns child processes off.

Requests to one child are strictly one at a time (PageRenderer holds a lock
around each exchange); callers on several threads simply queue.

Notes:

_return_page_buffers_to_os is shared with render_worker; the reason is in its
docstring. The module logs through the "riemann" logger by name rather than
core.log, which imports Qt.
"""

import atexit
import ctypes
import logging
import os
import pickle
import struct
import subprocess
import sys
import threading
import weakref
from typing import Any, Optional

from .assets import PACKAGE_DIR, executable, is_frozen
from .processes import NO_WINDOW

log = logging.getLogger("riemann")

CHILD_FLAG = "--riemann-render-process"
_FRAME = struct.Struct("<I")
_OPEN_TIMEOUT = 30.0


def _return_page_buffers_to_os() -> None:
    """
    Makes glibc give freed page buffers back to the OS.

    A page at Fit Width is about 10 MB. glibc serves blocks that large with
    mmap, but after the first one is freed it raises its threshold and puts
    later ones on the heap of whichever thread allocated them, where freed
    memory mostly stays. Fixing the threshold at 1 MB keeps page-sized blocks
    on mmap.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL("libc.so.6")
        M_MMAP_THRESHOLD = -3
        libc.mallopt(M_MMAP_THRESHOLD, 1024 * 1024)
    except (OSError, AttributeError):
        pass


def _write(stream, header: Any, payload: bytes = b"") -> None:
    data = pickle.dumps(header, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_FRAME.pack(len(data)) + data)
    if payload:
        stream.write(payload)
    stream.flush()


def _read_exact(stream, size: int) -> bytes:
    data = stream.read(size)
    if data is None or len(data) != size:
        raise EOFError("render process closed its pipe")
    return data


def _read(stream) -> Any:
    (size,) = _FRAME.unpack(_read_exact(stream, _FRAME.size))
    return pickle.loads(_read_exact(stream, size))


def serve(stdin=None, stdout=None) -> None:
    """The child's main loop: open one document, render pages until stdin closes."""
    _return_page_buffers_to_os()
    if stdout is None:
        stdout = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    if stdin is None:
        stdin = sys.stdin.buffer
    doc = None
    while True:
        try:
            request = _read(stdin)
        except (EOFError, OSError, struct.error):
            return
        kind = request[0]
        if kind == "quit":
            return
        try:
            if kind == "open":
                import riemann_core

                _, path, password = request
                doc = riemann_core.PdfEngine().load_document(path, password)
                _write(stdout, ("opened", doc.page_count))
            elif kind == "render":
                _, page, scale, theme = request
                if doc is None:
                    raise RuntimeError("no document open")
                res = doc.render_page(page, scale, theme)
                _write(stdout, ("page", res.width, res.height, res.scale, len(res.data)), bytes(res.data))
            else:
                raise ValueError(f"unknown request {kind!r}")
        except Exception as error:
            try:
                _write(stdout, ("error", f"{type(error).__name__}: {error}"))
            except (OSError, ValueError):
                return


class RenderedPage:
    """The parts of riemann_core.RenderResult the reader reads, for a page from a child."""

    __slots__ = ("data", "width", "height", "scale")

    def __init__(self, data: bytes, width: int, height: int, scale: float) -> None:
        self.data = data
        self.width = width
        self.height = height
        self.scale = scale


def child_command() -> list:
    """How to start a render child from this interpreter or frozen executable."""
    if is_frozen():
        return [executable(), CHILD_FLAG]
    return [sys.executable, "-m", "riemann.core.render_process"]


def _child_env() -> dict:
    env = dict(os.environ)
    if not is_frozen():
        paths = [p for p in sys.path if p and os.path.isdir(p)]
        package_root = os.path.dirname(PACKAGE_DIR)
        env["PYTHONPATH"] = os.pathsep.join([package_root] + paths)
    env["PYTHONUNBUFFERED"] = "1"
    return env


class RenderProcess:
    """One child process with one document open in it."""

    def __init__(self, path: str, password: Optional[str]) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.failed: Optional[str] = None
        self.proc: Optional[subprocess.Popen] = None
        try:
            self.proc = subprocess.Popen(
                child_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                env=_child_env(),
                **NO_WINDOW,
            )
        except OSError as error:
            self._fail(f"could not start: {error}")
            return
        threading.Thread(target=self._open, args=(path, password), daemon=True).start()

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc is not None else None

    def _fail(self, reason: str) -> None:
        if self.failed is None:
            self.failed = reason
            log.warning("Render process for %s unavailable (%s); rendering in-process", self.path, reason)
        self.ready.clear()
        self._kill()

    def _open(self, path: str, password: Optional[str]) -> None:
        with self.lock:
            try:
                _write(self.proc.stdin, ("open", path, password))
                reply = _read(self.proc.stdout)
            except (OSError, EOFError, ValueError, struct.error) as error:
                self._fail(f"open failed: {error}")
                return
            if reply[0] != "opened":
                self._fail(str(reply[1] if len(reply) > 1 else reply))
                return
            self.ready.set()

    def render_page(self, page: int, scale: float, theme: int) -> RenderedPage:
        with self.lock:
            if not self.ready.is_set():
                raise RuntimeError("render process not ready")
            try:
                _write(self.proc.stdin, ("render", page, scale, theme))
                reply = _read(self.proc.stdout)
                if reply[0] == "error":
                    raise ValueError(reply[1])
                _, width, height, used_scale, size = reply
                data = _read_exact(self.proc.stdout, size)
            except (OSError, EOFError, struct.error) as error:
                self._fail(f"render failed: {error}")
                raise RuntimeError(str(error)) from error
        return RenderedPage(data, width, height, used_scale)

    def _kill(self) -> None:
        proc = self.proc
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        try:
            proc.wait(1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(1.0)
            except subprocess.TimeoutExpired:
                pass

    def close(self) -> None:
        self.ready.clear()
        if self.proc is not None and self.proc.poll() is None:
            if self.lock.acquire(timeout=0.5):
                try:
                    _write(self.proc.stdin, ("quit",))
                except (OSError, ValueError):
                    pass
                finally:
                    self.lock.release()
        self._kill()


_LIVE: "weakref.WeakSet[PageRenderer]" = weakref.WeakSet()


class PageRenderer:
    """A document whose render_page runs in its own process when it can."""

    enabled = os.environ.get("RIEMANN_RENDER_PROCESS", "1") != "0"

    def __init__(self, doc: Any, path: str, password: Optional[str] = None) -> None:
        self.doc = doc
        self.process: Optional[RenderProcess] = None
        if self.enabled and path and os.path.exists(path):
            self.process = RenderProcess(path, password)
        _LIVE.add(self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.doc, name)

    def wait_ready(self, timeout: float = _OPEN_TIMEOUT) -> bool:
        return self.process is not None and self.process.ready.wait(timeout)

    @property
    def in_process(self) -> bool:
        return self.process is None or not self.process.ready.is_set()

    def render_page(self, page: int, scale: float, theme: int):
        process = self.process
        if process is not None and process.ready.is_set():
            try:
                return process.render_page(page, scale, theme)
            except RuntimeError:
                pass
        return self.doc.render_page(page, scale, theme)

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            process.close()


def close_all() -> None:
    for renderer in list(_LIVE):
        renderer.close()


atexit.register(close_all)


if __name__ == "__main__":
    serve()
