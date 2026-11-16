"""
The AI search engine process, shared by every reader tab.

One engine runs per app. It is started the first time a tab needs it, on a
free port picked here, with a random token it requires on every request (see
core/ai_server.py), and stopped when the app exits. Tabs send it HTTP
requests; closing a tab never stops the engine another tab is using.

The engine is part of the app: python -m riemann.core.ai_server from a
source checkout, or the frozen executable run with SERVER_FLAG (checked in
build_entry.py before Qt is imported). It downloads its model on first use,
pinned by SHA-256, into the models folder of the user cache directory, and
keeps its log and vector cache (ai-index) there too, since the install
folder is usually read-only.

The engine never opens PDFs; the app sends page text (page_texts, through
the Rust core). search_library asks the engine to search many papers at once
(the Library tab's meaning search); papers it has no vectors for come back
in "needs_text", are read and indexed here, and the search is asked again.
Papers that can't be read (password-protected ones, for instance) are
reported as skipped. While the engine loads its model it answers 503 with a
detail starting "loading", and search_library waits.
"""

import atexit
import os
import secrets
import socket
import subprocess
import sys
import time
from typing import List, Optional, Tuple

from .log import log
from .assets import PACKAGE_DIR, is_frozen
from .endpoints import LOOPBACK, loopback_url

_process: Optional[subprocess.Popen] = None
_port: Optional[int] = None
_token: str = ""
_log_file = None


def _log_path() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    folder = os.path.join(base, "Riemann")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "ai_engine.log")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


SERVER_FLAG = "--riemann-ai-sidecar"


def _command() -> Tuple[List[str], str]:
    """The command that starts the engine, and its working directory."""
    if is_frozen():
        return [sys.executable, SERVER_FLAG], os.path.dirname(sys.executable)
    package_root = os.path.dirname(PACKAGE_DIR)
    return [sys.executable, "-m", "riemann.core.ai_server"], package_root


def page_texts(pdf_path: str, password: Optional[str] = None) -> List[str]:
    """The text of every page, through the Rust core."""
    import riemann_core

    doc = riemann_core.PdfEngine().load_document(pdf_path, password)
    return [doc.get_page_text(i) for i in range(doc.page_count)]


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def ensure_started() -> Optional[Tuple[int, str]]:
    """
    Starts the engine unless it is already running. Returns (port, token), or None when
    it could not start.
    """
    global _process, _port, _token, _log_file
    if is_running():
        return _port, _token

    cmd, cwd = _command()

    port = _free_port()
    token = secrets.token_urlsafe(24)
    env = dict(
        os.environ,
        RIEMANN_AI_PORT=str(port),
        RIEMANN_AI_TOKEN=token,
        RIEMANN_AI_CACHE=os.path.join(os.path.dirname(_log_path()), "ai-index"),
        RIEMANN_AI_MODELS=os.path.join(os.path.dirname(_log_path()), "models", "all-MiniLM-L6-v2-onnx"),
    )
    if not is_frozen():
        env["PYTHONPATH"] = os.pathsep.join([cwd] + [p for p in sys.path if p and os.path.isdir(p)])
    try:
        if _log_file is None or _log_file.closed:
            try:
                _log_file = open(_log_path(), "w")
            except OSError:
                _log_file = None
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _process = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=_log_file or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **kwargs,
        )
    except Exception as e:
        print(f"[Riemann] Failed to start AI engine: {e}")
        _process = None
        return None

    _port, _token = port, token
    return port, token


def connection() -> Optional[Tuple[int, str]]:
    """(port, token) of the running engine, or None when it is not running."""
    return (_port, _token) if is_running() else None


def stop() -> None:
    """Stops the engine and every process it started."""
    global _process, _log_file
    process = _process
    _process = None
    if process is not None and process.poll() is None:
        try:
            import psutil

            parent = psutil.Process(process.pid)
            procs = parent.children(recursive=True) + [parent]
            for p in procs:
                try:
                    p.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(procs, timeout=2.0)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
        except Exception:
            try:
                process.kill()
            except Exception:
                log.debug("stop failed", exc_info=True)
    if _log_file is not None and not _log_file.closed:
        try:
            _log_file.close()
        except Exception:
            log.debug("stop failed", exc_info=True)
    _log_file = None


atexit.register(stop)


def _post(port: int, token: str, endpoint: str, payload: dict, timeout: float, patience: float) -> dict:
    """POSTs to the engine, waiting while it starts (connection refused) or loads its model (503)."""
    import requests

    started = time.time()
    while True:
        try:
            response = requests.post(
                loopback_url(port, endpoint),
                json=payload,
                headers={"X-Riemann-Token": token},
                timeout=timeout,
            )
        except requests.ConnectionError:
            if time.time() - started > 120:
                raise RuntimeError("The AI engine did not start.")
            time.sleep(1)
            continue
        if response.status_code == 503 and time.time() - started < patience:
            time.sleep(1)
            continue
        if response.status_code != 200:
            raise RuntimeError(f"The AI engine answered {response.status_code}: {response.text[:200]}")
        return response.json()


def search_library(query: str, pdf_paths: list, top_k: int = 20, timeout: float = 3600.0) -> dict:
    """
    The best passages for query across pdf_paths. Papers the engine has not read
    before are read and indexed first, which takes a while the first time; their
    vectors are cached on disk afterwards. Raises RuntimeError if the engine can't
    start or answers with an error.
    """
    started = ensure_started()
    if started is None:
        raise RuntimeError("The AI engine could not be started.")
    port, token = started
    payload = {"query": query, "pdf_paths": list(pdf_paths), "top_k": top_k}
    data = _post(port, token, "search_library", payload, timeout, patience=1800)
    if data.get("needs_text"):
        for path in data["needs_text"]:
            try:
                pages = page_texts(path)
                _post(port, token, "index", {"pdf_path": path, "pages": pages}, timeout, patience=1800)
            except Exception:
                log.debug("Could not index %s for library search", path, exc_info=True)
        data = _post(port, token, "search_library", payload, timeout, patience=1800)
    data["skipped"] = list(data.get("skipped", [])) + list(data.get("needs_text", []))
    data["needs_text"] = []
    return data
