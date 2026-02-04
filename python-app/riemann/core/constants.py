"""
Application Constants and Enumerations.

This module defines shared constant values and Enum classes used throughout
the application, specifically for PDF rendering and view modes.
"""

from enum import Enum


class ZoomMode(Enum):
    """
    Defines the zoom behavior strategy for the PDF viewer.

    Attributes:
        MANUAL (0): Zoom level is set explicitly by the user (e.g., 100%, 150%).
        FIT_WIDTH (1): Zoom level automatically calculates to fit the page width.
        FIT_HEIGHT (2): Zoom level automatically calculates to fit the page height.
    """

    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2


class ViewMode(Enum):
    """
    Defines the rendering pipeline mode for the document.

    Attributes:
        IMAGE (0): Standard PDF rendering where pages are rasterized as images.
                   Best for layout fidelity.
        REFLOW (1): Text extraction mode where content is reflowed via HTML.
                    Best for accessibility and small screens.
    """

    IMAGE = 0
    REFLOW = 1
