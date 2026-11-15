"""
Type stubs for the Riemann Core Rust extension.

This module defines the interface between the Python application and the
compiled Rust backend. It ensures type safety for rendering, OCR, and
document interaction features.
"""

from typing import List, Optional, Tuple

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

    scale: float
    """The scale actually rendered at: the one requested, or less when the
    render would exceed 64 megapixels."""

class RiemannDocument:
    """
    A thread-safe wrapper around a loaded PDF document.

    Boxes returned by search_page, get_text_segments, get_char_boxes,
    get_links and get_form_widgets are (left, top, right, bottom) in points
    on the page as displayed: the crop box with the page's /Rotate applied,
    origin at its bottom-left, y up. They line up with render_page output
    and get_page_size, whatever the page's rotation.
    """

    page_count: int
    """The total number of pages in the document."""

    def render_page(
        self, page_index: int, scale: float, dark_mode_int: int
    ) -> RenderResult:
        """
        Renders a specific page to a bitmap buffer. Annotations Riemann wrote
        into the file are hidden, since Riemann draws them itself.

        Args:
            page_index: Zero-based index of the page.
            scale: Zoom level (e.g., 1.0 for standard, 2.0 for HiDPI).
            dark_mode_int: 1 to enable dark mode color inversion, 0 for standard.

        Returns:
            A RenderResult object containing image dimensions and data.
        """
        ...

    def get_page_size(self, page_index: int) -> Tuple[float, float]:
        """
        Returns the size of a page in PDF points.

        Args:
            page_index: Zero-based index of the page.

        Returns:
            (width, height) in points.
        """
        ...

    def get_links(
        self, page_index: int
    ) -> List[Tuple[str, Tuple[float, float, float, float]]]:
        """
        Extracts clickable links from the page.

        Internal links (explicit or named destinations and GoTo actions) are
        returned as "#page=N", with "&top=Y" appended when the destination names
        a vertical position (PDF user space, origin at the bottom-left).
        External links carry their URI.

        Args:
            page_index: Zero-based index of the page.

        Returns:
            A list of (target, (left, top, right, bottom)) tuples.
        """
        ...

    def signature_count(self) -> int:
        """
        Returns how many digital signatures the document contains, without
        validating them.
        """
        ...

    def get_comments(
        self, page_index: int
    ) -> Tuple[
        Tuple[float, float, float, float],
        List[Tuple[str, str, str, str, Tuple[float, float, float, float]]],
    ]:
        """
        Lists comments left on a page by other applications: every annotation
        with text contents except links, form widgets, popups and the
        annotations Riemann itself wrote (named "riemann:...").

        Args:
            page_index: Zero-based index of the page.

        Returns:
            (crop_box, comments). The crop box is (left, bottom, right, top).
            Each comment is (subtype, contents, author, date, (left, top, right,
            bottom)), with the date as a raw PDF date string.
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

    def ocr_page(self, page_index: int, scale: float, languages: str = "eng") -> str:
        """
        Performs Optical Character Recognition (OCR) on the page.

        Args:
            page_index: Zero-based index of the page.
            scale: Resolution multiplier (higher is better for accuracy).
            languages: Tesseract languages joined by "+", such as "eng+deu".

        Returns:
            The text recognized by the OCR engine.
        """
        ...

    def ocr_pages(self, page_indices: List[int], scale: float, languages: str = "eng") -> List[str]:
        """Reads several pages with one Tesseract process; one string per page, in order."""
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

    def get_char_boxes(
        self, page_index: int
    ) -> List[Tuple[str, Tuple[float, float, float, float]]]:
        """
        Retrieves every character on the page with its loose bounding box, in
        content order. Generated characters (spaces and line breaks PDFium
        synthesises) are included and may have empty boxes.

        Args:
            page_index: Zero-based index of the page.

        Returns:
            A list of (character, (left, top, right, bottom)) tuples.
        """
        ...

    def get_outline(self) -> List[Tuple[int, str, int]]:
        """
        Returns the document outline (bookmarks), flattened depth-first.

        Returns:
            A list of (depth, title, page_index) tuples; page_index is -1 when
            an entry has no destination inside the document.
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

    def load_document(self, path: str, password: Optional[str] = None) -> RiemannDocument:
        """
        Loads a PDF document from the file system.

        Args:
            path: The absolute path to the PDF file.
            password: Optional password for encrypted PDFs.

        Returns:
            A loaded RiemannDocument instance.
        """
        ...
