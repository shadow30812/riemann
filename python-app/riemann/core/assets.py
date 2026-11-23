"""
Where Riemann's bundled files are, and whether it is running as a built executable.

This is the only module that knows the build layouts. In a source checkout and in
the Nuitka build (whose compiled modules keep their package paths) the assets
sit next to the riemann package. A PyInstaller bundle unpacks them under
sys._MEIPASS/riemann/assets, as Riemann.spec collects them. is_frozen is true
for both builds: PyInstaller sets sys.frozen, Nuitka defines __compiled__ in
every compiled module.

executable is the program that starts Riemann's own child processes (render,
convert, AI). In a Nuitka build sys.executable is made up: the dist folder
joined with the build machine's interpreter name ("python3", "python.exe"),
which doesn't exist, so the running binary is asked for its own path instead.
In a onefile build that is the unpacked binary, which exits with the app
(Nuitka hands it the launcher's process id), and starting it again doesn't
unpack the whole app a second time.

icon_path picks the -white variant of an SVG for dark backgrounds and falls back
to the plain icon when there is no white one.

No Qt imports: the render and AI child processes use this module too.
"""

import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def executable() -> str:
    if "__compiled__" not in globals():
        return sys.executable
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
        return buffer.value
    return os.path.realpath("/proc/self/exe")


def bundle_dir() -> str | None:
    """The PyInstaller unpack folder, or None outside a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", None)
    return None


def asset_path(*parts: str) -> str:
    """Absolute path of a file under riemann/assets."""
    bundle = bundle_dir()
    if bundle:
        return os.path.join(bundle, "riemann", "assets", *parts)
    return os.path.join(PACKAGE_DIR, "assets", *parts)


def icon_path(filename: str, dark: bool = False) -> str:
    """Absolute path of a file in assets/icons; with dark, its -white SVG when one exists."""
    if dark and filename.endswith(".svg") and not filename.endswith("-white.svg"):
        white = asset_path("icons", filename[: -len(".svg")] + "-white.svg")
        if os.path.exists(white):
            return white
    return asset_path("icons", filename)
