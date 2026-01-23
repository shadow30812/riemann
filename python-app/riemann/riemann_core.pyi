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

class PdfEngine:
    def __init__(self) -> None: ...
    def load_document(self, path: str) -> RiemannDocument: ...
