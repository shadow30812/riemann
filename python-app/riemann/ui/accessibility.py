"""
Names that screen readers (Orca, NVDA, VoiceOver) read for Riemann's controls.

Most toolbar buttons show only an icon, and Qt gives an icon-only button no
accessible name, so a screen reader announced each one as plain "button".
Their tooltips already say what they do, so name_from_tooltip turns the
tooltip into the accessible name: "AI Semantic Search (Ctrl+I)" becomes the
name "AI Semantic Search" and the description "Shortcut: Ctrl+I", plus any
further tooltip lines. A text box without a tooltip is named by its
placeholder ("Find in page...").

TooltipSkin (ui/tooltip.py) already attaches to every widget Riemann makes,
including ones added later, so it calls name_from_tooltip when it attaches
and again on every ToolTipChange: a button whose tooltip changes (Download
becomes Cancel Download) is renamed with it. A name set in code is kept; the
names made here are marked with the AUTO_NAME property so they can be told
apart and replaced.

Settings > Accessibility can turn the names off (SCREEN_READER_NAMES_KEY);
set_enabled then takes the names made here off every widget, or puts them
back.

Buttons with visible text, check boxes and labels need nothing: Qt reads their
text.
"""

import html
import re

from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
)

AUTO_NAME = "riemannAutoName"
AUTO_DESCRIPTION = "riemannAutoDescription"
_enabled = True
NAMED = (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox, QAbstractSlider)


def describe_tip(tip: str) -> tuple[str, str]:
    """(name, description) for a tooltip, plain or rich text."""
    from .tooltip import parse_tip

    text = html.unescape(re.sub(r"<br\s*/?>", "\n", tip or "", flags=re.I))
    text = re.sub(r"<[^>]+>", "", text)
    title, keys, body = parse_tip(text)
    parts = []
    if keys:
        parts.append("Shortcut: " + "+".join(keys))
    if body:
        parts.append(body)
    return title, ". ".join(parts)


def set_enabled(on: bool) -> None:
    """Turns the tooltip names on or off, for every widget that exists now and later."""
    global _enabled
    _enabled = bool(on)
    for widget in QApplication.allWidgets():
        name_from_tooltip(widget)


def name_from_tooltip(widget) -> None:
    """Names widget for screen readers from its tooltip (or placeholder), unless code already named it."""
    if not isinstance(widget, NAMED):
        return
    current = widget.accessibleName()
    auto = widget.property(AUTO_NAME) or ""
    if current and current != auto:
        return
    if not _enabled:
        if auto:
            widget.setAccessibleName("")
            widget.setProperty(AUTO_NAME, "")
        if widget.property(AUTO_DESCRIPTION) and widget.accessibleDescription() == widget.property(AUTO_DESCRIPTION):
            widget.setAccessibleDescription("")
            widget.setProperty(AUTO_DESCRIPTION, "")
        return
    if isinstance(widget, QAbstractButton) and widget.text().replace("&", "").strip():
        return
    name, description = describe_tip(widget.toolTip())
    if not name and isinstance(widget, QLineEdit):
        name = widget.placeholderText().strip()
    if name == current:
        return
    widget.setAccessibleName(name)
    widget.setProperty(AUTO_NAME, name)
    own_description = widget.accessibleDescription()
    if description and (not own_description or own_description == widget.property(AUTO_DESCRIPTION)):
        widget.setAccessibleDescription(description)
        widget.setProperty(AUTO_DESCRIPTION, description)
