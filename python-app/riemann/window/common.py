"""
Helpers shared by the window modules: resource paths and window_class(),
which returns RiemannWindow without a circular import (the mixins create new
windows and reach the window registry through it). The last-used folder for
file dialogs lives in core.settings.
"""

import os
import sys


def get_resource_path(relative_path: str) -> str:
    """Absolute path of a resource, in the source tree or a PyInstaller bundle."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = getattr(sys, "_MEIPASS")
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return os.path.join(base_path, relative_path)


def window_class():
    """RiemannWindow, imported when first needed."""
    from ..app import RiemannWindow

    return RiemannWindow

