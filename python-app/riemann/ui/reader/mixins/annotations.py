"""
Annotations Mixin.

Handles annotation tools (pen, note), undo/redo stacks, and file persistence.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..widgets import PageWidget


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
        lbl_date.setStyleSheet("color: #888; font-size: 11px;")
        meta_layout.addWidget(lbl_date)

        hdr_layout.addLayout(meta_layout)
        hdr_layout.addStretch()
        layout.addLayout(hdr_layout)

        txt_edit = QTextEdit()
        txt_edit.setPlainText(comment.get("contents", ""))
        txt_edit.setReadOnly(True)
        txt_edit.setStyleSheet(
            "font-size: 13px; line-height: 1.4; border: 1px solid #555; border-radius: 6px; padding: 6px;"
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


class AnnotationsMixin:
    """
    Provides methods for managing user annotations on PDF documents.
    This mixin is intended to be integrated into the main reader component,
    managing state for active tools, persistence, and undo/redo stacks.
    """

    def _get_annotation_path(self) -> str:
        """
        Generates a centralized system file path for storing the current PDF's annotations.

        Returns:
            str: The absolute path to the JSON annotation storage file, or an empty string
                 if no document is currently loaded.
        """
        if not self.current_path:
            return ""
        path_hash = hashlib.sha256(self.current_path.encode("utf-8")).hexdigest()
        base_dir = Path.home() / ".local" / "share" / "riemann" / "annotations"
        base_dir.mkdir(parents=True, exist_ok=True)
        return str(base_dir / f"{path_hash}.json")

    def load_annotations(self) -> None:
        """
        Loads the annotation data from the persistent JSON storage into memory.
        If the file does not exist, initializes an empty annotation dictionary.
        """
        if not self.current_path:
            return
        p = self._get_annotation_path()
        if os.path.exists(p):
            with open(p, "r") as f:
                self.annotations = json.load(f)
        else:
            self.annotations = {}

    def save_annotations(self) -> None:
        """
        Serializes the current in-memory annotation dictionary and saves it to the persistent JSON storage.
        """
        if not self.current_path:
            return
        p = self._get_annotation_path()
        with open(p, "w") as f:
            json.dump(self.annotations, f)

    def toggle_annotation_mode(self, checked: bool) -> None:
        """
        Toggles the visibility and operational state of the annotation toolbar and tools.

        Args:
            checked (bool): True to enable annotation mode and display the toolbar; False to disable.
        """
        self.anno_toolbar.setVisible(checked)
        self.btn_annotate.setChecked(checked)
        if not checked:
            self.current_tool = "nav"
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.current_tool = "nav"
            self.anno_toolbar.btn_nav.setChecked(True)

    def set_tool(self, tool_id: str) -> None:
        """
        Selects the active annotation tool and updates the UI cursor accordingly.

        Args:
            tool_id (str): The identifier of the tool to activate (e.g., 'nav', 'eraser').
        """
        self.current_tool = tool_id
        if tool_id == "nav":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif tool_id == "eraser":
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def set_color(self, c: str) -> None:
        """
        Sets the active color for annotation drawing tools.

        Args:
            c (str): The hexadecimal color string.
        """
        self.pen_color = c

    def set_thickness(self, v: int) -> None:
        """
        Sets the active line thickness for annotation drawing tools.

        Args:
            v (int): The thickness value in pixels.
        """
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
        """
        Evaluates a mouse click event to determine if an existing interactive annotation (e.g., a note)
        was targeted, and triggers its associated interface if so.

        Args:
            label (PageWidget): The page widget receiving the click.
            event (QMouseEvent): The mouse event details.

        Returns:
            bool: True if an annotation was clicked and handled; False otherwise.
        """
        pid = str(label.property("pageIndex"))
        w, h = label.width(), label.height()
        rx, ry = event.pos().x() / w, event.pos().y() / h
        if hasattr(self, "_map_to_unrotated"):
            rx, ry = self._map_to_unrotated(rx, ry)

        for i, anno in enumerate(self.annotations.get(pid, [])):
            if anno.get("type") == "note":
                ax, ay = anno["rel_pos"]
                if ((rx - ax) ** 2 + (ry - ay) ** 2) ** 0.5 < 0.03:
                    self.show_annotation_popup(anno, int(pid), i)
                    return True
        return False

    def show_annotation_popup(self, data: Dict, p_idx: int, idx: int) -> None:
        """
        Displays an input dialog to edit or delete an existing text annotation.

        Args:
            data (Dict): The dictionary containing the annotation data.
            p_idx (int): The index of the page containing the annotation.
            idx (int): The index of the annotation within the page's annotation list.
        """
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
        """
        Prompts the user for input to create a new textual annotation at the specified relative coordinates.

        Args:
            p_idx (int): The index of the target page.
            rx (float): The relative X coordinate (0.0 to 1.0).
            ry (float): The relative Y coordinate (0.0 to 1.0).
            type (str): The type classification of the annotation. Defaults to "note".
        """
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
        """
        Internal helper to append new annotation data, update the undo stack, and serialize the state.

        Args:
            page_idx (int): The index of the target page.
            data (Dict): The new annotation data payload.
        """
        pid = str(page_idx)
        if pid not in self.annotations:
            self.annotations[pid] = []
        self.annotations[pid].append(data)
        self.undo_stack.append(("add", page_idx, len(self.annotations[pid]) - 1))
        self.redo_stack.clear()
        self.save_annotations()
        self.refresh_page_render(page_idx)

    def _handle_eraser_click(self, label: PageWidget, pos: Any, page_idx: int) -> None:
        """
        Processes an eraser tool click by calculating the distance to all annotations on the page
        and deleting the closest one within an interaction threshold.

        Args:
            label (PageWidget): The target page widget.
            pos (Any): The local coordinate position of the click event.
            page_idx (int): The index of the target page.
        """
        pid = str(page_idx)
        if pid not in self.annotations:
            return

        w, h = label.width(), label.height()
        rx, ry = pos.x() / w, pos.y() / h
        if hasattr(self, "_map_to_unrotated"):
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

            elif atype == "markup":
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
        """
        Forces a page to be re-rendered to visually reflect annotation state changes.

        Args:
            p_idx (int): The index of the page to invalidate and re-render.
        """
        if p_idx in self.rendered_pages:
            self.rendered_pages.remove(p_idx)
        self.render_visible_pages()

    def _load_external_comments(self, path: str) -> None:
        """Extracts native PDF comments/annotations created by external applications."""
        self.external_comments: Dict[int, List[Dict[str, Any]]] = {}
        if not path or not os.path.isfile(path) or path.lower().endswith(".md"):
            return

        try:
            import pypdf

            reader = pypdf.PdfReader(path)
            for page_idx, page in enumerate(reader.pages):
                if "/Annots" not in page:
                    continue
                annots = page["/Annots"]
                crop = page.cropbox
                origin_x = float(crop.left)
                origin_y = float(crop.bottom)
                cw = float(crop.width) if float(crop.width) > 0 else 1.0
                ch = float(crop.height) if float(crop.height) > 0 else 1.0

                page_comments = []
                for annot_ref in annots:
                    try:
                        obj = annot_ref.get_object()
                        if not obj:
                            continue
                        subtype = str(obj.get("/Subtype", "")).lstrip("/")
                        if subtype in ("Link", "Widget"):
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

                        date_str = str(
                            obj.get("/M", "") or obj.get("/CreationDate", "") or ""
                        ).strip()
                        if date_str.startswith("D:"):
                            raw = date_str[2:16]
                            if len(raw) >= 8:
                                date_str = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
                                if len(raw) >= 14:
                                    date_str += (
                                        f" {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
                                    )

                        rect = obj.get("/Rect", [])
                        if len(rect) == 4:
                            x1 = (float(min(rect[0], rect[2])) - origin_x) / cw
                            x2 = (float(max(rect[0], rect[2])) - origin_x) / cw
                            y1 = 1.0 - ((float(max(rect[1], rect[3])) - origin_y) / ch)
                            y2 = 1.0 - ((float(min(rect[1], rect[3])) - origin_y) / ch)
                            rel_rect = (
                                min(x1, x2),
                                min(y1, y2),
                                max(x1, x2),
                                max(y1, y2),
                            )
                        else:
                            rel_rect = (0.0, 0.0, 0.0, 0.0)

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
                    self.external_comments[page_idx] = page_comments
        except Exception as e:
            print(f"[Warning] Failed to load external PDF comments: {e}")

    def _get_comment_at_pos(
        self, page_idx: int, pos: Any, page_w: int, page_h: int
    ) -> Optional[Dict[str, Any]]:
        """Finds any external comment clicked or hovered on at the given page position."""
        if not hasattr(self, "external_comments") or not self.external_comments:
            return None
        comments = self.external_comments.get(page_idx, [])
        if not comments:
            return None

        rx, ry = pos.x() / max(1, page_w), pos.y() / max(1, page_h)
        if hasattr(self, "_map_to_unrotated"):
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
