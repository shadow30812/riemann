class RenderResult:
    width: int
    height: int
    data: bytes

class RiemannDocument:
    page_count: int

    def render_page(
        self, page_index: int, scale: float, dark_mode_int: int
    ) -> RenderResult: ...
    def get_page_text(self, page_index: int) -> str: ...
    def ocr_page(self, page_index: int, scale: float) -> str: ...
    def search_page(
        self, page_index: int, query: str
    ) -> list[tuple[float, float, float, float]]: ...

class PdfEngine:
    def __init__(self) -> None: ...
    def load_document(self, path: str) -> RiemannDocument: ...
