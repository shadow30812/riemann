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
        MANUAL (int): Zoom level is set explicitly by the user (e.g., 100%, 150%).
        FIT_WIDTH (int): Zoom level automatically calculates to fit the page width.
        FIT_HEIGHT (int): Zoom level automatically calculates to fit the page height.
        AUTO_FIT (int): Zoom level automatically calculates to fit the page optimally.
    """

    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2
    AUTO_FIT = 3


class ViewMode(Enum):
    """
    Defines the rendering pipeline mode for the document.

    Attributes:
        IMAGE (int): Standard PDF rendering where pages are rasterized as images.
                    Best for layout fidelity.
        REFLOW (int): Text extraction mode where content is reflowed via HTML.
                    Best for accessibility and small screens.
    """

    IMAGE = 0
    REFLOW = 1
