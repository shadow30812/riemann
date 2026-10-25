"""
Rendering Mixin.

Handles layout calculation, virtualization, and PDF page rendering.
"""

import os
import sys
import urllib.parse
from typing import Dict, Optional, Tuple

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
    QTransform,
)
from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLineEdit, QWidget

from ....core.constants import ViewMode, ZoomMode
from ..render_worker import PAGE_CACHE, PAGE_IMAGE_FORMAT
from ..utils import generate_markdown_html, generate_reflow_html
from ..widgets import PageWidget

# Thumbnails are rendered at this fraction of the target resolution and shown
# (upscaled) until the full-resolution page arrives.
THUMBNAIL_FACTOR = 0.25


class RenderingMixin:
    """
    Provides rendering capabilities and virtualized layouts for document viewers.
    Meant to be mixed into the primary reader window interface class.
    """

    def rebuild_layout(self) -> None:
        """
        Reconstructs the primary layout tree of page widgets, enforcing view modes
        and scroll virtualization limits dynamically based on current configuration.
        """
        if not self.current_doc:
            return

        self._ignore_scroll = True
        self.scroll_timer.stop()

        sb = self.scroll.verticalScrollBar()
        was_blocked = sb.signalsBlocked()
        sb.blockSignals(True)

        target_page = self.current_page_index
        target_value = sb.value()
        target_max = sb.maximum()

        self.page_widgets.clear()
        self.rendered_pages.clear()
        self._virtual_enabled = False
        self._virtual_range = (0, 0)
        worker = getattr(self, "_render_worker", None)
        if worker is not None:
            worker.clear()

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
        self._last_applied_scale = self.calculate_scale()

        if hasattr(self, "scroll_layout") and self.scroll_layout:
            self.scroll_layout.activate()

        QApplication.processEvents()

        self.current_page_index = target_page

        # Unblock before restoring the position: QScrollArea only moves the
        # content when it sees valueChanged. _ignore_scroll keeps the reader's
        # own handler out of it.
        sb.blockSignals(was_blocked)
        if target_max > 0 and sb.maximum() > 0:
            ratio = target_value / target_max
            sb.setValue(int(ratio * sb.maximum()))
        else:
            self.ensure_visible(target_page)
        self._ignore_scroll = False
        if hasattr(self, "scroll") and self.scroll:
            self.scroll.setFocus()

        def _finalize_rebuild():
            self.current_page_index = target_page
            self._ignore_scroll = False

        QTimer.singleShot(50, _finalize_rebuild)

    def _do_rebuild_and_render(self) -> None:
        """Debounced executor for expensive layout and render rebuilds."""
        self.rebuild_layout()
        self.rendered_pages.clear()
        self.update_view()

    def _build_virtual_layout(self, count: int) -> None:
        """
        Constructs a virtualized UI layout utilizing top and bottom spacer blocks
        to mitigate RAM and Qt object limits on excessively large PDF documents.

        Args:
            count (int): The total number of pages in the current document.
        """
        self._virtual_enabled = True
        buf_before = 8
        buf_after = 16
        start = max(0, self.current_page_index - buf_before)
        end = min(count, self.current_page_index + buf_after)

        if getattr(self, "facing_mode", False) and start % 2 != 0:
            start = max(0, start - 1)
        self._virtual_range = (start, end)

        if not self._cached_base_size:
            self._probe_base_page_size()
        _, base_h = self._cached_base_size or (595, 842)

        scale = self.calculate_scale()
        page_height = int(base_h * scale) + self.scroll_layout.spacing()

        top_spacer_height = (
            (start // 2) * page_height
            if getattr(self, "facing_mode", False)
            else start * page_height
        )
        bottom_spacer_height = (
            max(0, (((count + 1) // 2) - ((end + 1) // 2)) * page_height)
            if getattr(self, "facing_mode", False)
            else max(0, (count - end) * page_height)
        )

        self._top_spacer = QWidget()
        self._top_spacer.setFixedHeight(max(0, top_spacer_height))
        self.scroll_layout.addWidget(self._top_spacer)

        self._create_widgets_for_range(start, end)

        self._bottom_spacer = QWidget()
        self._bottom_spacer.setFixedHeight(bottom_spacer_height)
        self.scroll_layout.addWidget(self._bottom_spacer)

    def _build_standard_layout(self, count: int) -> None:
        """
        Constructs a rigid, non-virtualized standard UI layout where every page
        is instantiated directly into the scroll view matrix.

        Args:
            count (int): The total number of pages in the current document.
        """
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
        """
        Iterates and generates individual visual label wrappers for a defined range
        of page indices, integrating multi-column structures for facing pages.

        Args:
            start (int): The starting index of the page batch.
            end (int): The ending boundary index of the page batch.
        """
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
        """
        Instantiates an empty core structural label widget tailored for holding PDF pixels.

        Args:
            index (int): The associated integer page index for identification.

        Returns:
            PageWidget: A configured custom label container widget.
        """
        lbl = PageWidget()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setProperty("pageIndex", index)
        lbl.setScaledContents(True)

        w, h = self._get_target_page_size()
        lbl.setFixedSize(w, h)
        bg = "#333" if self.theme_mode != 0 else "#fff"

        lbl.setStyleSheet(f"background-color: {bg}; border: 1px solid #555;")
        lbl.installEventFilter(self)
        return lbl

    def render_visible_pages(self) -> None:
        """
        Executes rendering sweeps on currently visible components based on scroll state.
        Discards older distant cached elements while converting adjacent pages into textures.
        """
        if not self.current_doc or not self.page_widgets:
            return

        scale = self.calculate_scale()
        last_scale = getattr(self, "_last_applied_scale", None)
        if last_scale and abs(last_scale - scale) > 1e-4 and hasattr(self, "apply_visual_zoom"):
            # The viewport changed size without a zoom event; resize the page
            # slots first so they match what is about to be rendered.
            self.apply_visual_zoom()
            scale = self.calculate_scale()
        target_indices = set()

        if self._virtual_enabled:
            margin = 2 if scale >= 2.0 else (3 if scale >= 1.2 else 5)
            start = max(0, self.current_page_index - margin)
            end = min(self.current_doc.page_count, self.current_page_index + margin + 1)
            for i in range(start, end):
                target_indices.add(i)
        else:
            viewport_y = self.scroll.verticalScrollBar().value()
            viewport_h = self.scroll.viewport().height()

            buf_mult = 0.5 if scale >= 2.0 else (1.0 if scale >= 1.2 else 2.5)
            view_start = viewport_y - (viewport_h * buf_mult)
            view_end = viewport_y + (viewport_h * (buf_mult + 1.0))

            for idx, widget in self.page_widgets.items():
                try:
                    row = widget.parentWidget()
                    if not row:
                        continue
                    w_y = row.pos().y()
                    w_bottom = w_y + widget.height()

                    if w_bottom >= view_start and w_y <= view_end:
                        target_indices.add(idx)
                except Exception:
                    continue

            if not target_indices:
                margin = 1 if scale >= 2.0 else 2
                start = max(0, self.current_page_index - margin)
                end = min(self.current_doc.page_count, self.current_page_index + margin + 1)
                for i in range(start, end):
                    target_indices.add(i)

        worker = getattr(self, "_render_worker", None)
        for idx in list(self.rendered_pages):
            if idx not in target_indices:
                if idx in self.page_widgets:
                    self.page_widgets[idx].clear()
                    self.page_widgets[idx].setText(f"Page {idx + 1}")
                self.rendered_pages.remove(idx)
                if worker is not None:
                    worker.cancel_page(idx)

        if worker is not None:
            worker.set_focus(self.current_page_index)

        # Prioritize rendering the visible page first for instant visual feedback
        sorted_indices = sorted(
            target_indices, key=lambda i: abs(i - self.current_page_index)
        )
        for idx in sorted_indices:
            if idx not in self.rendered_pages and idx in self.page_widgets:
                self._render_single_page(idx, scale)
                self.rendered_pages.add(idx)

    def _render_owner(self):
        """Identifies the currently loaded document in the shared page cache."""
        return (id(self), getattr(self, "_doc_generation", 0))

    def _render_single_page(self, idx: int, scale: float) -> None:
        """
        Puts a page on screen, preferring the shared cache and otherwise queueing
        it on the background render worker. Without a worker (tests, embedded
        use) the page is rendered synchronously.

        Args:
            idx (int): The index of the specific page to evaluate and draw.
            scale (float): The multiplier determining display resolution scaling factor.
        """
        try:
            dpr = self.devicePixelRatio()
            render_scale = round(scale * dpr, 4)
            theme = self.theme_mode
            owner = self._render_owner()

            cached = PAGE_CACHE.get((owner, idx, render_scale, theme))
            if cached is not None:
                self._display_page_image(idx, cached, scale, dpr)
                return

            worker = getattr(self, "_render_worker", None)
            if worker is None:
                res = self.current_doc.render_page(idx, render_scale, theme)
                img = QImage(
                    res.data, res.width, res.height, PAGE_IMAGE_FORMAT
                ).copy()
                PAGE_CACHE.put((owner, idx, render_scale, theme), img)
                self._display_page_image(idx, img, scale, dpr)
                return

            # Links already known for this page work before the pixels arrive.
            if idx in getattr(self, "_raw_links_cache", {}):
                w, h = self._get_target_page_size()
                if getattr(self, "rotation", 0) in (90, 270):
                    w, h = h, w
                self._render_links(idx, scale, w, h)

            stand_in = PAGE_CACHE.find_any(owner, idx, theme)
            if stand_in is not None:
                self._display_stand_in(idx, stand_in)
            else:
                thumb_scale = max(0.12, render_scale * THUMBNAIL_FACTOR)
                worker.request(idx, round(thumb_scale, 4), theme, True)
            worker.request(idx, render_scale, theme, False)

        except Exception as e:
            sys.stderr.write(f"Render error page {idx}: {e}\n")

    def _on_page_rendered(
        self,
        generation,
        idx: int,
        render_scale: float,
        theme: int,
        thumbnail: bool,
        img,
        meta=None,
    ) -> None:
        """Receives a page from the render worker and shows it if still wanted."""
        if generation != self._render_owner() or not self.current_doc:
            return
        if meta is not None:
            links, forms = meta
            self._raw_links_cache.setdefault(idx, links)
            self._raw_forms_cache.setdefault(idx, forms)
        if not thumbnail:
            PAGE_CACHE.put((generation, idx, render_scale, theme), img)

        widget = self.page_widgets.get(idx)
        if widget is None or not shiboken6.isValid(widget):
            return
        if idx not in self.rendered_pages or theme != self.theme_mode:
            return

        scale = self.calculate_scale()
        dpr = self.devicePixelRatio()
        if thumbnail:
            if not getattr(widget, "has_full_page", False):
                self._display_stand_in(idx, img)
                w, h = self._get_target_page_size()
                if getattr(self, "rotation", 0) in (90, 270):
                    w, h = h, w
                self._render_links(idx, scale, w, h)
            return

        wanted_scale = round(scale * dpr, 4)
        if abs(render_scale - wanted_scale) > 1e-3:
            # Zoom changed while this page was in flight: show it as a stand-in
            # and make sure a render at the current scale is on its way.
            if not getattr(widget, "has_full_page", False):
                self._display_stand_in(idx, img)
            worker = getattr(self, "_render_worker", None)
            cached = PAGE_CACHE.get((generation, idx, wanted_scale, theme))
            if cached is not None:
                self._display_page_image(idx, cached, scale, dpr)
            elif worker is not None:
                worker.request(idx, wanted_scale, theme, False)
            return
        self._display_page_image(idx, img, scale, dpr)

    def _display_stand_in(self, idx: int, img) -> None:
        """Shows a low-resolution or wrong-scale image, stretched to the page slot."""
        widget = self.page_widgets.get(idx)
        if widget is None:
            return
        pix = QPixmap.fromImage(img)
        rotation = getattr(self, "rotation", 0)
        if rotation != 0:
            pix = pix.transformed(QTransform().rotate(rotation))
        # Stretch once here. Left to QLabel's scaledContents, the small image
        # was rescaled on every repaint, about 8 ms per page per scroll tick.
        dpr = self.devicePixelRatio()
        target = widget.size() * dpr
        if not target.isEmpty() and pix.size() != target:
            pix = pix.scaled(
                target,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pix.setDevicePixelRatio(dpr)
        widget.has_full_page = False
        widget.setPixmap(pix)

    def _display_page_image(self, idx: int, img, scale: float, dpr: float) -> None:
        """Composites overlays onto a rendered page image and displays it."""
        widget = self.page_widgets.get(idx)
        if widget is None:
            return

        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(dpr)
        w, h = pix.width() / dpr, pix.height() / dpr

        self._render_forms(idx, scale, w, h)
        self._render_links(idx, scale, w, h)
        self._render_overlays(idx, pix, scale, w, h)

        rotation = getattr(self, "rotation", 0)
        if rotation != 0:
            transform = QTransform().rotate(rotation)
            pix = pix.transformed(transform, Qt.TransformationMode.SmoothTransformation)

        widget.has_full_page = True
        widget.setPixmap(pix)

    def invalidate_page_cache(self, idx: Optional[int] = None) -> None:
        """Forgets cached renders after the PDF content itself changed."""
        owner = self._render_owner()
        if idx is None:
            PAGE_CACHE.drop_owner(owner)
            self._raw_links_cache = {}
            self._raw_forms_cache = {}
        else:
            PAGE_CACHE.drop_page(owner, idx)
            getattr(self, "_raw_forms_cache", {}).pop(idx, None)

    def _render_forms(
        self, idx: int, scale: float, logical_w: float, logical_h: float
    ) -> None:
        """
        Synthesizes embedded interactive PDF form controls onto the layout architecture.

        Args:
            idx (int): The current page index to evaluate for embedded fields.
            scale (float): The visual zoom scale modifier.
            logical_h (float): The normalized logical height metric.
        """
        if idx in self.form_widgets:
            for w in self.form_widgets[idx]:
                if shiboken6.isValid(w):
                    w.deleteLater()
        self.form_widgets[idx] = []

        try:
            forms_cache = getattr(self, "_raw_forms_cache", None)
            if forms_cache is None:
                forms_cache = self._raw_forms_cache = {}
            if idx not in forms_cache:
                forms_cache[idx] = self.current_doc.get_form_widgets(idx)
            forms = forms_cache[idx]
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

                rotation = getattr(self, "rotation", 0)
                if rotation == 90:
                    x, y = int(logical_h) - y - h_rect, x
                    w_rect, h_rect = h_rect, w_rect
                elif rotation == 180:
                    x, y = int(logical_w) - x - w_rect, int(logical_h) - y - h_rect
                elif rotation == 270:
                    x, y = y, int(logical_w) - x - w_rect
                    w_rect, h_rect = h_rect, w_rect

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

    def _render_links(
        self, idx: int, scale: float, logical_w: float, logical_h: float
    ) -> None:
        """Extracts and scales link bounding boxes from the PDF to the UI widget."""
        scaled_links = []
        try:
            links_cache = getattr(self, "_raw_links_cache", None)
            if links_cache is None:
                links_cache = self._raw_links_cache = {}
            if idx not in links_cache:
                links_cache[idx] = (
                    self.current_doc.get_links(idx)
                    if hasattr(self.current_doc, "get_links")
                    else []
                )
            links = links_cache[idx]

            for url, (l, t, r, b) in links:
                x = int(l * scale)
                w_rect = int((r - l) * scale)
                h_rect = int((t - b) * scale)
                y = int(logical_h - (t * scale))
                if h_rect < 0:
                    y += h_rect
                    h_rect = abs(h_rect)

                rotation = getattr(self, "rotation", 0)
                if rotation == 90:
                    x, y = int(logical_h) - y - h_rect, x
                    w_rect, h_rect = h_rect, w_rect
                elif rotation == 180:
                    x, y = (
                        int(logical_w) - x - w_rect,
                        int(logical_h) - y - h_rect,
                    )
                elif rotation == 270:
                    x, y = y, int(logical_w) - x - w_rect
                    w_rect, h_rect = h_rect, w_rect

                # A little slack around the link makes small targets easy to hit.
                rect = QRect(x, y, max(1, w_rect), max(1, h_rect)).adjusted(-2, -2, 2, 2)
                scaled_links.append((rect, url))
        except Exception as e:
            sys.stderr.write(f"Link extraction error page {idx}: {e}\n")

        if idx in self.page_widgets:
            self.page_widgets[idx].link_rects = scaled_links

    def _render_overlays(
        self, idx: int, pix: QPixmap, scale: float, lw: float, lh: float
    ) -> None:
        """
        Applies non-destructive overlays for active annotations and query highlights.

        Args:
            idx (int): Target page index.
            pix (QPixmap): The source pixmap object to manipulate and overlay elements onto.
            scale (float): Rendering magnification variable.
            lw (float): The logical dimension width calculation.
            lh (float): The logical dimension height calculation.
        """
        has_search = bool(self.search_result and self.search_result[0] == idx)
        has_annotations = bool(self.annotations.get(str(idx)))
        if not has_search and not has_annotations:
            # Painting detaches the pixmap from the cached image it shares
            # memory with, a full copy of the page, so skip it when idle.
            return

        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if has_search:
            c = QColor(255, 255, 0, 100 if self.theme_mode != 0 else 128)
            painter.setBrush(c)
            painter.setPen(Qt.PenStyle.NoPen)
            for l, t, r, b in self.search_result[1]:
                x, w = int(l * scale), int((r - l) * scale)
                h = int((t - b) * scale)
                y = int(lh - (t * scale))
                painter.drawRect(x, y, w, h)

        if has_annotations:
            for anno in self.annotations[str(idx)]:
                self._draw_annotation(painter, anno, lw, lh, scale)

        painter.end()

    def _draw_annotation(
        self, painter: QPainter, anno: Dict, lw: float, lh: float, scale: float
    ) -> None:
        """
        Interprets geometry dict entries into rendered Qt graphical primitives.

        Args:
            painter (QPainter): The execution painting wrapper to process standard path nodes.
            anno (Dict): A serialized structure identifying custom bounds data for specific marks.
            lw (float): Normalized rendering width metrics.
            lh (float): Normalized rendering height metrics.
            scale (float): Geometric rendering scale modifier.
        """
        atype = anno.get("type", "note")

        if atype == "note":
            pos = anno.get("rel_pos", (0, 0))
            painter.setPen(QPen(QColor(255, 255, 0, 180), 2))
            painter.setBrush(QColor(255, 255, 0, 50))
            painter.drawEllipse(QPoint(int(pos[0] * lw), int(pos[1] * lh)), 10, 10)

        elif atype == "drawing":
            points = anno.get("points", [])
            if points:
                c = QColor(anno["color"])
                w = anno["thickness"]
                subtype = anno.get("subtype", "pen")

                if subtype == "highlight":
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

                if subtype in ("rect", "oval") and len(points) == 2:
                    p1 = QPoint(int(points[0][0] * lw), int(points[0][1] * lh))
                    p2 = QPoint(int(points[1][0] * lw), int(points[1][1] * lh))
                    rect = QRect(p1, p2).normalized()
                    if subtype == "rect":
                        painter.drawRect(rect)
                    else:
                        painter.drawEllipse(rect)
                else:
                    poly = QPolygon(
                        [QPoint(int(p[0] * lw), int(p[1] * lh)) for p in points]
                    )
                    painter.drawPolyline(poly)

        elif atype == "stamp":
            pos = anno.get("rel_pos", (0, 0))
            subtype = anno.get("subtype", "stamp_tick")
            c = QColor(anno.get("color", "#ff0000"))

            painter.setPen(
                QPen(
                    c,
                    max(2, int(4 * scale)),
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            cx, cy = int(pos[0] * lw), int(pos[1] * lh)
            size = int(12 * scale)

            if subtype == "stamp_tick":
                painter.drawLine(cx - size, cy, cx - size // 3, cy + size)
                painter.drawLine(cx - size // 3, cy + size, cx + size, cy - size)
            elif subtype == "stamp_cross":
                painter.drawLine(cx - size, cy - size, cx + size, cy + size)
                painter.drawLine(cx - size, cy + size, cx + size, cy - size)

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
        """
        Determines the dynamic visual viewport scale modifier taking ZoomMode constraints into account.

        Returns:
            float: The mathematically evaluated sizing zoom ratio.
        """
        if self.zoom_mode == ZoomMode.MANUAL:
            return self.manual_scale

        if not self._cached_base_size:
            self._probe_base_page_size()
            if not self._cached_base_size:
                return 1.0

        bw, bh = self._cached_base_size
        viewport = self.scroll.viewport()
        vw = max(10, viewport.width() + self._scrollbar_allowance() - 30)
        vh = max(10, viewport.height() - 20)

        if self.zoom_mode == ZoomMode.AUTO_FIT:
            scale_w = vw / (bw * 2) if self.facing_mode else vw / bw
            scale_h = vh / bh
            return min(scale_w, scale_h)

        if self.facing_mode and self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / (bw * 2)
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            return vw / bw
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            return vh / bh
        return 1.0

    def _scrollbar_allowance(self) -> int:
        """
        Width to add back to the viewport so fit-to-width ignores whether the
        vertical scrollbar happens to be shown. Otherwise the scale (and every
        rendered page) would change whenever the scrollbar appears or hides.
        """
        try:
            sb = self.scroll.verticalScrollBar()
            shown = int(sb.width()) if sb.isVisible() else 0
            return shown - int(sb.sizeHint().width())
        except Exception:
            return 0

    def update_view(self) -> None:
        """
        Triggers widespread sync operations tracking view coordinates, updating active displays
        for either pixel-precise layout views or dynamic text reflow modes.
        """
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
            if hasattr(self, "web") and self.web:
                self.web.setZoomFactor(self.calculate_scale())

            if getattr(self, "current_doc", None):
                txt = self.current_doc.get_page_text(self.current_page_index)
                html_content = generate_reflow_html(txt, self.theme_mode != 0)
                self.web.setHtml(html_content)

            elif getattr(
                self, "current_path", None
            ) and self.current_path.lower().endswith(".md"):
                try:
                    with open(self.current_path, "r", encoding="utf-8") as f:
                        text = f.read()
                    full_html = generate_markdown_html(text, self.theme_mode != 0)

                    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                        base_path = getattr(sys, "_MEIPASS")
                        font_path = os.path.join(
                            base_path,
                            "riemann",
                            "assets",
                            "fonts",
                            "NotoColorEmoji.ttf",
                        )

                    else:
                        base_path = os.path.dirname(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        )
                        font_path = os.path.join(
                            base_path, "assets", "fonts", "NotoColorEmoji.ttf"
                        )

                    font_uri = "file:///" + urllib.parse.quote(
                        font_path.replace("\\", "/")
                    )

                    emoji_style = f"""
                    <style>
                        @font-face {{
                            font-family: "Riemann Noto Emoji";
                            src: url("{font_uri}") format("truetype");
                        }}
                        body, p, span, div, h1, h2, h3, h4, h5, h6, table, th, td, li, pre, code {{ 
                            font-family: inherit, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Riemann Noto Emoji", "Twemoji Mozilla" !important; 
                        }}
                    </style>
                    """

                    full_html += emoji_style
                    self.web.setHtml(full_html)
                except Exception as e:
                    sys.stderr.write(f"Markdown Reload Error: {e}\n")

    def _probe_base_page_size(self) -> None:
        """
        Executes a background sample extraction on all pages assessing geometric foundation metrics,
        taking the maximum width and height to accommodate variable-sized PDFs.
        """
        if not self.current_doc:
            self._cached_base_size = None
            return
        try:
            max_w, max_h = 0, 0
            test_scale = 0.1
            probe_limit = min(5, self.current_doc.page_count)
            # Exact sizes come straight from the core when available; the
            # low-resolution render is the fallback for older builds.
            exact_size = getattr(type(self.current_doc), "get_page_size", None)

            for i in range(probe_limit):
                if exact_size is not None:
                    pw, ph = self.current_doc.get_page_size(i)
                    w, h = int(round(pw)), int(round(ph))
                else:
                    res = self.current_doc.render_page(i, test_scale, 0)
                    w = int(res.width / test_scale)
                    h = int(res.height / test_scale)

                if getattr(self, "rotation", 0) in (90, 270):
                    max_w = max(max_w, h)
                    max_h = max(max_h, w)
                else:
                    max_w = max(max_w, w)
                    max_h = max(max_h, h)

            self._cached_base_size = (
                (max_w, max_h) if max_w > 0 and max_h > 0 else (595, 842)
            )
        except Exception:
            self._cached_base_size = (595, 842)

    def _get_target_page_size(self) -> Tuple[int, int]:
        """
        Resolves final bounding box container sizes leveraging calculated metrics.

        Returns:
            Tuple[int, int]: Integer representations of logical page width and height boundaries.
        """
        if not self._cached_base_size:
            self._probe_base_page_size()
        if not self._cached_base_size:
            return (int(595 * self.manual_scale), int(842 * self.manual_scale))
        bw, bh = self._cached_base_size
        s = self.calculate_scale()
        return (int(bw * s), int(bh * s))

    def rotate_document(self) -> None:
        """Rotate document clockwise"""
        if not hasattr(self, "rotation"):
            self.rotation = 0
        self.rotation = (self.rotation + 90) % 360
        self._cached_base_size = None
        self.on_zoom_changed_internal()

    def rotate_document_ccw(self) -> None:
        """Rotate document counter-clockwise"""
        if not hasattr(self, "rotation"):
            self.rotation = 0
        self.rotation = (self.rotation - 90) % 360
        self._cached_base_size = None
        self.on_zoom_changed_internal()

    def _flush_scroll_layout(self) -> None:
        """
        Applies pending size changes to the scroll content immediately so the
        scrollbar range reflects the new page sizes before we reposition.
        """
        if hasattr(self, "scroll_layout") and self.scroll_layout:
            self.scroll_layout.invalidate()
            self.scroll_layout.activate()
        for _ in range(2):
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)

    def _page_top_y(self, idx: int) -> Optional[int]:
        """Returns the y offset of a page inside the scroll content, if laid out."""
        widget = self.page_widgets.get(idx)
        if widget is None or not shiboken6.isValid(widget):
            return None
        row = widget.parentWidget()
        if row is None:
            return None
        return row.y() + widget.y()

    def _page_anchor(self, y: float) -> Tuple[int, float]:
        """
        Finds the page under content position y and how far down it lies,
        as (page index, fraction of page height).
        """
        for idx, widget in self.page_widgets.items():
            top = self._page_top_y(idx)
            if top is None:
                continue
            h = max(1, widget.height())
            if top <= y < top + h + self.scroll_layout.spacing():
                return idx, max(0.0, min(1.0, (y - top) / h))

        idx = getattr(self, "current_page_index", 0)
        scale = getattr(self, "_last_applied_scale", None) or self.calculate_scale()
        base_h = self._cached_base_size[1] if self._cached_base_size else 842
        ph = int(base_h * scale) + self.scroll_layout.spacing()
        row = idx // 2 if getattr(self, "facing_mode", False) else idx
        frac = (y - row * ph) / ph if ph > 0 else 0.0
        return idx, max(0.0, min(1.0, frac))

    def apply_visual_zoom(self) -> None:
        """
        Updates the physical dimensions of the layout and active widgets immediately
        while anchoring the point at the centre of the viewport, so the same spot
        of the same page stays in view whatever the zoom direction.
        """
        if not hasattr(self, "scroll") or not self.scroll:
            return

        viewport = self.scroll.viewport()
        vbar = self.scroll.verticalScrollBar()
        half_view = viewport.height() / 2
        anchor_idx, anchor_frac = self._page_anchor(vbar.value() + half_view)

        self._update_all_widget_sizes()

        new_scale = self.calculate_scale()
        self._last_applied_scale = new_scale
        base_h = self._cached_base_size[1] if self._cached_base_size else 842
        spacing = self.scroll_layout.spacing()
        new_ph = int(base_h * new_scale) + spacing
        is_facing = getattr(self, "facing_mode", False)

        if self._virtual_enabled and self._cached_base_size:
            start, end = self._virtual_range
            doc_count = self.current_doc.page_count if self.current_doc else end
            top_spacer_height = (
                (start // 2) * new_ph if is_facing else start * new_ph
            )
            bottom_spacer_height = (
                max(0, (((doc_count + 1) // 2) - ((end + 1) // 2)) * new_ph)
                if is_facing
                else max(0, (doc_count - end) * new_ph)
            )

            if getattr(self, "_top_spacer", None):
                self._top_spacer.setFixedHeight(max(0, top_spacer_height))
            if getattr(self, "_bottom_spacer", None) and self.current_doc:
                self._bottom_spacer.setFixedHeight(max(0, bottom_spacer_height))

        self._flush_scroll_layout()

        page_top = self._page_top_y(anchor_idx)
        if page_top is None:
            row = anchor_idx // 2 if is_facing else anchor_idx
            page_top = row * new_ph
        page_h = new_ph - spacing
        target_y = int(page_top + anchor_frac * page_h - half_view)
        self._set_scroll_value_silently(max(0, target_y))

    def _set_scroll_value_silently(self, value: int) -> None:
        """
        Moves the vertical scrollbar without running the reader's own scroll
        handler. If the range has not caught up yet, the move is retried once
        the layout settles.

        The scrollbar's signals must stay connected: QScrollArea moves its
        content in response to valueChanged, so blocking them left the old
        pages on screen under a scrollbar that had already moved.
        """
        vbar = self.scroll.verticalScrollBar()

        def _apply():
            was_ignoring = getattr(self, "_ignore_scroll", False)
            self._ignore_scroll = True
            try:
                vbar.setValue(value)
            finally:
                self._ignore_scroll = was_ignoring

        _apply()
        if vbar.value() == value:
            return

        def _retry():
            if not shiboken6.isValid(vbar):
                return
            _apply()
            if hasattr(self, "render_visible_pages"):
                self.render_visible_pages()

        QTimer.singleShot(0, _retry)
