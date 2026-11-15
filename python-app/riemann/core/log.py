"""
Riemann's log.

Errors the app deliberately recovers from (a favicon that will not load, a
process that already exited, an optional module that is missing) are recorded
here instead of being dropped, so a misbehaving feature leaves a trace.

setup() is called once at startup. It writes everything from DEBUG up to
riemann.log in the cache folder (~/.cache/Riemann on Linux, rotated at 1 MB,
three files kept) and repeats warnings and errors on stderr. Before setup(),
for example under tests, records go through Python's default handling.
"""

import logging
import logging.handlers
import os

from PySide6.QtCore import QStandardPaths

log = logging.getLogger("riemann")

_configured = False


def log_folder() -> str:
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), ".cache", "Riemann")
    os.makedirs(folder, exist_ok=True)
    return folder


def setup() -> str:
    """Attaches the file and stderr handlers; returns the log file's path."""
    global _configured
    path = os.path.join(log_folder(), "riemann.log")
    if _configured:
        return path
    log.setLevel(logging.DEBUG)
    try:
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(module)s: %(message)s"))
        log.addHandler(handler)
    except OSError:
        pass
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("[Riemann] %(levelname)s: %(message)s"))
    log.addHandler(console)
    log.propagate = False
    _configured = True
    return path
