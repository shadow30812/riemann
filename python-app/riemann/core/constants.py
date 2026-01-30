from enum import Enum


class ZoomMode(Enum):
    """
    Enumeration defining the zoom behavior of the PDF viewer.

    Attributes:
        MANUAL: Zoom level is set explicitly by the user.
        FIT_WIDTH: Zoom level automatically adjusts to fit the page width to the viewport.
        FIT_HEIGHT: Zoom level automatically adjusts to fit the page height to the viewport.
    """

    MANUAL = 0
    FIT_WIDTH = 1
    FIT_HEIGHT = 2


class ViewMode(Enum):
    """
    Enumeration defining the rendering mode of the document.

    Attributes:
        IMAGE: Standard PDF rendering where pages are drawn as images.
        REFLOW: Text extraction mode rendered via HTML for easier reading on small screens.
    """

    IMAGE = 0
    REFLOW = 1
