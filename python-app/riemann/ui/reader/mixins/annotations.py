"""
Annotations Mixin.

Handles annotation tools (pen, note), undo/redo stacks, and file persistence.

Annotations are saved twice: right away into Riemann's JSON copy (located by
riemann.core.annotation_store) and, 1.2 s after the last change, into the PDF
on a worker thread. The JSON also records the file signature and the
annotations as last written into the PDF ("written"). When the PDF changes
outside Riemann, or a write is refused because the file changed under it, the
file's annotations are read back and merged with Riemann's by annotation ID
against that record, so neither side's edits are lost. A file that has piled
up many incremental saves is compacted when its tab closes.

Redaction marks ("redact") are annotations until applied. apply_redactions
hands them to core/redaction.py as a page edit, so the removal lands in the
scratch copy and reaches the file on Save; afterwards the marks, and every
annotation that touched them, leave the in-memory list too. Replace-text
overlays ("replace") are exempt, since they sit over their own mark.

The worker threads that read and write PDFs are not daemon threads: Python
kills daemon threads at exit even inside pikepdf or PDFium, which aborts the
process and could cut an appended save short.

Notes:

_on_pdf_annotations_written: Riemann's own write is not a change "on disk" for
the reload prompt. Signed, encrypted or read-only files keep their annotations
in Riemann only; changing them would break signatures or fail.

work: Pypdf is pure Python and held the GIL for about two seconds on the PDF
spec, which starved the UI thread; the core does not. The tab was closed before
parsing finished.
"""

import json
import os
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ....core import annotation_store, pdf_annotations, redaction
from ....core.doctypes import document_kind
from ....core.storage import write_json_atomic
from ..widgets import PageWidget
from ....core.settings import app_settings
from ...palette import CONTENT, app_is_dark, tokens
from ..replace_text import ReplaceTextDialog, overlay_from_selection, ring_colour
from .host import ReaderHostBase
from .pages import format_page_ranges


class _CommentsBridge(QObject):
    """Carries comments parsed on a worker thread back to the UI thread."""

    loaded = Signal(str, object)


class _PdfStoreBridge(QObject):
    """Carries results of reading or writing annotations in the PDF back to the UI thread."""

    read_done = Signal(str, object, object)
    write_done = Signal(str, object, str)


def annotation_touches(anno: Dict[str, Any], rects: List[List[float]]) -> bool:
    """Whether a Riemann annotation (relative page coordinates) touches any of rects."""
    boxes = [(min(l, r), min(t, b), max(l, r), max(t, b)) for l, t, r, b in rects]

    def hit(x1: float, y1: float, x2: float, y2: float) -> bool:
        return any(x1 <= bx2 and bx1 <= x2 and y1 <= by2 and by1 <= y2 for bx1, by1, bx2, by2 in boxes)

    points = []
    if "rel_pos" in anno:
        points.append(anno["rel_pos"])
    points += anno.get("points", []) or []
    for l, t, r, b in anno.get("rects", []) or []:
        if hit(min(l, r), min(t, b), max(l, r), max(t, b)):
            return True
    if points:
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
        return hit(min(xs), min(ys), max(xs), max(ys))
    return False


def compact_if_needed(path: str) -> None:
    """Compacts a PDF that has collected many incremental annotation saves."""
    if pdf_annotations.incremental_updates(path) >= pdf_annotations.COMPACT_AFTER_UPDATES:
        pdf_annotations.compact(path)


def store_in_pdf_enabled() -> bool:
    return app_settings().value("annotations/store_in_pdf", True, type=bool)


def _format_pdf_date(value: str) -> str:
    """Turns a PDF date (D:YYYYMMDDHHmmSS...) into 'YYYY-MM-DD HH:MM:SS'."""
    date_str = value.strip()
    if date_str.startswith("D:"):
        raw = date_str[2:16]
        if len(raw) >= 8:
            date_str = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
            if len(raw) >= 14:
                date_str += f" {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
    return date_str


