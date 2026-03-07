"""
Annotations Mixin.

Handles annotation tools (pen, note), undo/redo stacks, and file persistence.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QInputDialog

from ..widgets import PageWidget


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
        """
        Forces a page to be re-rendered to visually reflect annotation state changes.

        Args:
            p_idx (int): The index of the page to invalidate and re-render.
        """
        if p_idx in self.rendered_pages:
            self.rendered_pages.remove(p_idx)
        self.render_visible_pages()
