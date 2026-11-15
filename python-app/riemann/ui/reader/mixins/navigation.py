"""
Moving through the document: scrolling, the virtual page layout, page jumps, view and zoom modes, auto-scroll.

Jumps a reader makes on purpose (a link, the page box, the outline or
thumbnails, an AI result, a page cited by the assistant) go through
navigate_to, which remembers the position left behind as a page and the
fraction of it scrolled, so go_back (Alt+Left, the mouse back button) and
go_forward return to the exact spot. Scrolling and find-in-page do not count
as jumps. The history is cleared when another document is loaded.

Part of ReaderTab (riemann/ui/reader/tab.py). ReaderTab lists this mixin before
QWidget, so its Qt event handlers override QWidget's.

Notes:

zoom_step: Step from the zoom on screen. In the fit modes manual_scale still
holds the last manual zoom (even from an earlier session), so stepping from it
jumped to an unrelated size.
"""

import urllib.parse
from typing import Any, Optional, Tuple
from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QApplication,
    QScroller,
    QScrollerProperties,
)
from ....core.constants import ViewMode, ZoomMode
from ....core.log import log
from .host import ReaderHostBase


NAV_HISTORY_LIMIT = 100

class NavigationMixin(ReaderHostBase):
    _autoscroll_nav_timer = None
    _nav_back = None
    _nav_forward = None
    _resize_timer = None
    autoscroll_dir = 1
    _rebuild_debounce_timer = None
    _zoom_debounce_timer = None

    def _setup_scroller(self) -> None:
        QScroller.grabGesture(
            self.scroll.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture
        )
        scroller = QScroller.scroller(self.scroll.viewport())
        props = scroller.scrollerProperties()
        props.setScrollMetric(QScrollerProperties.ScrollMetric.DecelerationFactor, 0.01)
        props.setScrollMetric(QScrollerProperties.ScrollMetric.MaximumVelocity, 25)

        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.ScrollingCurve, QEasingCurve.Type.OutCubic
        )
        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.DragVelocitySmoothingFactor, 0.8
        )
        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.VerticalOvershootPolicy,
            QScrollerProperties.OvershootPolicy.OvershootAlwaysOn,
        )

        scroller.setScrollerProperties(props)

    def _get_closest_page(self, value: int) -> int:
        """The page nearest the middle of the viewport at scroll position value."""
        if not self.current_doc:
            return self.current_page_index

        scale = self.calculate_scale()
        base_h = self._cached_base_size[1] if self._cached_base_size else 842
        ph = int(base_h * scale) + self.scroll_layout.spacing()
        threshold_y = value + (self.scroll.viewport().height() / 2)

        if ph <= 0:
            return self.current_page_index

        return (
            min(self.current_doc.page_count - 1, max(0, int(threshold_y / ph) * 2))
            if self.facing_mode
            else min(self.current_doc.page_count - 1, max(0, int(threshold_y / ph)))
        )

    def defer_scroll_update(self, value: int) -> None:
        if self._ignore_scroll:
            return

        closest = self._get_closest_page(value)
        if closest != self.current_page_index:
            self.current_page_index = closest
            if self.current_doc:
                self.txt_page.setText(str(closest + 1))

        if not self.scroll_timer.isActive():
            self.scroll_timer.start()
        self._trigger_fullscreen_page_indicator()

    def _trigger_fullscreen_page_indicator(self) -> None:
        """Shows the floating page indicator near the scrollbar across all display modes."""
        if not self.current_doc or self.current_doc.page_count <= 0:
            return

        curr = self.current_page_index + 1
        total = self.current_doc.page_count
        self.page_indicator_popup.setText(f"{curr} / {total}")
        self.page_indicator_popup.adjustSize()

        self._page_indicator_fade_anim.stop()
        self.page_indicator_opacity.setOpacity(1.0)

        sb = self.scroll.verticalScrollBar()
        sb_width = sb.width() if sb.isVisible() else 14
        sb_val = sb.value()
        sb_max = sb.maximum()

        x = max(10, self.width() - sb_width - self.page_indicator_popup.width() - 15)
        if sb_max > 0:
            ratio = sb_val / sb_max
            y = int(40 + ratio * (self.height() - self.page_indicator_popup.height() - 80))
        else:
            y = (self.height() - self.page_indicator_popup.height()) // 2

        self.page_indicator_popup.move(x, y)
        self.page_indicator_popup.show()
        self.page_indicator_popup.raise_()

        self._page_indicator_timer.start(1200)

    def _start_page_indicator_fade(self) -> None:
        """Gradually fades out the page indicator over 800ms."""
        self._page_indicator_fade_anim.stop()
        self._page_indicator_fade_anim.setStartValue(self.page_indicator_opacity.opacity())
        self._page_indicator_fade_anim.setEndValue(0.0)
        self._page_indicator_fade_anim.start()

    def _on_page_indicator_fade_finished(self) -> None:
        """Hides the page indicator once faded out."""
        if self.page_indicator_opacity.opacity() <= 0.01:
            self.page_indicator_popup.hide()

    def real_scroll_handler(self) -> None:
        if self._ignore_scroll:
            return

        self.scroll_timer.stop()
        self.on_scroll_changed(self.scroll.verticalScrollBar().value())

    def on_scroll_changed(self, value: int) -> None:
        """Updates the current page as the view scrolls."""
        closest = self._get_closest_page(value)

        if closest != self.current_page_index:
            self.current_page_index = closest
            if self.current_doc:
                self.txt_page.setText(str(closest + 1))
            self.sidebar_nav.set_current_page(closest)

        if self._virtual_enabled:
            s, e = self._virtual_range
            count = self.current_doc.page_count
            if (self.current_page_index > e - 5 and e < count) or (
                self.current_page_index < s + 3 and s > 0
            ):
                self.rebuild_layout()

        self.render_visible_pages()

    def ensure_visible(self, index: int) -> None:
        if index in self.page_widgets:
            self.scroll.ensureWidgetVisible(self.page_widgets[index], 0, 0)
            return

        if self._virtual_enabled and self._cached_base_size:
            start, _ = self._virtual_range
            _, bh = self._cached_base_size
            ph = int(bh * self.calculate_scale()) + self.scroll_layout.spacing()
            top = self._top_spacer.height() if self._top_spacer else 0

            y = (
                top + max(0, (index // 2) - (start // 2)) * ph
                if self.facing_mode
                else top + max(0, index - start) * ph
            )
            self.scroll.verticalScrollBar().setValue(
                max(0, int(y - self.scroll.viewport().height() / 2))
            )

    def next_view(self) -> None:
        if not self.current_doc:
            return
        step = 2 if self.facing_mode else 1
        new_idx = min(self.current_doc.page_count - 1, self.current_page_index + step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(new_idx)

    def prev_view(self) -> None:
        step = 2 if self.facing_mode else 1
        new_idx = max(0, self.current_page_index - step)
        if new_idx != self.current_page_index:
            self.current_page_index = new_idx
            if not self.continuous_scroll:
                self.rebuild_layout()
            self.update_view()
            self.ensure_visible(new_idx)

    def start_middle_click_scroll(self, global_pos: QPoint) -> None:
        """Starts middle-click autoscroll navigation with explicit autoscroll cursor."""
        if not self.scroll:
            return
        self._autoscroll_active = True
        self._autoscroll_origin_global = global_pos
        self._autoscroll_speed_y = 0.0
        self._autoscroll_speed_x = 0.0
        QApplication.setOverrideCursor(Qt.CursorShape.SizeAllCursor)
        if self._autoscroll_nav_timer is None:
            self._autoscroll_nav_timer = QTimer(self)
            self._autoscroll_nav_timer.setInterval(20)
            self._autoscroll_nav_timer.timeout.connect(self._on_autoscroll_nav_tick)
        self._autoscroll_nav_timer.start()

    def stop_middle_click_scroll(self) -> None:
        """Stops middle-click autoscroll navigation and restores cursor."""
        self._autoscroll_active = False
        if (
            self._autoscroll_nav_timer is not None
            and self._autoscroll_nav_timer.isActive()
        ):
            self._autoscroll_nav_timer.stop()
        self._autoscroll_speed_y = 0.0
        self._autoscroll_speed_x = 0.0
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        if self.scroll:
            self.scroll.viewport().unsetCursor()

    def _on_autoscroll_nav_tick(self) -> None:
        """Ticks middle-click autoscroll navigation."""
        if not self._autoscroll_active or not self.scroll:
            return
        if self._autoscroll_speed_y != 0:
            vbar = self.scroll.verticalScrollBar()
            vbar.setValue(int(vbar.value() + self._autoscroll_speed_y))
        if self._autoscroll_speed_x != 0:
            hbar = self.scroll.horizontalScrollBar()
            hbar.setValue(int(hbar.value() + self._autoscroll_speed_x))

    def toggle_view_mode(self) -> None:
        self.view_mode = (
            ViewMode.REFLOW if self.view_mode == ViewMode.IMAGE else ViewMode.IMAGE
        )
        if self.view_mode == ViewMode.REFLOW:
            self._get_or_create_web_view()
            self.web.setZoomFactor(self.calculate_scale())

        self.stack.setCurrentIndex(1 if self.view_mode == ViewMode.REFLOW else 0)
        self.btn_reflow.setChecked(self.view_mode == ViewMode.REFLOW)

        if self._rebuild_debounce_timer is None:
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

    def toggle_facing_mode(self) -> None:
        self.facing_mode = not self.facing_mode
        self.settings.setValue("facingMode", self.facing_mode)
        self.btn_facing.setChecked(self.facing_mode)

        if self._rebuild_debounce_timer is None:
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

    def toggle_scroll_mode(self) -> None:
        self.continuous_scroll = not self.continuous_scroll
        self.settings.setValue("continuousScrollMode", self.continuous_scroll)
        self.btn_scroll_mode.setChecked(self.continuous_scroll)

        if self._rebuild_debounce_timer is None:
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

    def scroll_page(self, direction: int) -> None:
        bar = self.scroll.verticalScrollBar()
        step = self.scroll.viewport().height() * 0.9
        bar.setValue(bar.value() + (direction * step))

    def scroll_page_length(self, direction: int) -> None:
        """
        Scrolls one page down (direction 1) or up (-1); outside continuous scroll, turns
        the page.
        """
        if not self.continuous_scroll and self.view_mode == ViewMode.IMAGE:
            if direction > 0:
                self.next_view()
            else:
                self.prev_view()
            return

        scale = self.calculate_scale()
        base_h = self._cached_base_size[1] if self._cached_base_size else 842
        spacing = (
            self.scroll_layout.spacing()
            if self.scroll_layout
            else 0
        )
        ph = int(base_h * scale) + spacing
        if ph <= 0:
            ph = self.scroll.viewport().height()

        bar = self.scroll.verticalScrollBar()
        bar.setValue(int(bar.value() + (direction * ph)))

    @staticmethod
    def _parse_internal_link(url: str) -> Optional[Tuple[int, Optional[float]]]:
        """
        Parses '#page=N' or '#page=N&top=Y' into a 0-based page index and an
        optional vertical target in PDF points (origin at the page bottom).
        """
        try:
            params = urllib.parse.parse_qs(url.lstrip("#"))
            page = int(float(params["page"][0])) - 1
            top = float(params["top"][0]) if "top" in params else None
            return page, top
        except (KeyError, ValueError, IndexError):
            return None

    def jump_to_page(self, idx: int, top_pts: Optional[float] = None) -> None:
        """
        Jumps to the given 0-based page. The page top is aligned with the top of
        the viewport, or, when top_pts is given, that position on the page is.
        """
        if not self.current_doc:
            return
        idx = max(0, min(self.current_doc.page_count - 1, idx))
        self.current_page_index = idx
        self.sidebar_nav.set_current_page(idx)
        self.txt_page.setText(str(idx + 1))
        if (
            not self.continuous_scroll
            or idx not in self.page_widgets
            or (
                self._virtual_enabled
                and (idx < self._virtual_range[0] or idx >= self._virtual_range[1])
            )
        ):
            self.rebuild_layout()
            self.current_page_index = idx

        self._flush_scroll_layout()
        page_top = self._page_top_y(idx)
        if page_top is None:
            self.ensure_visible(idx)
        else:
            offset = 0
            widget = self.page_widgets[idx]
            if (
                top_pts is not None
                and self._cached_base_size
                and self.rotation == 0
            ):
                page_h_pts = self._page_height_pts(idx)
                scale = widget.height() / max(1, self._cached_base_size[1])
                offset = max(0, int((page_h_pts - top_pts) * scale) - 12)
                offset = min(offset, max(0, widget.height() - 40))
            self._set_scroll_value_silently(max(0, page_top + offset - 8))

        self.current_page_index = idx
        self.update_view()
        if self.scroll:
            self.scroll.setFocus()

    def _page_height_pts(self, idx: int) -> float:
        """Returns a page's height in PDF points, falling back to the layout size."""
        if getattr(type(self.current_doc), "get_page_size", None) is not None:
            try:
                return float(self.current_doc.get_page_size(idx)[1])
            except Exception:
                log.debug("_page_height_pts failed", exc_info=True)
        return float(self._cached_base_size[1]) if self._cached_base_size else 842.0

    def on_page_input_return(self) -> None:
        if not self.current_doc:
            return
        try:
            num = int(self.txt_page.text().strip())
            if 1 <= num <= self.current_doc.page_count:
                self.navigate_to(num - 1)
            else:
                raise ValueError
        except ValueError:
            self.txt_page.setText(str(self.current_page_index + 1))

    def on_zoom_selected(self, idx: int) -> None:
        self.apply_zoom_string(self.combo_zoom.currentText())
        if self.scroll:
            self.scroll.setFocus()

    def on_zoom_text_entered(self) -> None:
        self.apply_zoom_string(self.combo_zoom.lineEdit().text())
        if self.scroll:
            self.scroll.setFocus()

    def apply_zoom_string(self, text: str) -> None:
        if "Auto" in text:
            self.zoom_mode = ZoomMode.AUTO_FIT
        elif "Width" in text:
            self.zoom_mode = ZoomMode.FIT_WIDTH
        elif "Height" in text:
            self.zoom_mode = ZoomMode.FIT_HEIGHT
        else:
            try:
                val = (
                    float(text.replace("%", "").strip())
                    if "%" in text
                    else float(text.strip())
                )
                self.manual_scale = max(
                    0.1, min(val / 100.0 if val > 5.0 else val, 5.0)
                )
                self.zoom_mode = ZoomMode.MANUAL
            except ValueError:
                pass
        self.on_zoom_changed_internal()

    def on_zoom_finished(self) -> None:
        """
        Fired when the user finishes zooming (debounce).
        Triggers a high-res re-render of the visible pages without destroying widgets.
        """
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)

        self.rendered_pages.clear()
        self.update_view()
        self._update_zoom_combo_text()

    def on_zoom_changed_internal(self) -> None:
        """Triggered by UI buttons, shortcuts, or explicit zoom changes."""
        self.settings.setValue("zoomMode", self.zoom_mode.value)
        self.settings.setValue("zoomScale", self.manual_scale)

        if (
            self.view_mode == ViewMode.REFLOW
            and self.web
        ):
            self.web.setZoomFactor(self.calculate_scale())

        target_page = self.current_page_index
        self.apply_visual_zoom()
        self.current_page_index = target_page
        self.txt_page.setText(str(target_page + 1))
        self._update_zoom_combo_text()

        if self._zoom_debounce_timer is None:
            self._zoom_debounce_timer = QTimer(self)
            self._zoom_debounce_timer.setSingleShot(True)
            self._zoom_debounce_timer.setInterval(250)
            self._zoom_debounce_timer.timeout.connect(self.on_zoom_finished)
        self._zoom_debounce_timer.start()

    def _update_zoom_combo_text(self) -> None:
        """Helper to update the UI combobox with the current scale."""
        current_scale = self.calculate_scale()
        pct = int(current_scale * 100)

        if self.zoom_mode == ZoomMode.AUTO_FIT:
            txt = f"Auto Fit ({pct}%)"
        elif self.zoom_mode == ZoomMode.FIT_WIDTH:
            txt = f"Fit Width ({pct}%)"
        elif self.zoom_mode == ZoomMode.FIT_HEIGHT:
            txt = f"Fit Height ({pct}%)"
        else:
            txt = f"{pct}%"

        self.combo_zoom.setCurrentText(txt)
        fm = self.combo_zoom.fontMetrics()
        max_width = fm.horizontalAdvance(txt) + 40
        self.combo_zoom.setFixedWidth(max(100, max_width))

    def _update_all_widget_sizes(self) -> None:
        w, h = self._get_target_page_size()
        for lbl in self.page_widgets.values():
            lbl.setFixedSize(w, h)

    def zoom_step(self, factor: float) -> None:
        self.manual_scale = max(0.1, min(self.calculate_scale() * factor, 5.0))
        self.zoom_mode = ZoomMode.MANUAL
        self.on_zoom_changed_internal()

    def _map_to_unrotated(self, rx: float, ry: float) -> Tuple[float, float]:
        """Maps a point on the rotated page to the unrotated pixmap."""
        rotation = self.rotation
        if rotation == 90:
            return ry, 1.0 - rx
        elif rotation == 180:
            return 1.0 - rx, 1.0 - ry
        elif rotation == 270:
            return 1.0 - ry, rx
        return rx, ry

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)

        if self.lbl_toast is not None and self.lbl_toast.isVisible():
            self.lbl_toast.move(
                (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
            )

        self._schedule_fit_refresh()

    def _schedule_fit_refresh(self) -> None:
        """Re-fits the pages shortly after the space for them changes, in the fit zoom modes."""
        if (
            self.current_doc
            and self.view_mode == ViewMode.IMAGE
        ):
            if self.zoom_mode in (
                ZoomMode.FIT_WIDTH,
                ZoomMode.FIT_HEIGHT,
                ZoomMode.AUTO_FIT,
            ):
                if self._resize_timer is None:
                    self._resize_timer = QTimer(self)
                    self._resize_timer.setSingleShot(True)
                    self._resize_timer.setInterval(150)
                    self._resize_timer.timeout.connect(self.on_zoom_changed_internal)
                self._resize_timer.start()

    def toggle_autoscroll(self, direction: int) -> None:
        """Toggles the document auto-scrolling state and direction."""
        self.autoscroll_dir = direction

        if direction == 1:
            self.btn_autoscroll_up.setChecked(False)
            if self.btn_autoscroll_down.isChecked():
                self.autoscroll_timer.start()
            else:
                self.autoscroll_timer.stop()
        else:
            self.btn_autoscroll_down.setChecked(False)
            if self.btn_autoscroll_up.isChecked():
                self.autoscroll_timer.start()
            else:
                self.autoscroll_timer.stop()

    def _do_autoscroll(self) -> None:
        """Executes the incremental auto-scroll tick."""
        vbar = self.scroll.verticalScrollBar()
        speed = self.settings.value("app/autoscroll_speed", 5, type=int)
        new_val = vbar.value() + (speed * self.autoscroll_dir)

        if new_val >= vbar.maximum() and self.autoscroll_dir == 1:
            vbar.setValue(vbar.maximum())
            self.btn_autoscroll_down.setChecked(False)
            self.autoscroll_timer.stop()

        elif new_val <= 0 and self.autoscroll_dir == -1:
            vbar.setValue(0)
            self.btn_autoscroll_up.setChecked(False)
            self.autoscroll_timer.stop()

        else:
            vbar.setValue(new_val)

    def _update_scroller_state(self) -> None:
        """Stops left-button drags from scrolling the page while an annotation tool is active."""
        if not self.scroll:
            return

        is_annotating = (
            self.anno_toolbar.isVisible()
            and self.current_tool != "nav"
        )

        if is_annotating:
            QScroller.ungrabGesture(self.scroll.viewport())
        else:
            QScroller.grabGesture(
                self.scroll.viewport(),
                QScroller.ScrollerGestureType.LeftMouseButtonGesture,
            )

    on_page_text_submitted = on_page_input_return

    def _reading_position(self) -> Optional[Tuple[int, float]]:
        """Where the reader is: (page index, fraction of that page above the viewport top)."""
        if not self.current_doc:
            return None
        idx = self.current_page_index
        top = self._page_top_y(idx)
        widget = self.page_widgets.get(idx)
        if top is None or widget is None or widget.height() <= 0:
            return idx, 0.0
        value = self.scroll.verticalScrollBar().value()
        return idx, max(0.0, min(1.0, (value - top) / widget.height()))

    def _restore_position(self, position: Tuple[int, float]) -> None:
        idx, fraction = position
        self.jump_to_page(idx)
        widget = self.page_widgets.get(idx)
        top = self._page_top_y(idx)
        if widget is not None and top is not None and fraction > 0:
            self._set_scroll_value_silently(int(top + fraction * widget.height()))
            self.update_view()

    def navigate_to(self, idx: int, top_pts: Optional[float] = None) -> None:
        """Jumps to a page and remembers where the reader was, for go_back."""
        here = self._reading_position()
        if here is not None:
            back = self.__dict__.setdefault("_nav_back", [])
            if not back or back[-1] != here:
                back.append(here)
                del back[:-NAV_HISTORY_LIMIT]
            self._nav_forward = []
        self.jump_to_page(idx, top_pts)
        self._update_nav_buttons()

    def go_back(self) -> None:
        """Returns to where the reader was before the last link, outline or page jump."""
        back = self._nav_back
        if not back:
            return
        here = self._reading_position()
        if here is not None:
            self.__dict__.setdefault("_nav_forward", []).append(here)
        self._restore_position(back.pop())
        self._update_nav_buttons()

    def go_forward(self) -> None:
        forward = self._nav_forward
        if not forward:
            return
        here = self._reading_position()
        if here is not None:
            self.__dict__.setdefault("_nav_back", []).append(here)
        self._restore_position(forward.pop())
        self._update_nav_buttons()

    def reset_navigation_history(self) -> None:
        self._nav_back = []
        self._nav_forward = []
        self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        self.btn_nav_back.setEnabled(bool(self._nav_back))
        self.btn_nav_forward.setEnabled(bool(self._nav_forward))
