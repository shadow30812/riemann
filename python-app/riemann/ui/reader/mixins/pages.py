"""
Page editing in the reader: rotate, delete, move and extract pages, and
insert another PDF (plus OCR of a page range, from mixins/ai.py), from the
Pages toolbar menu or the thumbnails' context
menu (acting on the selected thumbnails, or the current page).

An edit first writes pending annotations into the PDF, lets go of the open
document, rewrites the file (riemann.core.page_edit), renumbers Riemann's own
annotation copy with the page mapping the edit returns, and reopens the file
at the page the reader was on. The new file signature is recorded as Riemann's
own, so the reload is not mistaken for a change made by another app.
"""

import os
import re
from typing import List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu, QMessageBox

from ....core import page_edit, pdf_annotations
from ....core.settings import get_dialog_directory, save_last_directory
from .host import ReaderHostBase


def parse_page_ranges(text: str, count: int) -> List[int]:
    """'1-3, 5' -> [0, 1, 2, 4] (1-based ranges in, 0-based pages out)."""
    pages = set()
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"Not a page or range: {part}")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a > b:
            a, b = b, a
        if a < 1 or b > count:
            raise ValueError(f"Pages run from 1 to {count}.")
        pages.update(range(a - 1, b))
    return sorted(pages)


def format_page_ranges(pages: List[int]) -> str:
    out, run = [], []
    for p in sorted(set(pages)):
        if run and p == run[-1] + 1:
            run.append(p)
            continue
        if run:
            out.append(f"{run[0] + 1}-{run[-1] + 1}" if len(run) > 1 else f"{run[0] + 1}")
        run = [p]
    if run:
        out.append(f"{run[0] + 1}-{run[-1] + 1}" if len(run) > 1 else f"{run[0] + 1}")
    return ", ".join(out)


class PagesMixin(ReaderHostBase):
    def _target_pages(self) -> List[int]:
        thumbs = getattr(self.sidebar_nav, "thumbs", None)
        if thumbs is not None and thumbs.isVisible():
            chosen = [int(i.data(Qt.ItemDataRole.UserRole)) for i in thumbs.selectedItems()]
            if chosen:
                return sorted(chosen)
        return [self.current_page_index]

    def build_pages_menu(self, menu: QMenu, pages: List[int]) -> None:
        label = format_page_ranges(pages)
        noun = "page" if len(pages) == 1 else "pages"
        menu.addAction(f"Rotate {noun} {label} clockwise", lambda: self.rotate_pages(pages, 90))
        menu.addAction(f"Rotate {noun} {label} counter-clockwise", lambda: self.rotate_pages(pages, -90))
        menu.addSeparator()
        if len(pages) == 1:
            menu.addAction(f"Move page {label} to…", lambda: self.move_page_to(pages[0]))
        menu.addAction(f"Extract {noun} {label} to a new PDF…", lambda: self.extract_pages(pages))
        menu.addAction(f"Insert a PDF after page {pages[-1] + 1}…", lambda: self.insert_pdf_after(pages[-1]))
        menu.addAction(f"Recognise text in {noun} {label}…", lambda: self.perform_ocr_pages(pages))
        menu.addSeparator()
        menu.addAction(f"Delete {noun} {label}", lambda: self.delete_pages(pages))

    def show_pages_menu(self, global_pos=None) -> None:
        if not self.current_doc:
            return
        menu = QMenu(self)
        self.build_pages_menu(menu, self._target_pages())
        button = self.btn_pages
        if global_pos is None:
            global_pos = self.menu_anchor(button)
        menu.exec(global_pos)

    def _editable(self) -> bool:
        path = self.current_path or ""
        if not path.lower().endswith(".pdf") or not self.current_doc:
            return False
        if not os.access(path, os.W_OK):
            QMessageBox.warning(self, "Edit pages", "This file is read-only.")
            return False
        return True

    def _edit_pages(self, operation, *args, done: str = "") -> bool:
        if not self._editable():
            return False
        path = self.current_path
        self.flush_annotations_to_pdf()
        current = self.current_page_index
        self._stop_render_worker()
        self._replace_page_renderer(None)
        self.current_doc = None
        try:
            mapping = operation(path, *args)
        except Exception as e:
            QMessageBox.warning(self, "Edit pages", str(e) or type(e).__name__)
            self.load_document(path, restore_state=False)
            QTimer.singleShot(300, lambda: self.jump_to_page(current))
            return False
        self.annotations = page_edit.remap_pages(self.annotations, mapping)
        if self._anno_written is not None:
            self._anno_written = page_edit.remap_pages(self._anno_written, mapping)
        self._anno_file_sig = pdf_annotations.file_signature(path)
        self._write_annotation_json()
        self.invalidate_page_cache()
        try:
            self._loaded_mtime = os.path.getmtime(path)
        except OSError:
            self._loaded_mtime = None
        target = mapping.get(current)
        if target is None:
            target = min((v for v in mapping.values() if v is not None), key=lambda v: abs(v - current), default=0)
        self.load_document(path, restore_state=False)
        QTimer.singleShot(300, lambda: self.jump_to_page(target))
        if done:
            self.show_toast(done)
        return True

    def rotate_pages(self, pages: List[int], degrees: int) -> None:
        self._edit_pages(page_edit.rotate_pages, pages, degrees, done="Rotated.")

    def delete_pages(self, pages: List[int]) -> None:
        noun = "page" if len(pages) == 1 else "pages"
        answer = QMessageBox.question(
            self,
            "Delete pages",
            f"Delete {noun} {format_page_ranges(pages)} from {os.path.basename(self.current_path)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._edit_pages(page_edit.delete_pages, pages, done=f"Deleted {noun} {format_page_ranges(pages)}.")

    def move_page_to(self, page: int) -> None:
        count = self.current_doc.page_count if self.current_doc else 0
        to, ok = QInputDialog.getInt(self, "Move page", f"Move page {page + 1} to position:", page + 1, 1, count)
        if ok and to - 1 != page:
            self._edit_pages(page_edit.move_page, page, to - 1, done=f"Moved page {page + 1} to {to}.")

    def extract_pages(self, pages: List[int]) -> None:
        count = self.current_doc.page_count if self.current_doc else 0
        text, ok = QInputDialog.getText(self, "Extract pages", "Pages (for example 1-3, 5):", text=format_page_ranges(pages))
        if not ok:
            return
        try:
            chosen = parse_page_ranges(text, count)
        except ValueError as e:
            QMessageBox.warning(self, "Extract pages", str(e))
            return
        stem = os.path.splitext(os.path.basename(self.current_path))[0]
        start = os.path.join(get_dialog_directory(self.settings), f"{stem} (pages {format_page_ranges(chosen)}).pdf")
        dest, _ = QFileDialog.getSaveFileName(self, "Save extracted pages", start, "PDF Files (*.pdf)")
        if not dest:
            return
        save_last_directory(self.settings, dest)
        try:
            page_edit.extract_pages(self.current_path, chosen, dest)
        except Exception as e:
            QMessageBox.warning(self, "Extract pages", str(e))
            return
        self.show_toast(f"Saved {len(chosen)} pages to {os.path.basename(dest)}")

    def insert_pdf_after(self, page: int) -> None:
        other, _ = QFileDialog.getOpenFileName(
            self, "Insert a PDF", get_dialog_directory(self.settings), "PDF Files (*.pdf)"
        )
        if other:
            self._edit_pages(page_edit.insert_pdf, other, page + 1, done=f"Inserted {os.path.basename(other)}.")
