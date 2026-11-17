"""
Page editing: delete, rotate, reorder and extract pages, and insert the pages
of another PDF.

Each edit rewrites the whole file with pikepdf into a temporary file next to
it and renames that over the original, so a failure leaves the original
untouched. The temporary file gets the original's permission bits first
(mkstemp creates it 0600), and the source is closed even when a check fails.

Encrypted PDFs are opened with the password the reader was given and saved
encrypted again (pikepdf drops the encryption unless told to keep it);
remove_password and save_unlocked_copy are the only ways it goes. Signed PDFs
are edited too: rewriting one invalidates its signatures, so the reader warns
first (document_state tells it which files are signed).

Edits return a page mapping {old index: new index or None for deleted}, which
remap_pages uses to move Riemann's own per-page data (its annotation copy) to
the pages' new positions. Annotations stored in the PDF travel with their
pages on their own.
"""

import os
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple


class PageEditError(Exception):
    pass


def _open(path: str, password: Optional[str] = None):
    """Opens path with pikepdf, trying the password only when the file asks for one (pikepdf warns otherwise)."""
    import pikepdf

    try:
        return pikepdf.open(path)
    except pikepdf.PasswordError:
        if not password:
            raise PageEditError("This PDF needs its password to be edited.") from None
    try:
        return pikepdf.open(path, password=password)
    except pikepdf.PasswordError:
        raise PageEditError("The password doesn't open this PDF.") from None


def _is_signed(pdf) -> bool:
    acro = pdf.Root.get("/AcroForm")
    try:
        return acro is not None and bool(int(acro.get("/SigFlags", 0)) & 1)
    except Exception:
        return True


def document_state(path: str, password: Optional[str] = None) -> Tuple[bool, bool]:
    """(encrypted, signed) for the PDF at path; (False, False) when it can't be read."""
    try:
        with _open(path, password) as pdf:
            return bool(pdf.is_encrypted), _is_signed(pdf)
    except Exception:
        return False, False


def _save_over(pdf, path: str, keep_encryption: bool = True) -> None:
    """Saves pdf over path through a temporary file, keeping path's permission bits (and its encryption when keep_encryption); pdf is closed either way."""
    folder = os.path.dirname(os.path.abspath(path))
    encryption = True if keep_encryption and pdf.is_encrypted else False
    try:
        fd, tmp = tempfile.mkstemp(prefix=".riemann-", suffix=".pdf", dir=folder)
        os.close(fd)
        try:
            pdf.save(tmp, encryption=encryption)
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


def page_count(path: str, password: Optional[str] = None) -> int:
    with _open(path, password) as pdf:
        return len(pdf.pages)


def delete_pages(path: str, indices: List[int], password: Optional[str] = None) -> Dict[int, Optional[int]]:
    with _open(path, password) as pdf:
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


def rotate_pages(path: str, indices: List[int], degrees: int, password: Optional[str] = None) -> Dict[int, Optional[int]]:
    if degrees % 90:
        raise PageEditError("Pages turn in steps of 90 degrees.")
    with _open(path, password) as pdf:
        count = len(pdf.pages)
        for i in _check(indices, count):
            pdf.pages[i].rotate(degrees, relative=True)
        _save_over(pdf, path)
    return {i: i for i in range(count)}


def reorder_pages(path: str, order: List[int], password: Optional[str] = None) -> Dict[int, Optional[int]]:
    """Puts the pages in the given order (a permutation of the old indices)."""
    with _open(path, password) as pdf:
        count = len(pdf.pages)
        if sorted(order) != list(range(count)):
            raise PageEditError("The new order must list every page once.")
        pdf.pages[:] = [pdf.pages[i] for i in order]
        _save_over(pdf, path)
    return {old: pos for pos, old in enumerate(order)}


def move_page(path: str, index: int, to: int, password: Optional[str] = None) -> Dict[int, Optional[int]]:
    count = page_count(path, password)
    order = list(range(count))
    _check([index, to], count)
    order.insert(to, order.pop(index))
    return reorder_pages(path, order, password)


def extract_pages(path: str, indices: List[int], dest: str, password: Optional[str] = None) -> None:
    """Writes the chosen pages to a new PDF, without a password; the original is not changed."""
    import pikepdf

    with _open(path, password) as pdf:
        pages = _check(indices, len(pdf.pages))
        out = pikepdf.Pdf.new()
        for i in pages:
            out.pages.append(pdf.pages[i])
        out.save(dest)


def insert_pdf(path: str, other: str, at: int, password: Optional[str] = None) -> Dict[int, Optional[int]]:
    """Inserts every page of other before page at (at == page count appends)."""
    with _open(path, password) as pdf:
        count = len(pdf.pages)
        if not 0 <= at <= count:
            raise PageEditError("Page out of range.")
        with _open(other) as src:
            added = len(src.pages)
            for offset, page in enumerate(src.pages):
                pdf.pages.insert(at + offset, page)
            _save_over(pdf, path)
    return {old: (old if old < at else old + added) for old in range(count)}


def remove_password(path: str, password: Optional[str] = None) -> Dict[int, Optional[int]]:
    """Rewrites path without encryption; the pages stay where they are."""
    with _open(path, password) as pdf:
        count = len(pdf.pages)
        _save_over(pdf, path, keep_encryption=False)
    return {i: i for i in range(count)}


def save_unlocked_copy(path: str, dest: str, password: Optional[str] = None) -> None:
    """Writes a copy of path without encryption to dest; path is not changed."""
    if os.path.exists(dest) and os.path.samefile(path, dest):
        raise PageEditError("Choose a new name for the unlocked copy.")
    with _open(path, password) as pdf:
        pdf.save(dest, encryption=False)


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
