"""
Page editing in the reader: rotate, delete, move and extract pages, and
insert another PDF (plus OCR of a page range, from mixins/ai.py), from the
Pages toolbar menu or the thumbnails' context
menu (acting on the selected thumbnails, or the current page).

Edits change a scratch copy, never the file on disk, until the user saves.
The first edit writes pending annotations into the PDF, records the
annotations as they are (so Discard can put them back), and copies the file
to a private folder in the cache (edit_scratch). From then on pdf_path() is
the scratch copy: the reader loads and renders it, and annotations, form
entries and further edits are written into it, while current_path stays the
document's identity for notes, the session, history and the library. Each
edit rewrites the scratch copy (riemann.core.page_edit), renumbers Riemann's
own annotation copy with the page mapping the edit returns, and reopens it at
the page the reader was on.

Encrypted files are edited with the password the reader opened them with
(document_password) and stay encrypted. Rewriting a signed file invalidates
its signatures, so the first edit of a signed document asks first
(_confirm_signed_edit); the answer holds until another file is loaded.

Save writes the scratch copy over the original in one rename, keeping its
permissions, after asking if the original changed on disk meanwhile.
Discard deletes the scratch copy, restores the annotations and reopens the
original. Closing a tab with edits waiting asks which (resolve_page_edits);
so does loading another file into the tab.
"""

import copy
import os
import re
import shutil
import tempfile
from typing import List, Optional

from PySide6.QtCore import QStandardPaths, Qt, QTimer
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


SCRATCH_FOLDER = "page-edits"


def scratch_root() -> str:
    return os.path.join(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation), SCRATCH_FOLDER)


class PagesMixin(ReaderHostBase):
    edit_scratch: Optional[str] = None
    _edit_snapshot = None

    def pdf_path(self) -> str:
        """The file the reader shows and writes: the scratch copy while page edits wait for Save, the converted PDF of an Office file."""
        return self.edit_scratch or self.converted_pdf or self.current_path or ""

    def has_unsaved_page_edits(self) -> bool:
        return bool(self.edit_scratch)

    def _start_scratch(self) -> str:
        self.flush_annotations_to_pdf()
        os.makedirs(scratch_root(), exist_ok=True)
        folder = tempfile.mkdtemp(dir=scratch_root())
        scratch = os.path.join(folder, os.path.basename(self.current_path))
        shutil.copy2(self.current_path, scratch)
        self._edit_snapshot = (
            copy.deepcopy(self.annotations),
            copy.deepcopy(self._anno_written),
            self._anno_file_sig,
        )
        self.edit_scratch = scratch
        return scratch

    def _drop_scratch(self) -> None:
        scratch, self.edit_scratch, self._edit_snapshot = self.edit_scratch, None, None
        if scratch:
            shutil.rmtree(os.path.dirname(scratch), ignore_errors=True)

    def _reopen_at(self, page: int) -> None:
        self.load_document(self.current_path, self.document_password)
        QTimer.singleShot(300, self, lambda: self.jump_to_page(page))

    def _release_document(self) -> None:
        self._stop_render_worker()
        self._replace_page_renderer(None)
        self.current_doc = None

    def save_page_edits(self) -> bool:
        """Writes the edited scratch copy over the original; True when it was saved."""
        if not self.edit_scratch:
            return True
        path = self.current_path
        try:
            changed = self._loaded_mtime is not None and os.path.getmtime(path) != self._loaded_mtime
        except OSError:
            changed = False
        if changed:
            answer = QMessageBox.question(
                self,
                "Save page changes",
                f"{os.path.basename(path)} was changed by another app since it was opened. Replace it with the edited pages?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.flush_annotations_to_pdf()
        try:
            self.flush_forms_to_pdf()
        except Exception as e:
            print(f"[Warning] Could not store form entries in the PDF: {e}")
        current = self.current_page_index
        self._release_document()
        try:
            page_edit.replace_with(path, self.edit_scratch)
        except OSError as e:
            QMessageBox.warning(self, "Save page changes", str(e) or type(e).__name__)
            self._reopen_at(current)
            return False
        self._drop_scratch()
        self._anno_file_sig = pdf_annotations.file_signature(path)
        try:
            self._loaded_mtime = os.path.getmtime(path)
        except OSError:
            self._loaded_mtime = None
        self.invalidate_page_cache()
        self._reopen_at(current)
        self.show_toast(f"Saved the page changes to {os.path.basename(path)}.")
        return True

    def discard_page_edits(self) -> None:
        """Forgets the edits: the scratch copy goes, the annotations are put back, the original is shown."""
        if not self.edit_scratch:
            return
        annotations, written, sig = self._edit_snapshot or (self.annotations, self._anno_written, self._anno_file_sig)
        timer = self._pdf_store_timer
        if timer is not None:
            timer.stop()
        self._release_document()
        self._drop_scratch()
        self.annotations = annotations
        self._anno_written = written
        self._anno_file_sig = sig
        self._write_annotation_json()
        self.invalidate_page_cache()

    def resolve_page_edits(self) -> bool:
        """Asks whether to save edits that wait for Save; False when the user cancels."""
        if not self.edit_scratch:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved page changes")
        box.setText(f"Save the page changes to {os.path.basename(self.current_path)}?")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is save:
            return self.save_page_edits()
        if box.clickedButton() is discard:
            self.discard_page_edits()
            return True
        return False

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

    def _confirm_signed_edit(self) -> bool:
        """Warns once per document that rewriting a signed PDF breaks its signatures; True to go ahead."""
        if not self.document_signed or self._signed_edit_accepted:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Signed PDF")
        box.setText(f"{os.path.basename(self.current_path)} is digitally signed.")
        box.setInformativeText(
            "Editing it in any way rewrites the file, and its signatures will no longer be valid. "
            "This isn't advisable. Annotations saved into a copy are added after the signed "
            "content and leave the signatures intact, though a certified PDF that forbids "
            "changes will still report them."
        )
        go = box.addButton("Edit anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return False
        self._signed_edit_accepted = True
        return True

    def _edit_pages(self, operation, *args, done: str = "") -> bool:
        if not self._editable() or not self._confirm_signed_edit():
            return False
        current = self.current_page_index
        try:
            path = self.edit_scratch or self._start_scratch()
        except OSError as e:
            QMessageBox.warning(self, "Edit pages", str(e) or type(e).__name__)
            return False
        self.flush_annotations_to_pdf()
        self._release_document()
        try:
            mapping = operation(path, *args, password=self.document_password)
        except Exception as e:
            QMessageBox.warning(self, "Edit pages", str(e) or type(e).__name__)
            self._reopen_at(current)
            return False
        self.annotations = page_edit.remap_pages(self.annotations, mapping)
        if self._anno_written is not None:
            self._anno_written = page_edit.remap_pages(self._anno_written, mapping)
        self._anno_file_sig = pdf_annotations.file_signature(path)
        self._write_annotation_json()
        self.invalidate_page_cache()
        target = mapping.get(current)
        if target is None:
            target = min((v for v in mapping.values() if v is not None), key=lambda v: abs(v - current), default=0)
        self._reopen_at(target)
        if done:
            self.show_toast(f"{done} Save to keep it in the file.")
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
            page_edit.extract_pages(self.pdf_path(), chosen, dest, password=self.document_password)
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