def _relative_rect(rect, origin_x: float, origin_y: float, cw: float, ch: float):
    """Maps a PDF rectangle (any corner order) to 0..1 page coordinates, top-left origin."""
    if not rect:
        return (0.0, 0.0, 0.0, 0.0)
    x1 = (min(rect[0], rect[2]) - origin_x) / cw
    x2 = (max(rect[0], rect[2]) - origin_x) / cw
    y1 = 1.0 - ((max(rect[1], rect[3]) - origin_y) / ch)
    y2 = 1.0 - ((min(rect[1], rect[3]) - origin_y) / ch)
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _display_space(page) -> "pdf_annotations._PageSpace":
    """The crop box (clipped to the media box) and /Rotate of a pypdf page, as the renderer shows it."""
    def box(b):
        return (float(b.left), float(b.bottom), float(b.right), float(b.top))

    try:
        rotate = int(page.rotation or 0)
    except Exception:
        rotate = 0
    return pdf_annotations._PageSpace(pdf_annotations.visible_box(box(page.cropbox), box(page.mediabox)), rotate)


def _relative_display_rect(rect, space) -> tuple:
    """Maps a PDF rectangle to 0..1 coordinates on the page as displayed, top-left origin."""
    if not rect:
        return (0.0, 0.0, 0.0, 0.0)
    u1, v1 = space.from_pdf(rect[0], rect[1])
    u2, v2 = space.from_pdf(rect[2], rect[3])
    return (min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2))


def read_comments_from_core(doc) -> Dict[int, List[Dict[str, Any]]]:
    """
    Same result as read_external_comments, read through the Rust core. Each
    call releases the GIL and the PDFium lock between pages, so it can run on a
    worker thread without starving the UI thread or the page renderer.
    """
    comments: Dict[int, List[Dict[str, Any]]] = {}
    for page_idx in range(doc.page_count):
        try:
            crop, items = doc.get_comments(page_idx)
        except Exception:
            continue
        if not items:
            continue
        left, bottom, right, top = crop
        cw = (right - left) if right - left > 0 else 1.0
        ch = (top - bottom) if top - bottom > 0 else 1.0
        comments[page_idx] = [
            {
                "page": page_idx,
                "subtype": subtype,
                "contents": contents,
                "author": author,
                "date": _format_pdf_date(date),
                "rel_rect": _relative_rect(rect, left, bottom, cw, ch),
            }
            for subtype, contents, author, date, rect in items
        ]
    return comments


def read_external_comments(path: str) -> Dict[int, List[Dict[str, Any]]]:
    """
    Extracts native PDF comments created by other applications (Acrobat, Preview,
    Okular), keyed by page index. Pure pypdf parsing with no Qt calls, so it is
    safe to run off the UI thread.
    """
    comments: Dict[int, List[Dict[str, Any]]] = {}
    if not path or not os.path.isfile(path) or document_kind(path) != "pdf":
        return comments

    try:
        import pypdf

        reader = pypdf.PdfReader(path)
        for page_idx, page in enumerate(reader.pages):
            if "/Annots" not in page:
                continue
            annots = page["/Annots"]
            space = _display_space(page)

            page_comments = []
            for annot_ref in annots:
                try:
                    obj = annot_ref.get_object()
                    if not obj:
                        continue
                    subtype = str(obj.get("/Subtype", "")).lstrip("/")
                    if subtype in ("Link", "Widget"):
                        continue
                    if str(obj.get("/NM", "")).startswith(pdf_annotations.NAME_PREFIX):
                        continue

                    contents = ""
                    if "/Contents" in obj and obj["/Contents"]:
                        contents = str(obj["/Contents"]).strip()
                    elif "/Popup" in obj:
                        popup = obj["/Popup"].get_object()
                        if popup and "/Contents" in popup and popup["/Contents"]:
                            contents = str(popup["/Contents"]).strip()

                    if not contents:
                        continue

                    author = str(obj.get("/T", "") or "").strip()
                    if not author and "/Popup" in obj:
                        popup = obj["/Popup"].get_object()
                        if popup:
                            author = str(popup.get("/T", "") or "").strip()

                    date_str = _format_pdf_date(
                        str(obj.get("/M", "") or obj.get("/CreationDate", "") or "")
                    )
                    rect = obj.get("/Rect", [])
                    rel_rect = _relative_display_rect([float(v) for v in rect] if len(rect) == 4 else None, space)

                    page_comments.append(
                        {
                            "page": page_idx,
                            "subtype": subtype,
                            "contents": contents,
                            "author": author,
                            "date": date_str,
                            "rel_rect": rel_rect,
                        }
                    )
                except Exception:
                    continue
            if page_comments:
                comments[page_idx] = page_comments
    except Exception as e:
        print(f"[Warning] Failed to load external PDF comments: {e}")
    return comments



