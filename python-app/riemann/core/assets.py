"""
Where Riemann's bundled files are, and whether it is running as a built executable.

This is the only module that knows the build layouts. In a source checkout and in
the Nuitka build (whose compiled modules keep their package paths) the assets
sit next to the riemann package. A PyInstaller bundle unpacks them under
sys._MEIPASS/riemann/assets, as Riemann.spec collects them. is_frozen is true
for both builds: PyInstaller sets sys.frozen, Nuitka defines __compiled__ in
every compiled module.

icon_path picks the -white variant of an SVG for dark backgrounds and falls back
to the plain icon when there is no white one.

No Qt imports: the render and AI child processes use this module too.
"""

import os
import sys
from typing import Optional

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def bundle_dir() -> Optional[str]:
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
