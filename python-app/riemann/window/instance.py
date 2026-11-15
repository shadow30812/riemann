"""
Single-instance hand-off: a second launch passes its paths to the running
Riemann over a per-user local socket and exits. Each path is NUL-terminated;
an empty message only asks the running window to come to the front.
"""

import os
import hashlib
from typing import List
from PySide6.QtNetwork import QLocalServer, QLocalSocket


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


def encode_ipc_paths(paths: List[str]) -> bytes:
    """The message a second launch sends: NUL-terminated paths (NUL cannot occur in a path)."""
    return ("".join(p + "\0" for p in paths) if paths else ACTIVATE_MESSAGE).encode("utf-8")


def decode_ipc_message(data: bytes) -> List[str]:
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


def listen_for_instances(server: QLocalServer, server_name: str) -> bool:
    """Listens on the single-instance socket, clearing one left by a crashed run."""
    if server.listen(server_name):
        return True
    QLocalServer.removeServer(server_name)
    return server.listen(server_name)