class CommentViewDialog(QDialog):
    """Clean popup dialog displaying external PDF comments from Acrobat, Preview, etc."""

    def __init__(self, comment: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("PDF Comment")
        self.setMinimumSize(380, 240)
        self.resize(440, 280)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        author = comment.get("author") or "Unknown Author"
        date = comment.get("date") or ""
        subtype = comment.get("subtype") or "Comment"
        page_num = comment.get("page", 0) + 1

        hdr_layout = QHBoxLayout()
        icon_lbl = QLabel("💬")
        icon_lbl.setStyleSheet("font-size: 22px;")
        hdr_layout.addWidget(icon_lbl)

        meta_layout = QVBoxLayout()
        lbl_author = QLabel(f"<b>{author}</b> ({subtype})")
        lbl_author.setStyleSheet("font-size: 13px;")
        meta_layout.addWidget(lbl_author)

        info_text = f"Page {page_num}"
        if date:
            info_text += f" • {date}"
        lbl_date = QLabel(info_text)
        t = tokens(app_is_dark())
        lbl_date.setStyleSheet(f"color: {t['muted']}; font-size: 11px;")
        meta_layout.addWidget(lbl_date)

        hdr_layout.addLayout(meta_layout)
        hdr_layout.addStretch()
        layout.addLayout(hdr_layout)

        txt_edit = QTextEdit()
        txt_edit.setPlainText(comment.get("contents", ""))
        txt_edit.setReadOnly(True)
        txt_edit.setStyleSheet(
            f"font-size: 13px; line-height: 1.4; border: 1px solid {t['border_strong']}; border-radius: 6px; padding: 6px;"
        )
        layout.addWidget(txt_edit)

        btn_box = QHBoxLayout()
        btn_copy = QPushButton("Copy Text")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(comment.get("contents", "")))
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_close.setDefault(True)

        btn_box.addWidget(btn_copy)
        btn_box.addStretch()
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)


