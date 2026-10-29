"""
Entry point script for the frozen (Nuitka or PyInstaller) application.

This script acts as the bootstrap loader for the Riemann application when
packaged as a standalone executable. It imports the main run function
from the application package and executes it.

The Windows build has no console, so a crash during startup used to vanish
without a trace. Startup errors and native crashes are now written to
crash.log in the app's data folder (%LOCALAPPDATA%\\Riemann on Windows,
~/.local/share/Riemann on Linux), and on Windows a message box says where.
"""

import faulthandler
import os
import sys
import traceback


def _log_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, "Riemann")
    os.makedirs(path, exist_ok=True)
    return path


try:
    _CRASH_LOG = os.path.join(_log_dir(), "crash.log")
    # Append: a second launch that hands its files to the running window must not
    # wipe what the first one wrote.
    _crash_file = open(_CRASH_LOG, "a", encoding="utf-8")
    faulthandler.enable(_crash_file)  # segfaults and aborts in Qt or the Rust core
except OSError:
    _CRASH_LOG, _crash_file = None, None


def _report_crash() -> None:
    text = traceback.format_exc()
    try:
        sys.stderr.write(text)
    except Exception:
        pass
    if _crash_file:
        _crash_file.write(text)
        _crash_file.flush()
    if sys.platform == "win32":
        try:
            import ctypes

            where = f"\n\nDetails were saved to:\n{_CRASH_LOG}" if _CRASH_LOG else ""
            last = text.strip().splitlines()[-1] if text.strip() else "Unknown error"
            ctypes.windll.user32.MessageBoxW(None, f"Riemann failed to start.\n\n{last}{where}", "Riemann", 0x10)
        except Exception:
            pass


if sys.platform.startswith("linux"):
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
        else:
            # The link points back at the unpack folder. Nuitka's onefile cleanup
            # follows links, so the loop made it give up part way and every launch
            # left about 1 GB behind in /tmp. Remove it before the bootstrap cleans up.
            import atexit

            def _drop_qt_lib_link(path=_qt_lib):
                try:
                    os.unlink(path)
                except OSError:
                    pass

            atexit.register(_drop_qt_lib_link)

    # Ensure QT_PLUGIN_PATH points to Nuitka's extracted plugins
    _plugins_dir = os.path.join(_this_dir, "PySide6", "qt-plugins")
    if os.path.exists(_plugins_dir):
        os.environ["QT_PLUGIN_PATH"] = _plugins_dir

    try:
        # Explicitly import QtDBus so Nuitka bundles libQt6DBus.so.6 on Linux
        import PySide6.QtDBus  # noqa: F401
    except ImportError:
        pass


if __name__ == "__main__":
    try:
        from riemann.app import run

        run()
    except SystemExit:
        raise
    except BaseException:
        _report_crash()
        sys.exit(1)
