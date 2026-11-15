"""
Application Constants and Enumerations.

This module defines shared constant values and Enum classes used throughout
the application, specifically for PDF rendering and view modes.
"""

from enum import Enum


class ZoomMode(Enum):
    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2
    AUTO_FIT = 3


class ViewMode(Enum):
    """IMAGE renders pages as pictures; REFLOW extracts the text and shows it as HTML."""

    IMAGE = 0
    REFLOW = 1
