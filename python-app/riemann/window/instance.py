"""
Single-instance hand-off: a second launch passes its paths to the running
Riemann over a per-user local socket and exits. Each path is NUL-terminated;
ACTIVATE_MESSAGE only asks the running window to come to the front, and a
connection that sends nothing is a probe and is ignored.

listen_for_instances removes a socket file only when nobody answers on it (a
crashed run left it behind). When another Riemann is listening, for instance
the first window while a second one starts with --new-window, the new process
goes without the socket rather than taking it from the running one. Qt
creates a socket with options set in a temporary folder and renames it into
place, replacing whatever is there, so the probe comes before listen. The
socket is created with UserAccessOption, so only the same user account can
connect, and the running instance opens only what accepts_handoff allows:
existing folders, and existing files that start with a PDF header or whose
name is a PDF, Markdown, HTML, EPUB, Office, CSV, TSV, image or listed text or source file
(core/doctypes.py). The list is explicit on purpose: a file is never accepted
just because its content looks like text.
"""

import hashlib
import os

from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..core.doctypes import (
    EBOOK_SUFFIXES,
    HTML_SUFFIXES,
    IMAGE_SUFFIXES,
    MARKDOWN_SUFFIXES,
    OFFICE_SUFFIXES,
    TABLE_SUFFIXES,
    has_text_name,
)
from ..core.log import log

ACTIVATE_MESSAGE = "--activate"


def single_instance_name() -> str:
    """Name of the local socket a running instance listens on, one per user account."""
    try:
        import getpass

        user = getpass.getuser()
    except Exception:
        user = str(os.getuid()) if hasattr(os, "getuid") else ""
    digest = hashlib.sha256(user.encode("utf-8", "replace")).hexdigest()[:12]
    return f"RiemannSingleInstance-{digest}"


def encode_ipc_paths(paths: list[str]) -> bytes:
    """The message a second launch sends: NUL-terminated paths (NUL cannot occur in a path)."""
    return ("".join(p + "\0" for p in paths) if paths else ACTIVATE_MESSAGE).encode("utf-8")


def decode_ipc_message(data: bytes) -> list[str]:
    """The paths in a message from a second launch; empty when it only asks to come forward."""
    msg = data.decode("utf-8", "replace")
    if not msg or msg == ACTIVATE_MESSAGE:
        return []
    return [p for p in msg.split("\0" if "\0" in msg else "|") if p]


def hand_off_to_running_instance(server_name: str, payload: bytes, attempts: int = 3) -> bool:
    """
    Sends payload to an already running Riemann. Returns False when none is
    listening. A running instance that is busy (the connection times out) is
    retried rather than taken for a stale socket, which the caller would
    delete, cutting the running instance off from later hand-offs.
    """
    for _ in range(attempts):
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(1500):
            socket.write(payload)
            socket.waitForBytesWritten(1500)
            socket.disconnectFromServer()
            return True
        if socket.error() != QLocalSocket.LocalSocketError.SocketTimeoutError:
            return False
    return False


HANDOFF_SUFFIXES = (
    (".pdf", *MARKDOWN_SUFFIXES, *HTML_SUFFIXES, *EBOOK_SUFFIXES, *OFFICE_SUFFIXES, *TABLE_SUFFIXES, *IMAGE_SUFFIXES)
)
PROBE_TIMEOUT_MS = 500


def accepts_handoff(path: str) -> bool:
    if os.path.isdir(path):
        return True
    if not os.path.isfile(path):
        return False
    if path.lower().endswith(HANDOFF_SUFFIXES) or has_text_name(path):
        return True
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def instance_is_listening(server_name: str) -> bool:
    """True when a running Riemann accepts connections on server_name; the probe sends nothing."""
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(PROBE_TIMEOUT_MS):
        return socket.error() == QLocalSocket.LocalSocketError.SocketTimeoutError
    socket.abort()
    return True


def listen_for_instances(server: QLocalServer, server_name: str) -> bool:
    """Listens on the single-instance socket, clearing one left by a crashed run but never a live one."""
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if instance_is_listening(server_name):
        return False
    if server.listen(server_name):
        return True
    QLocalServer.removeServer(server_name)
    return server.listen(server_name)


def handed_over_paths(data: bytes) -> tuple[list[str], list[str]]:
    """The files and folders in a message from a second launch that the running instance should open."""
    files, folders = [], []
    for path in decode_ipc_message(data):
        clean = QUrl(path).toLocalFile() if path.startswith("file://") else path
        if not accepts_handoff(clean):
            log.warning("Ignored a path handed over by another instance: %s", clean)
        elif os.path.isdir(clean):
            folders.append(os.path.abspath(clean))
        else:
            files.append(os.path.abspath(clean))
    return files, folders
