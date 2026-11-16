"""
Application Constants and Enumerations.

This module defines shared constant values and Enum classes used throughout
the application, specifically for PDF rendering and view modes.

MIN_ZOOM and MAX_ZOOM bound the reader's zoom (1.0 is 100%); clamp_zoom applies
them. FALLBACK_PAGE_SIZE (A4, in points) stands in for the page size until the
first page of a document has been measured.
"""

from enum import Enum

MIN_ZOOM = 0.1
MAX_ZOOM = 5.0
FALLBACK_PAGE_SIZE = (595, 842)


def clamp_zoom(scale: float) -> float:
    return max(MIN_ZOOM, min(scale, MAX_ZOOM))


class ZoomMode(Enum):
    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2
    AUTO_FIT = 3


class ViewMode(Enum):
    """IMAGE renders pages as pictures; REFLOW extracts the text and shows it as HTML."""

    IMAGE = 0
    REFLOW = 1
