"""
Rendering Mixin.

Handles layout calculation, virtualization, and PDF page rendering.
"""

import sys
from typing import Dict, Tuple

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLineEdit, QWidget

from ....core.constants import ViewMode, ZoomMode
from ..utils import generate_reflow_html
from ..widgets import PageWidget


class RenderingMixin:
    """Methods for rendering PDF pages and layouts."""

    def rebuild_layout(self) -> None:
        """Reconstructs the layout of page widgets, handling virtualization."""
        if not self.current_doc:
            return

        sb = self.scroll.verticalScrollBar()
        was_blocked = sb.signalsBlocked()
        sb.blockSignals(True)
        old_scroll_val = sb.value()

        self.page_widgets.clear()
        self.rendered_pages.clear()
        self._virtual_enabled = False
        self._virtual_range = (0, 0)

        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        count = self.current_doc.page_count
        use_virtual = self.continuous_scroll and (count > self.virtual_threshold)

        if use_virtual:
            self._build_virtual_layout(count)
        else:
            self._build_standard_layout(count)

        self.scroll_content.adjustSize()
        QApplication.processEvents()

        if self.continuous_scroll:
            sb.setValue(old_scroll_val)
        sb.blockSignals(was_blocked)

    def _build_virtual_layout(self, count: int) -> None:
        """Helper for virtualized layout construction."""
        self._virtual_enabled = True
        buf_before = 30
        buf_after = 40
        start = max(0, self.current_page_index - buf_before)
        end = min(count, self.current_page_index + buf_after)
        self._virtual_range = (start, end)

        if not self._cached_base_size:
            self._probe_base_page_size()
        _, base_h = self._cached_base_size or (595, 842)

        scale = self.calculate_scale()
        page_height = int(base_h * scale) + self.scroll_layout.spacing()

        self._top_spacer = QWidget()
        self._top_spacer.setFixedHeight(max(0, start * page_height))
        self.scroll_layout.addWidget(self._top_spacer)

        self._create_widgets_for_range(start, end)

        self._bottom_spacer = QWidget()
        self._bottom_spacer.setFixedHeight(max(0, (count - end) * page_height))
        self.scroll_layout.addWidget(self._bottom_spacer)

    def _build_standard_layout(self, count: int) -> None:
        """Helper for standard layout construction."""
        if self.continuous_scroll:
            pages = range(count)
        else:
            if self.facing_mode:
                start = (self.current_page_index // 2) * 2
                pages = range(start, min(start + 2, count))
            else:
                pages = range(self.current_page_index, self.current_page_index + 1)

        self._create_widgets_for_range(pages.start, pages.stop)

    def _create_widgets_for_range(self, start: int, end: int) -> None:
        """Creates and adds page widgets for a specific index range."""
        idx_ptr = start
        while idx_ptr < end:
            p_idx = idx_ptr
            is_pair = self.facing_mode and (p_idx + 1 < end) and (p_idx % 2 == 0)

            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            lbl_left = self._create_page_label(p_idx)
            layout.addWidget(lbl_left)
            self.page_widgets[p_idx] = lbl_left

            if is_pair:
                p_idx_right = p_idx + 1
                lbl_right = self._create_page_label(p_idx_right)
                layout.addWidget(lbl_right)
                self.page_widgets[p_idx_right] = lbl_right
                idx_ptr += 2
            else:
                idx_ptr += 1

            self.scroll_layout.addWidget(row)

    def _create_page_label(self, index: int) -> PageWidget:
        """Creates a PageWidget instance."""
        lbl = PageWidget()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setProperty("pageIndex", index)
        w, h = self._get_target_page_size()
        lbl.setFixedSize(w, h)
        bg = "#333" if self.dark_mode else "#fff"
        lbl.setStyleSheet(f"background-color: {bg}; border: 1px solid #555;")
        lbl.installEventFilter(self)
        return lbl

    def render_visible_pages(self) -> None:
        """Triggers rendering for pages currently within viewport range."""
        if not self.current_doc or not self.page_widgets:
            return

        # Determine visible range roughly
        target_indices = set()
        start = max(0, self.current_page_index - 7)
        end = min(self.current_doc.page_count, self.current_page_index + 8)

        for i in range(start, end):
            target_indices.add(i)

        # Clear pages that moved out of range
        for idx in list(self.rendered_pages):
            if idx not in target_indices:
                if idx in self.page_widgets:
                    self.page_widgets[idx].clear()
                    self.page_widgets[idx].setText(f"Page {idx + 1}")
                self.rendered_pages.remove(idx)

        scale = self.calculate_scale()
        for idx in target_indices:
            if idx not in self.rendered_pages and idx in self.page_widgets:
                self._render_single_page(idx, scale)
                self.rendered_pages.add(idx)

    def _render_single_page(self, idx: int, scale: float) -> None:
        """Renders a page, forms, and annotations."""
        try:
            dpr = self.devicePixelRatio()
            render_scale = scale * dpr
            res = self.current_doc.render_page(
                idx, render_scale, 1 if self.dark_mode else 0
            )

            img = QImage(res.data, res.width, res.height, QImage.Format.Format_ARGB32)
            img.setDevicePixelRatio(dpr)
            pix = QPixmap.fromImage(img)

            w, h = pix.width() / dpr, pix.height() / dpr

            self._render_forms(idx, scale, h)
            self._render_overlays(idx, pix, scale, w, h)

            self.page_widgets[idx].setPixmap(pix)

        except Exception as e:
            sys.stderr.write(f"Render error page {idx}: {e}\n")

    def _render_forms(self, idx: int, scale: float, logical_h: float) -> None:
        """Overlays interactive form widgets."""
        if idx in self.form_widgets:
            for w in self.form_widgets[idx]:
                w.deleteLater()
        self.form_widgets[idx] = []

        try:
            forms = self.current_doc.get_form_widgets(idx)
            for _, rect_tuple, f_type, value, is_checked in forms:
                cache_key = (idx, rect_tuple)
                if cache_key in self.form_values_cache:
                    cached = self.form_values_cache[cache_key]
                    if "Text" in f_type:
                        value = cached
                    elif "Checkbox" in f_type:
                        is_checked = cached

                l, t, r, b = rect_tuple
                x = int(l * scale)
                w_rect = int((r - l) * scale)
                h_rect = int((t - b) * scale)
                y = int(logical_h - (t * scale))
                if h_rect < 0:
                    y += h_rect
                    h_rect = abs(h_rect)

                ctrl = None
                if "Text" in f_type:
                    ctrl = QLineEdit(self.page_widgets[idx])
                    ctrl.setText(value)
                    ctrl.setStyleSheet(
                        "background: rgba(0,100,255,0.15); border: 1px solid #50a0ff;"
                    )
                    ctrl.textChanged.connect(
                        lambda v, k=cache_key: self.form_values_cache.update({k: v})
                    )
                elif "Checkbox" in f_type or "Radio" in f_type:
                    ctrl = QCheckBox(self.page_widgets[idx])
                    ctrl.setChecked(is_checked)
                    ctrl.stateChanged.connect(
                        lambda v, k=cache_key: self.form_values_cache.update(
                            {k: bool(v)}
                        )
                    )

                if ctrl:
                    ctrl.setGeometry(x, y, w_rect, h_rect)
                    ctrl.show()
                    self.form_widgets[idx].append(ctrl)
        except Exception:
            pass

    def _render_overlays(
        self, idx: int, pix: QPixmap, scale: float, lw: float, lh: float
    ) -> None:
        """Draws search results and annotations onto the pixmap."""
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.search_result and self.search_result[0] == idx:
            c = QColor(255, 255, 0, 100 if self.dark_mode else 128)
            painter.setBrush(c)
            painter.setPen(Qt.PenStyle.NoPen)
            for l, t, r, b in self.search_result[1]:
                x, w = int(l * scale), int((r - l) * scale)
                h = int((t - b) * scale)
                y = int(lh - (t * scale))
                painter.drawRect(x, y, w, h)

        if str(idx) in self.annotations:
            for anno in self.annotations[str(idx)]:
                self._draw_annotation(painter, anno, lw, lh, scale)

        painter.end()

    def _draw_annotation(
        self, painter: QPainter, anno: Dict, lw: float, lh: float, scale: float
    ) -> None:
        """Draws a single annotation."""
        atype = anno.get("type", "note")

        if atype == "note":
            pos = anno.get("rel_pos", (0, 0))
            painter.setPen(QPen(QColor(255, 255, 0, 180), 2))
            painter.setBrush(QColor(255, 255, 0, 50))
            painter.drawEllipse(QPoint(int(pos[0] * lw), int(pos[1] * lh)), 10, 10)

        elif atype == "drawing":
            points = anno.get("points", [])
            if points:
                poly = QPolygon(
                    [QPoint(int(p[0] * lw), int(p[1] * lh)) for p in points]
                )
                c = QColor(anno["color"])
                w = anno["thickness"]
                if anno.get("subtype") == "highlight":
                    c.setAlpha(80)
                    w *= 3
                painter.setPen(
                    QPen(
                        c,
                        w,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    )
                )
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(poly)

        elif atype == "markup" and "rects" in anno:
            subtype = anno.get("subtype", "highlight")
            c_val = anno.get("color", (255, 255, 0))
            if isinstance(c_val, list) or isinstance(c_val, tuple):
                color = QColor(*c_val)
            else:
                color = QColor(c_val)

            for l, t, r, b in anno["rects"]:
                x = int(l * scale)
                w = int((r - l) * scale)
                h = int((t - b) * scale)
                y = int(lh - (t * scale))
                if h < 0:
                    y += h
                    h = abs(h)

                if subtype == "highlight":
                    color.setAlpha(120)
                    painter.setBrush(color)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRect(x, y, w, h)
                elif subtype == "underline":
                    pen = QPen(color, max(1, int(2 * scale)))
                    painter.setPen(pen)
                    base_y = y + int(1.25 * h) - int(2 * scale)
                    painter.drawLine(x, base_y, x + w, base_y)
                elif subtype == "strikeout":
                    pen = QPen(color, max(1, int(2 * scale)))
                    painter.setPen(pen)
                    mid = y + h // 2
                    painter.drawLine(x, mid, x + w, mid)

    def calculate_scale(self) -> float:
        """Determines rendering scale based on ZoomMode."""
        if self.zoom_mode == ZoomMode.MANUAL:
            return self.manual_scale

        if not self._cached_base_size:
            self._probe_base_page_size()
            if not self._cached_base_size:
                return 1.0

        bw, bh = self._cached_base_size
        viewport = self.scroll.viewport()
        vw = max(10, viewport.width() - 30)
        vh = max(10, viewport.height() - 20)

        if self.facing_mode and self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / (bw * 2)
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / bw
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            return vh / bh
        return 1.0

    def update_view(self) -> None:
        """Refreshes the view based on current mode."""
        if self.view_mode == ViewMode.IMAGE:
            self.render_visible_pages()
            if self.current_doc:
                self.txt_page.setText(str(self.current_page_index + 1))
                self.lbl_total.setText(f"/ {self.current_doc.page_count}")
            self.settings.setValue("lastPage", self.current_page_index)
            self.settings.setValue(
                "lastScrollY", self.scroll.verticalScrollBar().value()
            )
        else:
            if self.current_doc:
                txt = self.current_doc.get_page_text(self.current_page_index)
                html_content = generate_reflow_html(txt, self.dark_mode)
                self.web.setHtml(html_content)

    def _probe_base_page_size(self) -> None:
        """Caches base page size."""
        if not self.current_doc:
            self._cached_base_size = None
            return
        try:
            res = self.current_doc.render_page(0, 1.0, 0)
            self._cached_base_size = (res.width, res.height)
        except Exception:
            self._cached_base_size = (595, 842)

    def _get_target_page_size(self) -> Tuple[int, int]:
        """Calculates page pixel dimensions at current scale."""
        if not self._cached_base_size:
            return (int(595 * self.manual_scale), int(842 * self.manual_scale))
        bw, bh = self._cached_base_size
        s = self.calculate_scale()
        return (int(bw * s), int(bh * s))
