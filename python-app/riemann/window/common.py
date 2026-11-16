"""
Helpers shared by the window modules: window_class() returns RiemannWindow
without a circular import (the mixins create new windows and reach the window
registry through it). The last-used folder for file dialogs lives in
core.settings, bundled files in core.assets.
"""


def window_class():
    """RiemannWindow, imported when first needed."""
    from ..app import RiemannWindow

    return RiemannWindow

