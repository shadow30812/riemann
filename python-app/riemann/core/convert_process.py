"""
Turning documents into HTML in a child process with a time limit.

A book or office file from anywhere may be broken or built to make a parser
spin or eat memory. convert(kind, path) runs the converter for kind in a child
process, so the worst a file can do is be refused: the child is killed after
timeout seconds, and a crash or a memory error in it only ends the child.

CONVERTERS maps a kind to "module:function"; the function takes a path and
returns a dict that json can write (for a book: title, html, sections, note).
It raises an exception with a message for the person when it can't. The
child writes its answer as JSON to a file the parent names, so nothing a
library prints can corrupt it; stdout and stderr are discarded.

The child is this module run as a program (python -m
riemann.core.convert_process KIND PATH OUT) or, in a frozen build, the
executable with CHILD_FLAG, which build_entry.py checks before importing Qt.
Converters import no Qt. RIEMANN_CONVERT_PROCESS=0 in the environment runs
them in this process instead (for debugging).

convert blocks, so callers run it on a worker thread (ConvertWorker in
ui/reader/mixins/document.py). cancel() kills the child of a conversion still
running, for a tab that closes first.
"""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from typing import Optional

from .assets import PACKAGE_DIR, executable, is_frozen
from .processes import NO_WINDOW

CHILD_FLAG = "--riemann-convert"
TIMEOUT = 120.0
CONVERTERS = {
    "ebook": "riemann.core.epub:read_epub",
    "office": "riemann.core.office_basic:read_document",
    "spreadsheet": "riemann.core.office_basic:read_spreadsheet",
}


class ConvertError(Exception):
    pass


def _converter(kind: str):
    module, name = CONVERTERS[kind].split(":")
    return getattr(importlib.import_module(module), name)


def child_command(kind: str, path: str, out: str) -> list:
    if is_frozen():
        return [executable(), CHILD_FLAG, kind, path, out]
    return [sys.executable, "-m", "riemann.core.convert_process", kind, path, out]


def _child_env() -> dict:
    env = dict(os.environ)
    if not is_frozen():
        paths = [p for p in sys.path if p and os.path.isdir(p)]
        env["PYTHONPATH"] = os.pathsep.join([os.path.dirname(PACKAGE_DIR)] + paths)
    return env


class Conversion:
    """One conversion; run() blocks until it ends, cancel() from another thread stops it."""

    def __init__(self, kind: str, path: str, timeout: float = TIMEOUT):
        if kind not in CONVERTERS:
            raise ConvertError(f"Riemann has no converter for {kind}.")
        self.kind, self.path, self.timeout = kind, path, timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._cancelled = False

    def run(self) -> dict:
        if os.environ.get("RIEMANN_CONVERT_PROCESS") == "0":
            return _converter(self.kind)(self.path)
        handle, out = tempfile.mkstemp(prefix="riemann-convert-", suffix=".json")
        os.close(handle)
        try:
            with self._lock:
                if self._cancelled:
                    raise ConvertError("Cancelled.")
                self._proc = subprocess.Popen(
                    child_command(self.kind, self.path, out),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=_child_env(),
                    **NO_WINDOW,
                )
            try:
                code = self._proc.wait(self.timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
                raise ConvertError(f"Reading the file took more than {int(self.timeout)} seconds, so Riemann stopped.")
            if self._cancelled:
                raise ConvertError("Cancelled.")
            try:
                with open(out, encoding="utf-8") as f:
                    answer = json.load(f)
            except (OSError, ValueError):
                raise ConvertError(f"The file couldn't be read (the reader stopped with code {code}).")
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
        if "error" in answer:
            raise ConvertError(answer["error"])
        return answer["result"]

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()


def convert(kind: str, path: str, timeout: float = TIMEOUT) -> dict:
    return Conversion(kind, path, timeout).run()


def serve(argv: list) -> int:
    kind, path, out = argv[:3]
    try:
        answer = {"result": _converter(kind)(path)}
    except MemoryError:
        answer = {"error": "There isn't enough memory to read this file."}
    except Exception as error:
        answer = {"error": str(error) or type(error).__name__}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(answer, f)
    return 0


if __name__ == "__main__":
    sys.exit(serve(sys.argv[1:]))
