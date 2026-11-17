"""
Page editing: delete, rotate, reorder and extract pages, and insert the pages
of another PDF.

Each edit rewrites the whole file with pikepdf into a temporary file next to
it and renames that over the original, so a failure leaves the original
untouched. The temporary file gets the original's permission bits first
(mkstemp creates it 0600), and the source is closed even when a check fails. Signed and encrypted PDFs are refused: rewriting a signed file
would invalidate its signatures.

Edits return a page mapping {old index: new index or None for deleted}, which
remap_pages uses to move Riemann's own per-page data (its annotation copy) to
the pages' new positions. Annotations stored in the PDF travel with their
pages on their own.
"""

import os
import shutil
import tempfile
from typing import Dict, List, Optional


class PageEditError(Exception):
    pass


def _open(path: str):
    import pikepdf

    pdf = pikepdf.open(path)
    if pdf.is_encrypted:
        pdf.close()
        raise PageEditError("Encrypted PDFs cannot be edited.")
    acro = pdf.Root.get("/AcroForm")
    try:
        signed = acro is not None and int(acro.get("/SigFlags", 0)) & 1
    except Exception:
        signed = True
    if signed:
        pdf.close()
        raise PageEditError("This PDF is signed; editing its pages would invalidate the signatures.")
    return pdf


def _save_over(pdf, path: str) -> None:
    """Saves pdf over path through a temporary file, keeping path's permission bits; pdf is closed either way."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp = tempfile.mkstemp(prefix=".riemann-", suffix=".pdf", dir=folder)
        os.close(fd)
        try:
            pdf.save(tmp)
            pdf.close()
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    finally:
        pdf.close()


def replace_with(path: str, source: str) -> None:
    """Replaces path with a copy of source, atomically, keeping path's permission bits."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".riemann-", suffix=".pdf", dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _check(indices: List[int], count: int) -> List[int]:
    clean = sorted({int(i) for i in indices})
    if not clean or clean[0] < 0 or clean[-1] >= count:
        raise PageEditError("Page out of range.")
    return clean


def page_count(path: str) -> int:
    import pikepdf

    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def delete_pages(path: str, indices: List[int]) -> Dict[int, Optional[int]]:
    with _open(path) as pdf:
        count = len(pdf.pages)
        doomed = _check(indices, count)
        if len(doomed) == count:
            raise PageEditError("A PDF needs at least one page.")
        for i in reversed(doomed):
            del pdf.pages[i]
        _save_over(pdf, path)
    mapping, new = {}, 0
    for old in range(count):
        if old in doomed:
            mapping[old] = None
        else:
            mapping[old] = new
            new += 1
    return mapping


def rotate_pages(path: str, indices: List[int], degrees: int) -> Dict[int, Optional[int]]:
    if degrees % 90:
        raise PageEditError("Pages turn in steps of 90 degrees.")
    with _open(path) as pdf:
        count = len(pdf.pages)
        for i in _check(indices, count):
            pdf.pages[i].rotate(degrees, relative=True)
        _save_over(pdf, path)
    return {i: i for i in range(count)}


def reorder_pages(path: str, order: List[int]) -> Dict[int, Optional[int]]:
    """Puts the pages in the given order (a permutation of the old indices)."""
    with _open(path) as pdf:
        count = len(pdf.pages)
        if sorted(order) != list(range(count)):
            raise PageEditError("The new order must list every page once.")
        pdf.pages[:] = [pdf.pages[i] for i in order]
        _save_over(pdf, path)
    return {old: pos for pos, old in enumerate(order)}


def move_page(path: str, index: int, to: int) -> Dict[int, Optional[int]]:
    count = page_count(path)
    order = list(range(count))
    _check([index, to], count)
    order.insert(to, order.pop(index))
    return reorder_pages(path, order)


def extract_pages(path: str, indices: List[int], dest: str) -> None:
    """Writes the chosen pages to a new PDF; the original is not changed."""
    import pikepdf

    with pikepdf.open(path) as pdf:
        pages = _check(indices, len(pdf.pages))
        out = pikepdf.Pdf.new()
        for i in pages:
            out.pages.append(pdf.pages[i])
        out.save(dest)


def insert_pdf(path: str, other: str, at: int) -> Dict[int, Optional[int]]:
    """Inserts every page of other before page at (at == page count appends)."""
    import pikepdf

    with _open(path) as pdf:
        count = len(pdf.pages)
        if not 0 <= at <= count:
            raise PageEditError("Page out of range.")
        with pikepdf.open(other) as src:
            added = len(src.pages)
            for offset, page in enumerate(src.pages):
                pdf.pages.insert(at + offset, page)
            _save_over(pdf, path)
    return {old: (old if old < at else old + added) for old in range(count)}


def remap_pages(pages: Dict[str, list], mapping: Dict[int, Optional[int]]) -> Dict[str, list]:
    """Moves per-page data ({"3": [...]}) to new page numbers, dropping deleted pages."""
    out: Dict[str, list] = {}
    for key, items in (pages or {}).items():
        try:
            new = mapping.get(int(key), int(key))
        except ValueError:
            continue
        if new is not None and items:
            out.setdefault(str(new), []).extend(items)
    return out
