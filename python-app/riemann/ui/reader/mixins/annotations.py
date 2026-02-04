"""
Annotations Mixin.

Handles annotation tools (pen, note), undo/redo stacks, and file persistence.
"""

import json
import os
from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QInputDialog

from ..widgets import PageWidget


class AnnotationsMixin:
    """Methods for managing user annotations."""

    def load_annotations(self) -> None:
        """Loads annotations from JSON."""
        if not self.current_path:
            return
        p = str(self.current_path) + ".riemann.json"
        if os.path.exists(p):
            with open(p, "r") as f:
                self.annotations = json.load(f)
        else:
            self.annotations = {}

    def save_annotations(self) -> None:
        """Saves annotations to JSON."""
        if not self.current_path:
            return
        with open(str(self.current_path) + ".riemann.json", "w") as f:
            json.dump(self.annotations, f)

    def toggle_annotation_mode(self, checked: bool) -> None:
        """Shows/hides annotation toolbar."""
        self.anno_toolbar.setVisible(checked)
        self.btn_annotate.setChecked(checked)
        if not checked:
            self.current_tool = "nav"
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.current_tool = "nav"
            self.anno_toolbar.btn_nav.setChecked(True)

    def set_tool(self, tool_id: str) -> None:
        """Selects annotation tool."""
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
        self.pen_thickness = v

    def undo_annotation(self) -> None:
        """Undoes last annotation."""
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
        """Redoes last undone annotation."""
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
        """Checks for clicks on existing annotations."""
        pid = str(label.property("pageIndex"))
        x, y = event.pos().x(), event.pos().y()
        for i, anno in enumerate(self.annotations.get(pid, [])):
            if anno.get("type") == "note":
                ax, ay = anno["rel_pos"]
                px, py = ax * label.width(), ay * label.height()
                if ((x - px) ** 2 + (y - py) ** 2) ** 0.5 < 20:
                    self.show_annotation_popup(anno, int(pid), i)
                    return True
        return False

    def show_annotation_popup(self, data: Dict, p_idx: int, idx: int) -> None:
        """Shows edit dialog for annotation."""
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
        """Adds new note annotation."""
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
        """Adds annotation and saves."""
        pid = str(page_idx)
        if pid not in self.annotations:
            self.annotations[pid] = []
        self.annotations[pid].append(data)
        self.undo_stack.append(("add", page_idx, len(self.annotations[pid]) - 1))
        self.redo_stack.clear()
        self.save_annotations()
        self.refresh_page_render(page_idx)

    def _handle_eraser_click(self, label: PageWidget, pos: Any, page_idx: int) -> None:
        """Deletes annotation nearest to click."""
        pid = str(page_idx)
        if pid not in self.annotations:
            return
        w, h = label.width(), label.height()
        rx, ry = pos.x() / w, pos.y() / h

        best, min_dist = -1, 0.08
        for i, anno in enumerate(self.annotations[pid]):
            dist = 1.0
            if anno.get("type") in ("note", "text"):
                ax, ay = anno["rel_pos"]
                dist = ((rx - ax) ** 2 + (ry - ay) ** 2) ** 0.5
            elif anno.get("type") == "drawing":
                pts = anno.get("points", [])
                if pts:
                    dist = min(
                        [((rx - px) ** 2 + (ry - py) ** 2) ** 0.5 for px, py in pts]
                    )

            if dist < min_dist:
                min_dist = dist
                best = i

        if best != -1:
            self.annotations[pid].pop(best)
            self.save_annotations()
            self.refresh_page_render(page_idx)

    def refresh_page_render(self, p_idx: int) -> None:
        """Forces page re-render."""
        if p_idx in self.rendered_pages:
            self.rendered_pages.remove(p_idx)
        self.render_visible_pages()
