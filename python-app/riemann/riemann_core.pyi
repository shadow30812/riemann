"""
Type stubs for the Riemann Core Rust extension.

This module defines the interface between the Python application and the
compiled Rust backend. It ensures type safety for rendering, OCR, and
document interaction features.
"""

from typing import List, Tuple

class RenderResult:
    """
    Represents the output of a page rendering operation.
    """

    width: int
    """The width of the rendered image in pixels."""

    height: int
    """The height of the rendered image in pixels."""

    data: bytes
    """The raw BGRA pixel data."""

class RiemannDocument:
    """
    A thread-safe wrapper around a loaded PDF document.
    """

    page_count: int
    """The total number of pages in the document."""

    def render_page(
        self, page_index: int, scale: float, dark_mode_int: int
    ) -> RenderResult:
        """
        Renders a specific page to a bitmap buffer.

        Args:
            page_index: Zero-based index of the page.
            scale: Zoom level (e.g., 1.0 for standard, 2.0 for HiDPI).
            dark_mode_int: 1 to enable dark mode color inversion, 0 for standard.

        Returns:
            A RenderResult object containing image dimensions and data.
        """
        ...

    def get_page_text(self, page_index: int) -> str:
        """
        Extracts all plain text from a specific page.

        Args:
            page_index: Zero-based index of the page.

        Returns:
            The extracted text content.
        """
        ...

    def ocr_page(self, page_index: int, scale: float) -> str:
        """
        Performs Optical Character Recognition (OCR) on the page.

        Args:
            page_index: Zero-based index of the page.
            scale: Resolution multiplier (higher is better for accuracy).

        Returns:
            The text recognized by the OCR engine.
        """
        ...

    def search_page(
        self, page_index: int, query: str
    ) -> List[Tuple[float, float, float, float]]:
        """
        Searches for a text query on the page.

        Args:
            page_index: Zero-based index of the page.
            query: The text string to search for.

        Returns:
            A list of bounding boxes (left, top, right, bottom) for matches.
        """
        ...

    def get_text_segments(
        self, page_index: int
    ) -> List[Tuple[str, Tuple[float, float, float, float]]]:
        """
        Retrieves granular text segments and their positions.

        Args:
            page_index: Zero-based index of the page.

        Returns:
            A list of tuples containing (text, (left, top, right, bottom)).
        """
        ...

    def create_markup_annotation(
        self,
        page_index: int,
        rects: List[Tuple[float, float, float, float]],
        subtype: str,
        color: Tuple[int, int, int],
    ) -> None:
        """
        Adds a markup annotation (highlight, underline, strikeout) to the page.

        Args:
            page_index: Zero-based index of the page.
            rects: List of bounding boxes to annotate.
            subtype: The annotation type ("highlight", "underline", "strikeout").
            color: RGB color tuple (0-255).
        """
        ...

    def get_form_widgets(
        self, page_index: int
    ) -> List[Tuple[int, Tuple[float, float, float, float], str, str, bool]]:
        """
        Retrieves interactive form widgets from the page.

        Args:
            page_index: Zero-based index of the page.

        Returns:
            A list of widgets. Each widget tuple contains:
            (index, bounds, type, string_value, boolean_state).
        """
        ...

class PdfEngine:
    """
    The main entry point for the PDF backend.
    """
    def __init__(self) -> None:
        """Initialize the PDF engine and internal libraries."""
        ...

    def load_document(self, path: str) -> RiemannDocument:
        """
        Loads a PDF document from the file system.

        Args:
            path: The absolute path to the PDF file.

        Returns:
            A loaded RiemannDocument instance.
        """
        ...
