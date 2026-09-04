"""
PDF Hyperlink Extractor for Riemann.
Extracts internal document destinations (TOC, cross-references, named destinations)
and external URIs with precise bounding boxes directly via PDFium C API.
"""

import ctypes
import os
import sys
from typing import Dict, List, Optional, Tuple


class FS_RECTF(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_float),
        ("top", ctypes.c_float),
        ("right", ctypes.c_float),
        ("bottom", ctypes.c_float),
    ]


class PdfLinkExtractor:
    _instance: Optional["PdfLinkExtractor"] = None

    def __init__(self) -> None:
        self.pdfium: Optional[ctypes.CDLL] = None
        self._doc_cache: Dict[str, ctypes.c_void_p] = {}
        self._page_links_cache: Dict[
            Tuple[str, int], List[Tuple[str, Tuple[float, float, float, float]]]
        ] = {}
        self._init_pdfium()

    @classmethod
    def get_instance(cls) -> "PdfLinkExtractor":
        if cls._instance is None:
            cls._instance = PdfLinkExtractor()
        return cls._instance

    def _init_pdfium(self) -> None:
        base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        candidates = [
            os.path.join(base_dir, "libs", "libpdfium.so"),
            os.path.join(base_dir, "libs", "pdfium.dll"),
            os.path.join(base_dir, "libpdfium.so"),
            os.path.join(base_dir, "pdfium.dll"),
            os.path.join(os.path.dirname(__file__), "libpdfium.so"),
            os.path.join(os.path.dirname(__file__), "pdfium.dll"),
            "/usr/lib64/libpdfium.so",
            "/usr/lib/libpdfium.so",
            "/usr/local/lib/libpdfium.so",
        ]

        lib_path = None
        for path in candidates:
            if os.path.exists(path):
                lib_path = path
                break

        if not lib_path:
            return

        try:
            self.pdfium = ctypes.CDLL(lib_path)

            self.pdfium.FPDF_InitLibraryWithConfig.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDF_InitLibraryWithConfig.restype = None
            self.pdfium.FPDF_InitLibraryWithConfig(None)

            self.pdfium.FPDF_LoadDocument.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
            self.pdfium.FPDF_LoadDocument.restype = ctypes.c_void_p

            self.pdfium.FPDF_GetPageCount.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDF_GetPageCount.restype = ctypes.c_int

            self.pdfium.FPDF_LoadPage.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            self.pdfium.FPDF_LoadPage.restype = ctypes.c_void_p

            self.pdfium.FPDF_ClosePage.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDF_ClosePage.restype = None

            self.pdfium.FPDF_CloseDocument.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDF_CloseDocument.restype = None

            self.pdfium.FPDFPage_GetAnnotCount.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDFPage_GetAnnotCount.restype = ctypes.c_int

            self.pdfium.FPDFPage_GetAnnot.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            self.pdfium.FPDFPage_GetAnnot.restype = ctypes.c_void_p

            self.pdfium.FPDFPage_CloseAnnot.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDFPage_CloseAnnot.restype = None

            self.pdfium.FPDFAnnot_GetLink.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDFAnnot_GetLink.restype = ctypes.c_void_p

            self.pdfium.FPDFAnnot_GetRect.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(FS_RECTF),
            ]
            self.pdfium.FPDFAnnot_GetRect.restype = ctypes.c_int

            self.pdfium.FPDFLink_GetDest.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            self.pdfium.FPDFLink_GetDest.restype = ctypes.c_void_p

            self.pdfium.FPDFDest_GetDestPageIndex.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            self.pdfium.FPDFDest_GetDestPageIndex.restype = ctypes.c_int

            self.pdfium.FPDFLink_GetAction.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDFLink_GetAction.restype = ctypes.c_void_p

            self.pdfium.FPDFAction_GetType.argtypes = [ctypes.c_void_p]
            self.pdfium.FPDFAction_GetType.restype = ctypes.c_ulong

            self.pdfium.FPDFAction_GetDest.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            self.pdfium.FPDFAction_GetDest.restype = ctypes.c_void_p

            self.pdfium.FPDFAction_GetURIPath.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
            ]
            self.pdfium.FPDFAction_GetURIPath.restype = ctypes.c_ulong

        except Exception as e:
            sys.stderr.write(f"PdfLinkExtractor init error: {e}\n")
            self.pdfium = None

    def _get_document(self, path: str) -> Optional[ctypes.c_void_p]:
        if not self.pdfium or not path or not os.path.isfile(path):
            return None
        if path in self._doc_cache:
            return self._doc_cache[path]

        try:
            doc = self.pdfium.FPDF_LoadDocument(
                path.encode("utf-8"), None
            )
            if doc:
                self._doc_cache[path] = doc
                return doc
        except Exception:
            pass
        return None

    def close_document(self, path: str) -> None:
        """Closes the cached PDF document handle and clears link caches."""
        doc = self._doc_cache.pop(path, None)
        if doc and self.pdfium:
            try:
                self.pdfium.FPDF_CloseDocument(doc)
            except Exception:
                pass
        keys_to_del = [k for k in self._page_links_cache if k[0] == path]
        for k in keys_to_del:
            self._page_links_cache.pop(k, None)

    def get_links_for_page(
        self, path: str, page_index: int
    ) -> List[Tuple[str, Tuple[float, float, float, float]]]:
        """
        Extracts both internal hyperlinks (#page=N) and external URIs for a given page.
        Returns a list of (target_str, (left, top, right, bottom)).
        Uses memory cache to avoid expensive redundant extractions.
        """
        if not self.pdfium or not path:
            return []

        cache_key = (path, page_index)
        if cache_key in self._page_links_cache:
            return self._page_links_cache[cache_key]

        doc = self._get_document(path)
        if not doc:
            return []

        links = []
        try:
            page = self.pdfium.FPDF_LoadPage(doc, page_index)
            if not page:
                return []

            annot_count = self.pdfium.FPDFPage_GetAnnotCount(page)
            for a_idx in range(annot_count):
                annot = self.pdfium.FPDFPage_GetAnnot(page, a_idx)
                if not annot:
                    continue

                link = self.pdfium.FPDFAnnot_GetLink(annot)
                rect = FS_RECTF()
                self.pdfium.FPDFAnnot_GetRect(annot, ctypes.byref(rect))

                target = None
                if link:
                    # Direct destination check
                    dest = self.pdfium.FPDFLink_GetDest(doc, link)
                    if dest:
                        dp = self.pdfium.FPDFDest_GetDestPageIndex(doc, dest)
                        if dp >= 0:
                            target = f"#page={dp + 1}"

                    # Action destination check
                    if not target:
                        action = self.pdfium.FPDFLink_GetAction(link)
                        if action:
                            dest = self.pdfium.FPDFAction_GetDest(doc, action)
                            if dest:
                                dp = self.pdfium.FPDFDest_GetDestPageIndex(
                                    doc, dest
                                )
                                if dp >= 0:
                                    target = f"#page={dp + 1}"

                            if not target:
                                buflen = self.pdfium.FPDFAction_GetURIPath(
                                    doc, action, None, 0
                                )
                                if buflen > 0:
                                    buf = ctypes.create_string_buffer(buflen)
                                    self.pdfium.FPDFAction_GetURIPath(
                                        doc, action, buf, buflen
                                    )
                                    target = buf.value.decode(
                                        "utf-8", errors="ignore"
                                    )

                if target and (rect.right > rect.left) and (rect.top != rect.bottom):
                    links.append(
                        (
                            target,
                            (
                                float(rect.left),
                                float(rect.top),
                                float(rect.right),
                                float(rect.bottom),
                            ),
                        )
                    )

                self.pdfium.FPDFPage_CloseAnnot(annot)

            self.pdfium.FPDF_ClosePage(page)
        except Exception as e:
            sys.stderr.write(f"Link extraction error on page {page_index}: {e}\n")

        self._page_links_cache[cache_key] = links
        return links
