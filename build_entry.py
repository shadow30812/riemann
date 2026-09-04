"""
Entry point script for the frozen PyInstaller application.

This script acts as the bootstrap loader for the Riemann application when
packaged as a standalone executable. It imports the main run function
from the application package and executes it.
"""

import os
import sys

# Self-sanitization: Protect against toxic library paths in LD_LIBRARY_PATH (e.g. /opt/plecs)
_ld_path = os.environ.get("LD_LIBRARY_PATH", "")
if "plecs" in _ld_path and "_RIEMANN_SANITIZED" not in os.environ:
    _clean_parts = [p for p in _ld_path.split(":") if "plecs" not in p and p]
    if _clean_parts:
        os.environ["LD_LIBRARY_PATH"] = ":".join(_clean_parts)
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)
    os.environ["_RIEMANN_SANITIZED"] = "1"
    try:
        _exe = os.environ.get("NUITKA_ONEFILE_BINARY") or sys.executable
        if not os.path.exists(_exe) and os.path.exists("/proc/self/exe"):
            _exe = "/proc/self/exe"
        if os.path.exists(_exe):
            os.execv(_exe, sys.argv if sys.argv else [_exe])
    except Exception:
        pass

# Ensure PySide6/Qt/lib points to onefile root so any Qt library lookups resolve
_this_dir = os.path.dirname(os.path.abspath(__file__))
_qt_dir = os.path.join(_this_dir, "PySide6", "Qt")
_qt_lib = os.path.join(_qt_dir, "lib")
if not os.path.exists(_qt_lib):
    try:
        os.makedirs(_qt_dir, exist_ok=True)
        os.symlink(_this_dir, _qt_lib)
    except Exception:
        pass

# Ensure QT_PLUGIN_PATH points to Nuitka's extracted plugins
_plugins_dir = os.path.join(_this_dir, "PySide6", "qt-plugins")
if os.path.exists(_plugins_dir):
    os.environ["QT_PLUGIN_PATH"] = _plugins_dir

from riemann.app import run

try:
    # Explicitly import QtDBus so Nuitka bundles libQt6DBus.so.6 on Linux
    import PySide6.QtDBus
except ImportError:
    pass

if __name__ == "__main__":
    run()