class AnnotationsMixin(ReaderHostBase):
    """
    Provides methods for managing user annotations on PDF documents.
    This mixin is intended to be integrated into the main reader component,
    managing state for active tools, persistence, and undo/redo stacks.
    """
    _anno_file_sig = None
    _anno_json_cache = None
    _anno_written = None
    _comments_bridge = None
    _pdf_store_again = False
    _pdf_store_blocked = False
    _pdf_store_busy = False
    _pdf_store_snapshot = None
    _pdf_store_timer = None
    _store_bridge = None

    def _get_annotation_path(self) -> str:
        """Riemann's JSON copy of the current document's annotations ('' with no document)."""
        if not self.current_path:
            return ""
        cached = self._anno_json_cache
        if cached and cached[0] == self.current_path:
            return cached[1]
        path = annotation_store.shared().json_path(self.current_path)
        self._anno_json_cache = (self.current_path, path)
        return path

    def load_annotations(self) -> None:
        """
        Loads the document's annotations. Riemann's JSON copy is read first;
        the copy stored inside the PDF is then checked on a worker thread and
        wins if another application changed the file since Riemann last
        wrote it (so edits made there come back).
        """
        if not self.current_path:
            return
        self.annotations = {}
        if self.converted_pdf:
            return
        self._anno_file_sig = None
        self._anno_written = None
        self._pdf_store_blocked = False
        timer = self._pdf_store_timer
        if timer is not None:
            timer.stop()
        p = self._get_annotation_path()
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict) and "pages" in data:
                self.annotations = data.get("pages") or {}
                self._anno_file_sig = data.get("pdf")
                self._anno_written = data.get("written")
            elif isinstance(data, dict):
                self.annotations = data
        for annos in self.annotations.values():
            for anno in annos:
                anno.setdefault("id", pdf_annotations.new_annotation_id())

        if self.current_path.lower().endswith(".pdf") and store_in_pdf_enabled():
            self._check_pdf_annotations_async(self.pdf_path())

    def _pdf_store_bridge(self) -> "_PdfStoreBridge":
        bridge = self._store_bridge
        if bridge is None:
            bridge = _PdfStoreBridge(self if isinstance(self, QObject) else None)
            bridge.read_done.connect(self._on_pdf_annotations_read)
            bridge.write_done.connect(self._on_pdf_annotations_written)
            self._store_bridge = bridge
        return bridge

    def _check_pdf_annotations_async(self, path: str) -> None:
        current_sig = pdf_annotations.file_signature(path)
        if current_sig is not None and current_sig == self._anno_file_sig:
            return
        bridge = self._pdf_store_bridge()

        def work():
            try:
                stored = pdf_annotations.read_annotations(path)
            except Exception as e:
                print(f"[Warning] Could not read annotations stored in {path}: {e}")
                stored = None
            try:
                bridge.read_done.emit(path, stored, current_sig)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="riemann-anno-read").start()

    def _on_pdf_annotations_read(self, path: str, stored, sig) -> None:
        if path != self.pdf_path():
            return
        if stored is not None:
            before = self._annotation_snapshot()
            merged = pdf_annotations.merge_annotations(before, stored, self._anno_written)
            self.annotations = merged
            self._anno_file_sig = sig
            self._anno_written = pdf_annotations.written_form(stored)
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._write_annotation_json()
            for pid in set(before) | set(merged) | set(stored):
                if before.get(pid) != merged.get(pid):
                    self.refresh_page_render(int(pid))
            if pdf_annotations.written_form(merged) != self._anno_written:
                self.save_annotations()
        elif any(self.annotations.values()):
            self.save_annotations()

    def _write_annotation_json(self) -> None:
        p = self._get_annotation_path()
        if not p:
            return
        write_json_atomic(
            p,
            {
                "pages": self.annotations,
                "pdf": self._anno_file_sig,
                "written": self._anno_written,
            },
        )

    def save_annotations(self) -> None:
        """
        Saves the annotations to Riemann's JSON copy right away and, shortly
        after the last change, into the PDF itself so other apps see them.
        """
        if not self.current_path or self.converted_pdf:
            return
        self._write_annotation_json()
        if (
            self.current_path.lower().endswith(".pdf")
            and store_in_pdf_enabled()
            and not self._pdf_store_blocked
        ):
            timer = self._pdf_store_timer
            if timer is None:
                timer = QTimer(self if isinstance(self, QObject) else None)
                timer.setSingleShot(True)
                timer.setInterval(1200)
                timer.timeout.connect(self._store_annotations_in_pdf)
                self._pdf_store_timer = timer
            timer.start()

    def _annotation_snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        return json.loads(json.dumps(self.annotations))

    def _store_annotations_in_pdf(self, blocking: bool = False) -> None:
        """Writes the annotations into the PDF on a worker thread (or inline when blocking)."""
        path = self.pdf_path()
        if not path:
            return
        if self._pdf_store_busy and not blocking:
            self._pdf_store_again = True
            return
        snapshot = self._annotation_snapshot()
        self._pdf_store_snapshot = snapshot
        bridge = self._pdf_store_bridge()
        try:
            self._pdf_store_mtime_before = os.path.getmtime(path)
        except OSError:
            self._pdf_store_mtime_before = None
        self._pdf_store_busy = True
        self._pdf_store_again = False

        def work():
            error = ""
            sig = None
            try:
                if not pdf_annotations.can_store_in(path):
                    error = "blocked"
                elif pdf_annotations.write_annotations(path, snapshot):
                    sig = pdf_annotations.file_signature(path)
                else:
                    error = "changed"
            except Exception as e:
                error = str(e) or type(e).__name__
            if blocking:
                self._on_pdf_annotations_written(path, sig, error)
                return
            try:
                bridge.write_done.emit(path, sig, error)
            except RuntimeError:
                pass

        if blocking:
            work()
        else:
            threading.Thread(target=work, name="riemann-anno-write").start()

    def _on_pdf_annotations_written(self, path: str, sig, error: str) -> None:
        self._pdf_store_busy = False
        if path != self.pdf_path():
            return
        if sig is not None:
            self._anno_file_sig = sig
            self._anno_written = pdf_annotations.written_form((self._pdf_store_snapshot or {}))
            self._write_annotation_json()
            if path == self.current_path and self._loaded_mtime == self._pdf_store_mtime_before:
                try:
                    self._loaded_mtime = os.path.getmtime(path)
                except OSError:
                    pass
        elif error == "blocked":
            self._pdf_store_blocked = True
        elif error == "changed":
            self._check_pdf_annotations_async(path)
        elif error:
            print(f"[Warning] Could not store annotations in {path}: {error}")
        if self._pdf_store_again:
            self._pdf_store_again = False
            self._store_annotations_in_pdf()

    def flush_annotations_to_pdf(self) -> None:
        """
        Writes pending annotation changes into the PDF before the tab closes,
        then compacts the file in the background if incremental saves have
        piled up.
        """
        timer = self._pdf_store_timer
        if timer is not None and timer.isActive():
            timer.stop()
            self._store_annotations_in_pdf(blocking=True)
        path = self.pdf_path()
        if path and path.lower().endswith(".pdf") and self._anno_written is not None:
            threading.Thread(target=compact_if_needed, args=(path,), name="riemann-compact").start()

    def toggle_annotation_mode(self, checked: bool) -> None:
        if checked and self.converted_pdf:
            self.show_toast("Save as PDF (Ctrl+S) to mark it up.")
            checked = False
        self.anno_toolbar.setVisible(checked)
        self.btn_annotate.setChecked(checked)
        if not checked:
            self.current_tool = "nav"
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.current_tool = "nav"
            self.anno_toolbar.btn_nav.setChecked(True)

    def set_tool(self, tool_id: str) -> None:
        """Selects an annotation tool ('nav', 'eraser', ...) and sets the cursor to match."""
        self.current_tool = tool_id
        if tool_id == "nav":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif tool_id == "eraser":
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def set_color(self, c: str) -> None:
        self.pen_color = c

    def set_thickness(self, v: int) -> None:
        """Sets the pen thickness, in pixels."""
        self.pen_thickness = v

    def undo_annotation(self) -> None:
        """
        Reverts the most recently recorded annotation action, moving it to the redo stack,
        and triggers a visual refresh.
        """
        if not self.undo_stack:
            return
        _, p_idx, _ = self.undo_stack.pop()
        pid = str(p_idx)
        if pid in self.annotations and self.annotations[pid]:
            item = self.annotations[pid].pop()
            self.redo_stack.append((pid, item))
            self.save_annotations()
            self.refresh_page_render(p_idx)

    def redo_annotation(self) -> None:
        """
        Re-applies the most recently undone annotation action from the redo stack,
        and triggers a visual refresh.
        """
        if not self.redo_stack:
            return
        pid, item = self.redo_stack.pop()
        if pid not in self.annotations:
            self.annotations[pid] = []
        self.annotations[pid].append(item)
        self.undo_stack.append(("add", int(pid), len(self.annotations[pid]) - 1))
        self.save_annotations()
        self.refresh_page_render(int(pid))

    def handle_annotation_click(self, label: PageWidget, event: QMouseEvent) -> bool:
        """Opens the annotation under the click, if there is one. Returns True if there was."""
        pid = str(label.property("pageIndex"))
        w, h = label.width(), label.height()
        rx, ry = event.pos().x() / w, event.pos().y() / h
        rx, ry = self._map_to_unrotated(rx, ry)

        for i, anno in enumerate(self.annotations.get(pid, [])):
            if anno.get("type") == "note":
                ax, ay = anno["rel_pos"]
                if ((rx - ax) ** 2 + (ry - ay) ** 2) ** 0.5 < 0.03:
                    self.show_annotation_popup(anno, int(pid), i)
                    return True
        return False

    def show_annotation_popup(self, data: Dict, p_idx: int, idx: int) -> None:
        """Edits or deletes text annotation idx on page p_idx."""
        txt, ok = QInputDialog.getText(
            self, "Edit Note", "Text (Empty to delete):", text=data.get("text", "")
        )
        if ok:
            if not txt.strip():
                del self.annotations[str(p_idx)][idx]
            else:
                self.annotations[str(p_idx)][idx]["text"] = txt
            self.save_annotations()
            self.refresh_page_render(p_idx)

    def create_new_annotation(
        self, p_idx: int, rx: float, ry: float, type: str = "note"
    ) -> None:
        """Asks for the text of a new annotation at (rx, ry), relative to the page (0 to 1)."""
        txt, ok = QInputDialog.getText(self, "Add Note", "Text:")
        if ok and txt:
            self._add_anno_data(
                p_idx,
                {
                    "type": type,
                    "rel_pos": (rx, ry),
                    "text": txt,
                    "color": self.pen_color,
                },
            )

    def _add_anno_data(self, page_idx: int, data: Dict) -> None:
        """Appends an annotation, records it for undo and saves."""
        if self.converted_pdf:
            self.show_toast("Save as PDF (Ctrl+S) to mark it up.")
            return
        pid = str(page_idx)
        if pid not in self.annotations:
            self.annotations[pid] = []
        data.setdefault("id", pdf_annotations.new_annotation_id())
        self.annotations[pid].append(data)
        self.undo_stack.append(("add", page_idx, len(self.annotations[pid]) - 1))
        self.redo_stack.clear()
        self.save_annotations()
        self.refresh_page_render(page_idx)

    def _add_redaction_mark(self, page_idx: int, rects: List[List[float]]) -> None:
        """Marks rects (relative, unrotated) on a page for redaction; nothing is removed until applied."""
        boxes = [[min(l, r), min(t, b), max(l, r), max(t, b)] for l, t, r, b in rects]
        self._add_anno_data(page_idx, {"type": "redact", "rects": boxes})

    def _selected_rects(self) -> Dict[int, List[List[float]]]:
        """The current text selection as relative, unrotated rects per page."""
        out: Dict[int, List[List[float]]] = {}
        for page_idx, widget in sorted(self.page_widgets.items()):
            rects = getattr(widget, "selected_text_rects", None) or []
            w, h = widget.width(), widget.height()
            if not rects or w <= 0 or h <= 0:
                continue
            for r in rects:
                rx1, ry1 = self._map_to_unrotated(r.left() / w, r.top() / h)
                rx2, ry2 = self._map_to_unrotated((r.right() + 1) / w, (r.bottom() + 1) / h)
                out.setdefault(page_idx, []).append([min(rx1, rx2), min(ry1, ry2), max(rx1, rx2), max(ry1, ry2)])
        return out

    def replace_selected_text(self) -> None:
        """Covers the selected text with new text in a matching box (a "replace" annotation)."""
        selected = self._selected_rects()
        if len(selected) != 1:
            self.show_toast("Select text on one page to replace it.")
            return
        page_idx, rects = next(iter(selected.items()))
        dialog = ReplaceTextDialog(self.current_selected_text or "", self)
        if not dialog.exec() or not dialog.text().strip():
            return
        page_size = self.current_doc.get_page_size(page_idx)
        background = self._background_around(page_idx, rects, page_size)
        overlay = overlay_from_selection(rects, self.current_doc.get_char_styles(page_idx), page_size, dialog.text(), background)
        self._add_anno_data(page_idx, overlay)
        if dialog.remove_original():
            self._add_redaction_mark(page_idx, rects)
        self.clear_all_text_selections()

    def _background_around(self, page_idx: int, rects: List[List[float]], page_size) -> str:
        """The page's own colour around rects, from a render without the dark theme."""
        width, height = page_size
        try:
            result = self.page_renderer.render_page(page_idx, 1.0, 0)
            pixels, pw, ph = bytes(result.data), result.width, result.height
        except Exception:
            return CONTENT["paper"]
        sx, sy = pw / max(width, 1.0), ph / max(height, 1.0)
        l = min(r[0] for r in rects) * width * sx
        t = min(r[1] for r in rects) * height * sy
        r_ = max(r[2] for r in rects) * width * sx
        b = max(r[3] for r in rects) * height * sy
        return ring_colour(pixels, pw, ph, (int(l), int(t), int(r_), int(b)))

    def mark_selection_for_redaction(self) -> None:
        """Turns the selected text on every page into redaction marks."""
        selected = self._selected_rects()
        for page_idx, rects in selected.items():
            self._add_redaction_mark(page_idx, rects)
        self.clear_all_text_selections()
        if selected:
            self.show_toast("Marked for redaction. Apply redactions to remove the text.")

    def redaction_marks(self) -> Dict[int, List[List[float]]]:
        marks: Dict[int, List[List[float]]] = {}
        for pid, annos in self.annotations.items():
            for anno in annos:
                if anno.get("type") == "redact" and anno.get("rects"):
                    marks.setdefault(int(pid), []).extend(anno["rects"])
        return marks

    def apply_redactions(self) -> None:
        """Removes everything under the redaction marks from the document, through the scratch copy (so on Save)."""
        marks = self.redaction_marks()
        if not marks:
            self.show_toast("Nothing is marked. Use the redaction tool or select text and choose Mark for Redaction.")
            return
        count = sum(len(r) for r in marks.values())
        pages = sorted(marks)
        noun = "page" if len(pages) == 1 else "pages"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Apply redactions")
        box.setText(
            f"Remove everything under {count} redaction mark{'s' if count != 1 else ''} "
            f"on {noun} {format_page_ranges(pages)}?"
        )
        box.setInformativeText(
            "Text, images and drawings under the marks are deleted from the document, not just covered, "
            "along with annotations, links and form fields that touch them. A black box is left in their place. "
            "The file changes when you save; until then, Discard brings everything back.\n\n"
            "Not removed: document metadata, bookmarks, attachments, text kept for accessibility, "
            "and the glyph shapes stored in embedded fonts."
        )
        go = box.addButton("Apply", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        keep = self._redaction_exempt_ids()
        if not self._edit_pages(redaction.apply_redactions, marks, keep, done="Redacted."):
            return
        for pid in list(self.annotations):
            rects = marks.get(int(pid))
            if not rects:
                continue
            self.annotations[pid] = [
                a for a in self.annotations[pid]
                if a.get("id") in keep or (a.get("type") != "redact" and not annotation_touches(a, rects))
            ]
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.save_annotations()

    def _redaction_exempt_ids(self) -> List[str]:
        return [
            str(a["id"]) for annos in self.annotations.values() for a in annos
            if a.get("type") == "replace" and a.get("id")
        ]

    def _handle_eraser_click(self, label: PageWidget, pos: Any, page_idx: int) -> None:
        """Deletes the annotation nearest the click, if it is within reach."""
        pid = str(page_idx)
        if pid not in self.annotations:
            return

        w, h = label.width(), label.height()
        rx, ry = pos.x() / w, pos.y() / h
        rx, ry = self._map_to_unrotated(rx, ry)

        best, min_dist = -1, 0.06

        for i, anno in enumerate(self.annotations[pid]):
            dist = 1.0
            atype = anno.get("type")

            if atype in ("note", "text", "stamp"):
                ax, ay = anno.get("rel_pos", (0, 0))
                dist = ((rx - ax) ** 2 + (ry - ay) ** 2) ** 0.5

            elif atype == "drawing":
                pts = anno.get("points", [])
                if pts:
                    if anno.get("subtype") in ("rect", "oval") and len(pts) == 2:
                        x1, y1 = pts[0]
                        x2, y2 = pts[1]
                        l, r = min(x1, x2), max(x1, x2)
                        top, b = min(y1, y2), max(y1, y2)
                        if ((l - 0.05) <= rx <= (r + 0.05)) and (
                            (top - 0.05) <= ry <= (b + 0.05)
                        ):
                            dist = 0.0
                    else:
                        dist = min(
                            [((rx - px) ** 2 + (ry - py) ** 2) ** 0.5 for px, py in pts]
                        )

            elif atype in ("markup", "redact", "replace"):
                rects = anno.get("rects", [])
                if rects:
                    for l, t, r, b in rects:
                        left, right = min(l, r), max(l, r)
                        top, bottom = min(t, b), max(t, b)
                        if (left - 0.05) <= rx <= (right + 0.05) and (
                            top - 0.05
                        ) <= ry <= (bottom + 0.05):
                            dist = 0.0
                            break

            if dist < min_dist:
                min_dist = dist
                best = i

        if best != -1:
            self.annotations[pid].pop(best)
            self.save_annotations()
            self.refresh_page_render(page_idx)

    def refresh_page_render(self, p_idx: int) -> None:
        if p_idx in self.rendered_pages:
            self.rendered_pages.remove(p_idx)
        self.render_visible_pages()

    def _load_external_comments(self, path: str) -> None:
        """Loads comments made in other applications, blocking until done."""
        self.external_comments = read_external_comments(path)

    def _load_external_comments_async(self, path: str) -> None:
        """
        Loads comments made in other applications on a background thread.
        Parsing them with pypdf on the UI thread took about two seconds on the
        756-page PDF spec and delayed the first page by as much.
        """
        self.external_comments = {}
        bridge = self._comments_bridge
        if bridge is None:
            bridge = _CommentsBridge(self)
            bridge.loaded.connect(self._on_external_comments_loaded)
            self._comments_bridge = bridge

        doc = self.current_doc
        use_core = doc is not None and hasattr(doc, "get_comments")

        def work():
            result = read_comments_from_core(doc) if use_core else read_external_comments(path)
            try:
                bridge.loaded.emit(path, result)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="riemann-comments").start()

    def _on_external_comments_loaded(self, path: str, comments: dict) -> None:
        if path == self.pdf_path():
            self.external_comments = comments

    def _get_comment_at_pos(
        self, page_idx: int, pos: Any, page_w: int, page_h: int
    ) -> Optional[Dict[str, Any]]:
        """Finds any external comment clicked or hovered on at the given page position."""
        if not self.external_comments:
            return None
        comments = self.external_comments.get(page_idx, [])
        if not comments:
            return None

        rx, ry = pos.x() / max(1, page_w), pos.y() / max(1, page_h)
        rx, ry = self._map_to_unrotated(rx, ry)

        for c in comments:
            x1, y1, x2, y2 = c["rel_rect"]
            hit_pad_x = max(0.02, 16.0 / max(1, page_w))
            hit_pad_y = max(0.02, 16.0 / max(1, page_h))
            if (x1 - hit_pad_x <= rx <= x2 + hit_pad_x) and (
                y1 - hit_pad_y <= ry <= y2 + hit_pad_y
            ):
                return c
        return None

    def show_external_comment_dialog(self, comment: Dict[str, Any]) -> None:
        """Displays dialog showing external comment content, author, and date."""
        dlg = CommentViewDialog(comment, self)
        dlg.exec()
