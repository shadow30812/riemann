"""
Keyboard shortcuts that more than one visible widget registers for the same keys.

Reader and browser tabs register their shortcuts at window scope, so they work
wherever the focus is in the window. In split view two tabs are visible at
once and both own, say, Ctrl+F. Qt then counts the key as ambiguous: it emits
activatedAmbiguously on one of the shortcuts and runs none of them.
share_keys connects that signal on each of a widget's own shortcuts, and
run_focused runs the copy whose widget holds the focus (the innermost one,
when a window-wide shortcut such as the AI panel's Ctrl+L shares the keys),
or the first visible copy when the focus is in none of them.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QWidget


def share_keys(owner: QWidget) -> None:
    """Lets owner's own shortcuts settle presses that other visible widgets share."""
    for shortcut in owner.findChildren(QShortcut, "", Qt.FindChildOption.FindDirectChildrenOnly):
        shortcut.activatedAmbiguously.connect(lambda s=shortcut: run_focused(s))


def _owner(shortcut: QShortcut):
    parent = shortcut.parent()
    return parent if isinstance(parent, QWidget) else None


def _depth(widget: QWidget) -> int:
    depth = 0
    while widget is not None:
        depth += 1
        widget = widget.parentWidget()
    return depth


def run_focused(shortcut: QShortcut) -> None:
    owner = _owner(shortcut)
    if owner is None:
        return
    rivals = [
        s
        for s in owner.window().findChildren(QShortcut)
        if s.key() == shortcut.key() and s.isEnabled() and _owner(s) is not None and _owner(s).isVisible()
    ]
    focus = QApplication.focusWidget()
    holding = [s for s in rivals if focus is not None and (_owner(s) is focus or _owner(s).isAncestorOf(focus))]
    chosen = max(holding, key=lambda s: _depth(_owner(s))) if holding else (rivals[0] if rivals else None)
    if chosen is not None:
        chosen.activated.emit()
