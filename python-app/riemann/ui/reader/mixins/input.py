"""
Keyboard, mouse, wheel, focus and drag-and-drop handling for the reader.

Part of ReaderTab (riemann/ui/reader/tab.py). ReaderTab lists this mixin before
QWidget, so its Qt event handlers override QWidget's.

Notes:

eventFilter: Clicks on links tolerate more hand jitter before they turn into a
text selection and stop following the link.
"""

import os
import time
from typing import Any
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRect,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QKeyEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QMenu,
    QRubberBand,
)
from ....core.constants import ViewMode, ZoomMode, clamp_zoom
from ....core.doctypes import TAB_KINDS, document_kind
from ...palette import CONTENT
from ..widgets import PageWidget
from .host import ReaderHostBase


class InputMixin(ReaderHostBase):
    _mouse_press_pos = None
    _scroll_accumulator = None
    _press_on_link = False

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() in (
            Qt.MouseButton.BackButton,
            Qt.MouseButton.ForwardButton,
        ):
            if event.button() == Qt.MouseButton.BackButton:
                self.go_back()
            else:
                self.go_forward()
            return True
        if self._autoscroll_active:
            if event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
            ):
                if event.type() == QEvent.Type.MouseButtonPress:
                    self.stop_middle_click_scroll()
                return True
            elif event.type() == QEvent.Type.MouseMove:
                cur = event.globalPosition().toPoint()
                dy = cur.y() - self._autoscroll_origin_global.y()
                dx = cur.x() - self._autoscroll_origin_global.x()
                dy_eff = dy - 8 if dy > 8 else (dy + 8 if dy < -8 else 0)
                dx_eff = dx - 8 if dx > 8 else (dx + 8 if dx < -8 else 0)
                self._autoscroll_speed_y = (dy_eff * 0.12) * (
                    1.0 + abs(dy_eff) * 0.003
                )
                self._autoscroll_speed_x = (dx_eff * 0.12) * (
                    1.0 + abs(dx_eff) * 0.003
                )
                if abs(dy) > 8 and abs(dy) >= abs(dx):
                    QApplication.changeOverrideCursor(Qt.CursorShape.SizeVerCursor)
                elif abs(dx) > 8 and abs(dx) > abs(dy):
                    QApplication.changeOverrideCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    QApplication.changeOverrideCursor(Qt.CursorShape.SizeAllCursor)
                return True
            elif (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape
            ):
                self.stop_middle_click_scroll()
                return True

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.MiddleButton:
                if self._autoscroll_active:
                    self.stop_middle_click_scroll()
                else:
                    self.start_middle_click_scroll(
                        event.globalPosition().toPoint()
                    )
                return True

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and source == self.scroll.viewport()
        ):
            self.clear_all_text_selections()

        if event.type() == QEvent.Type.Wheel:
            mod = event.modifiers()
            if mod & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.zoom_step(1.1)
                elif delta < 0:
                    self.zoom_step(0.9)
                return True

        if (
            self.anno_toolbar is not None
            and self.anno_toolbar.isVisible()
            and self.current_tool != "nav"
            and event.type() in (QEvent.Type.Wheel, QEvent.Type.NativeGesture)
        ):
            return True

        if event.type() == QEvent.Type.KeyPress:
            if source == self.scroll or isinstance(
                source, PageWidget
            ):
                if self._handle_selection_key(event):
                    return True
                if (
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    and event.key() == Qt.Key.Key_C
                    and self.current_selected_text
                ):
                    QApplication.clipboard().setText(self.current_selected_text)
                    self.show_toast("Copied text to clipboard! 📋")
                    return True
                key = event.key()
                if self.view_mode == ViewMode.IMAGE:
                    if key == Qt.Key.Key_Left:
                        self.prev_view()
                        return True
                    elif key == Qt.Key.Key_Right:
                        self.next_view()
                        return True

                    if not self.continuous_scroll:
                        vbar = self.scroll.verticalScrollBar()
                        if vbar.maximum() == 0:
                            if key == Qt.Key.Key_Up:
                                self.prev_view()
                                return True
                            elif key == Qt.Key.Key_Down:
                                self.next_view()
                                return True

                    if key == Qt.Key.Key_Up:
                        vbar = self.scroll.verticalScrollBar()
                        vbar.setValue(vbar.value() - 50)
                        return True
                    elif key == Qt.Key.Key_Down:
                        vbar = self.scroll.verticalScrollBar()
                        vbar.setValue(vbar.value() + 50)
                        return True
                    elif key == Qt.Key.Key_Space:
                        mod = event.modifiers()
                        self.scroll_page(
                            -1 if mod & Qt.KeyboardModifier.ShiftModifier else 1
                        )
                        return True

        if isinstance(source, PageWidget):
            page_idx = source.property("pageIndex")

            if self.is_snipping:
                if event.type() == QEvent.Type.MouseButtonPress:
                    self.snip_start = event.pos()
                    if not self.snip_band:
                        self.snip_band = QRubberBand(
                            QRubberBand.Shape.Rectangle, source
                        )
                    self.snip_band.setGeometry(
                        self.snip_start.x(), self.snip_start.y(), 0, 0
                    )
                    self.snip_band.show()
                    return True
                elif event.type() == QEvent.Type.MouseMove and self.snip_band:
                    self.snip_band.setGeometry(
                        QRect(self.snip_start, event.pos()).normalized()
                    )
                    return True
                elif event.type() == QEvent.Type.MouseButtonRelease and self.snip_band:
                    rect = self.snip_band.geometry()
                    self.snip_band.hide()
                    if rect.width() > 10 and rect.height() > 10:
                        self.process_snip(source, rect)
                    return True

            if (
                self.anno_toolbar is not None
                and self.anno_toolbar.isVisible()
                and self.current_tool != "nav"
            ):
                if event.type() == QEvent.Type.MouseButtonPress:
                    if self.current_tool == "note":
                        if self.handle_annotation_click(source, event):
                            return True
                        rx, ry = self._map_to_unrotated(
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        self.create_new_annotation(page_idx, rx, ry, type="note")
                        return True

                    elif self.current_tool == "text":
                        rx, ry = self._map_to_unrotated(
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        self.create_new_annotation(page_idx, rx, ry, type="text")
                        return True

                    elif self.current_tool in ("stamp_tick", "stamp_cross"):
                        rx, ry = self._map_to_unrotated(
                            event.pos().x() / source.width(),
                            event.pos().y() / source.height(),
                        )
                        self._add_anno_data(
                            page_idx,
                            {
                                "type": "stamp",
                                "subtype": self.current_tool,
                                "rel_pos": (rx, ry),
                                "color": self.pen_color,
                            },
                        )
                        return True

                    elif self.current_tool in (
                        "pen",
                        "highlight",
                        "markup_highlight",
                        "markup_underline",
                        "markup_strikeout",
                        "rect",
                        "oval",
                        "redact",
                    ):
                        self.active_drawing = [QPoint(event.pos().x(), event.pos().y())]
                        if self.current_tool.startswith("markup"):
                            self.current_markup_rects = []
                        return True

                    elif self.current_tool == "eraser":
                        self._handle_eraser_click(source, event.pos(), page_idx)
                        return True

                elif event.type() == QEvent.Type.MouseMove and self.active_drawing:
                    if self.current_tool.startswith("markup"):
                        rect = QRect(self.active_drawing[0], event.pos()).normalized()
                        preview_color = QColor(self.pen_color)
                        preview_color.setAlpha(100)
                        source.set_markup_preview([rect], preview_color)

                    elif self.current_tool in ("rect", "oval", "redact"):
                        self.active_drawing.append(
                            QPoint(event.pos().x(), event.pos().y())
                        )
                        redact = self.current_tool == "redact"
                        source.set_shape_preview(
                            self.active_drawing[0],
                            event.pos(),
                            "rect" if redact else self.current_tool,
                            CONTENT["redact_mark"] if redact else self.pen_color,
                            2 if redact else self.pen_thickness,
                        )

                    else:
                        self.active_drawing.append(
                            QPoint(event.pos().x(), event.pos().y())
                        )
                        source.set_temp_stroke(
                            self.active_drawing,
                            self.pen_color,
                            self.pen_thickness,
                            self.current_tool == "highlight",
                        )
                    return True

                elif event.type() == QEvent.Type.MouseButtonRelease:
                    if (
                        self.current_tool in ("pen", "highlight")
                        and self.active_drawing
                    ):
                        w, h = source.width(), source.height()
                        pts = []
                        for p in self.active_drawing:
                            pts.append(self._map_to_unrotated(p.x() / w, p.y() / h))

                        self._add_anno_data(
                            page_idx,
                            {
                                "type": "drawing",
                                "subtype": self.current_tool,
                                "points": pts,
                                "color": self.pen_color,
                                "thickness": self.pen_thickness,
                            },
                        )
                        source.clear_temp_stroke()
                        self.active_drawing = []
                        return True

                    elif self.current_tool in ("rect", "oval") and self.active_drawing:
                        w, h = source.width(), source.height()
                        p1 = self.active_drawing[0]
                        p2 = self.active_drawing[-1]
                        rx1, ry1 = self._map_to_unrotated(p1.x() / w, p1.y() / h)
                        rx2, ry2 = self._map_to_unrotated(p2.x() / w, p2.y() / h)
                        self._add_anno_data(
                            page_idx,
                            {
                                "type": "drawing",
                                "subtype": self.current_tool,
                                "points": [(rx1, ry1), (rx2, ry2)],
                                "color": self.pen_color,
                                "thickness": self.pen_thickness,
                            },
                        )
                        self.active_drawing = []
                        source.clear_temp_stroke()
                        return True

                    elif self.current_tool == "redact" and self.active_drawing:
                        w, h = source.width(), source.height()
                        p1 = self.active_drawing[0]
                        p2 = self.active_drawing[-1]
                        self.active_drawing = []
                        source.clear_temp_stroke()
                        if abs(p2.x() - p1.x()) > 2 and abs(p2.y() - p1.y()) > 2:
                            rx1, ry1 = self._map_to_unrotated(p1.x() / w, p1.y() / h)
                            rx2, ry2 = self._map_to_unrotated(p2.x() / w, p2.y() / h)
                            self._add_redaction_mark(page_idx, [[rx1, ry1, rx2, ry2]])
                        return True

                    elif self.current_tool.startswith("markup") and self.active_drawing:
                        rects, text = self._get_linear_text_selection(
                            page_idx, self.active_drawing[0], event.pos()
                        )

                        if rects:
                            w, h = source.width(), source.height()
                            normalized_rects = []
                            for r in rects:
                                rx1, ry1 = self._map_to_unrotated(
                                    r.left() / w, r.top() / h
                                )
                                rx2, ry2 = self._map_to_unrotated(
                                    r.right() / w, r.bottom() / h
                                )
                                normalized_rects.append([rx1, ry1, rx2, ry2])

                            self._add_anno_data(
                                page_idx,
                                {
                                    "type": "markup",
                                    "subtype": self.current_tool.replace("markup_", ""),
                                    "rects": normalized_rects,
                                    "text": text,
                                    "color": self.pen_color,
                                },
                            )
                        source.set_markup_preview([], QColor(Qt.GlobalColor.transparent))
                        self.active_drawing = []
                        return True

            elif (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                source.setFocus()
                self._mouse_press_pos = event.pos()
                self.is_selecting_text = False
                self.text_select_start = event.pos()
                self._press_on_link = any(
                    r.contains(event.pos()) for r, _ in getattr(source, "link_rects", [])
                )
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if self._extend_selection_to_pos(page_idx, event.pos()):
                        self._just_selected_multi_click = True
                        return True
                self._set_caret_at_pos(page_idx, event.pos())

                now = time.time()
                last_time = self._last_click_time
                last_pos = self._last_click_pos
                dt = now - last_time
                dist = (event.pos() - last_pos).manhattanLength()

                if dt < 0.55 and dist < 18:
                    self._click_count = self._click_count + 1
                else:
                    self._click_count = 1

                self._last_click_time = now
                self._last_click_pos = event.pos()

                if self._click_count >= 3:
                    self._click_count = 3
                    self._just_selected_multi_click = True
                    rects, text = self._get_paragraph_at_pos(
                        page_idx, event.pos()
                    )
                    if rects and text:
                        self.clear_all_text_selections()
                        source.set_text_selection(rects)
                        self.current_selected_text = text
                    return True

                return True

            elif (
                event.type() == QEvent.Type.MouseButtonDblClick
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._click_count = 2
                self._last_click_time = time.time()
                self._last_click_pos = event.pos()
                self._just_selected_multi_click = True
                rects, text = self._get_word_at_pos(page_idx, event.pos())
                if rects and text:
                    self.clear_all_text_selections()
                    source.set_text_selection(rects)
                    self.current_selected_text = text
                return True

            elif event.type() == QEvent.Type.MouseMove:
                if self._autoscroll_active:
                    return True
                if (
                    hasattr(source, "link_rects")
                    and not self.is_selecting_text
                    and not self.active_drawing
                    and not self.is_snipping
                ):
                    hovered_url = None
                    for r, url in source.link_rects:
                        if r.contains(event.pos()):
                            hovered_url = url
                            break

                    if hovered_url:
                        if hovered_url.startswith("#page="):
                            target = self._parse_internal_link(hovered_url)
                            page_label = target[0] + 1 if target else "?"
                            self.link_tooltip.setText(f"Jump to Page {page_label} ↗")
                            self._hover_internal_link(hovered_url, source, event.pos())
                        else:
                            self._hover_left_link()
                            self.link_tooltip.setText(hovered_url)
                        self.link_tooltip.adjustSize()
                        self.link_tooltip.move(
                            10, self.height() - self.link_tooltip.height() - 10
                        )
                        self.link_tooltip.show()
                        self.link_tooltip.raise_()

                        if hovered_url.startswith("#page=") or (
                            event.modifiers()
                            == Qt.KeyboardModifier.ControlModifier
                        ):
                            source.setCursor(Qt.CursorShape.PointingHandCursor)
                        else:
                            source.setCursor(Qt.CursorShape.IBeamCursor)
                    else:
                        self._hover_left_link()
                        hovered_comment = self._get_comment_at_pos(
                            page_idx, event.pos(), source.width(), source.height()
                        )
                        if hovered_comment:
                            author = hovered_comment.get("author") or "Comment"
                            contents = hovered_comment.get("contents", "")
                            preview = (
                                contents if len(contents) <= 90 else contents[:87] + "..."
                            )
                            self.link_tooltip.setText(f"💬 {author}: {preview}")
                            self.link_tooltip.adjustSize()
                            self.link_tooltip.move(
                                10, self.height() - self.link_tooltip.height() - 10
                            )
                            self.link_tooltip.show()
                            self.link_tooltip.raise_()
                            source.setCursor(Qt.CursorShape.PointingHandCursor)
                        else:
                            self.link_tooltip.hide()
                            source.setCursor(Qt.CursorShape.IBeamCursor)

                if (
                    (event.buttons() & Qt.MouseButton.LeftButton)
                    and self._mouse_press_pos is not None
                    and not self.active_drawing
                    and not self.is_snipping
                ):
                    if not self.is_selecting_text:
                        threshold = 14 if self._press_on_link else 5
                        if (
                            event.pos() - self._mouse_press_pos
                        ).manhattanLength() >= threshold:
                            self.is_selecting_text = True
                            self._just_selected_multi_click = False
                    if self.is_selecting_text:
                        rects, text = self._get_linear_text_selection(
                            page_idx, self.text_select_start, event.pos()
                        )
                        source.set_text_selection(rects)
                        self.current_selected_text = text
                return True

            elif event.type() == QEvent.Type.MouseButtonRelease:
                if (
                    event.button() == Qt.MouseButton.LeftButton
                    and not self.active_drawing
                    and not self.is_snipping
                    and not self.is_selecting_text
                ):
                    if hasattr(source, "link_rects"):
                        pos = event.pos()
                        for rect, url in source.link_rects:
                            if rect.contains(pos):
                                self.clear_all_text_selections()
                                if url.startswith("#page="):
                                    target = self._parse_internal_link(url)
                                    if target is not None:
                                        self.navigate_to(target[0], top_pts=target[1])
                                        return True
                                elif (
                                    event.modifiers()
                                    == Qt.KeyboardModifier.ControlModifier
                                ):
                                    main_win = self.window()
                                    if hasattr(main_win, "new_browser_tab"):
                                        main_win.new_browser_tab(url)
                                    else:
                                        QDesktopServices.openUrl(QUrl(url))
                                    return True

                if event.button() == Qt.MouseButton.LeftButton:
                    if self.is_selecting_text:
                        self.is_selecting_text = False
                        rects, text = self._get_linear_text_selection(
                            page_idx, self.text_select_start, event.pos()
                        )
                        source.set_text_selection(rects)
                        self.current_selected_text = text
                    elif self._just_selected_multi_click:
                        self._just_selected_multi_click = False
                    else:
                        comment = self._get_comment_at_pos(
                            page_idx, event.pos(), source.width(), source.height()
                        )
                        if comment:
                            self.clear_all_text_selections()
                            self.show_external_comment_dialog(comment)
                            return True

                        if self.handle_annotation_click(source, event):
                            self.clear_all_text_selections()
                            return True

                        self.clear_all_text_selections()
                return True

            elif (
                event.type() == QEvent.Type.KeyPress
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_C
            ):
                if self.current_selected_text:
                    QApplication.clipboard().setText(self.current_selected_text)
                    self.show_toast("Copied text to clipboard! 📋")
                return True

            elif event.type() == QEvent.Type.ContextMenu:
                if self.current_selected_text:
                    menu = QMenu(source)
                    copy_action = menu.addAction("Copy Text")
                    search_action = menu.addAction("Search Web")
                    menu.addSeparator()
                    replace_action = menu.addAction("Replace Text…")
                    redact_action = menu.addAction("Mark for Redaction")

                    action = menu.exec(event.globalPos())

                    if action == copy_action:
                        QApplication.clipboard().setText(self.current_selected_text)
                        self.show_toast("Copied text to clipboard! 📋")
                    elif action == search_action:
                        self._search_web_for_selected_text(self.current_selected_text)
                    elif action == redact_action:
                        self.mark_selection_for_redaction()
                    elif action == replace_action:
                        self.replace_selected_text()
                return True

        return super().eventFilter(source, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mod = event.modifiers()

        if self._handle_selection_key(event):
            event.accept()
            return

        if (
            mod & Qt.KeyboardModifier.ControlModifier
            and key == Qt.Key.Key_C
            and self.current_selected_text
        ):
            QApplication.clipboard().setText(self.current_selected_text)
            self.show_toast("Copied text to clipboard! 📋")
            event.accept()
            return

        if key == Qt.Key.Key_Escape:
            if self.is_reading_aloud():
                self.stop_read_aloud()
                event.accept()
                return
            if self._reader_fullscreen:
                if hasattr(self.window(), "exit_fullscreen"):
                    self.window().exit_fullscreen()
                event.accept()
                return

        if key == Qt.Key.Key_F11:
            self.toggle_reader_fullscreen()
            return

        if key == Qt.Key.Key_F5:
            self.reload_document()
            event.accept()
            return

        if mod == Qt.KeyboardModifier.NoModifier:
            if key == Qt.Key.Key_R:
                self.toggle_view_mode()
                event.accept()
                return
            elif key == Qt.Key.Key_C:
                self.toggle_scroll_mode()
                event.accept()
                return
            elif key == Qt.Key.Key_D:
                self.toggle_facing_mode()
                event.accept()
                return
            elif key == Qt.Key.Key_A:
                self.apply_zoom_string("Auto Fit")
                event.accept()
                return
            elif key == Qt.Key.Key_W:
                self.apply_zoom_string("Fit Width")
                event.accept()
                return
            elif key == Qt.Key.Key_H:
                self.apply_zoom_string("Fit Height")
                event.accept()
                return

            elif key == Qt.Key.Key_F:
                self.toggle_reader_fullscreen()
                event.accept()
                return

        if self.view_mode == ViewMode.IMAGE:
            if mod & Qt.KeyboardModifier.ControlModifier:
                if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    self.zoom_step(1.1)
                elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                    self.zoom_step(0.9)
                return

            if key == Qt.Key.Key_Right:
                self.next_view()
            elif key == Qt.Key.Key_Left:
                self.prev_view()
            elif key == Qt.Key.Key_PageDown:
                self.scroll_page_length(1)
                event.accept()
                return
            elif key == Qt.Key.Key_PageUp:
                self.scroll_page_length(-1)
                event.accept()
                return
            elif key == Qt.Key.Key_Space:
                self.scroll_page(-1 if mod & Qt.KeyboardModifier.ShiftModifier else 1)

            elif key == Qt.Key.Key_Up:
                if (
                    not self.continuous_scroll
                    and self.scroll.verticalScrollBar().maximum() == 0
                ):
                    self.prev_view()
                else:
                    self.scroll.verticalScrollBar().setValue(
                        self.scroll.verticalScrollBar().value() - 50
                    )
            elif key == Qt.Key.Key_Down:
                if (
                    not self.continuous_scroll
                    and self.scroll.verticalScrollBar().maximum() == 0
                ):
                    self.next_view()
                else:
                    self.scroll.verticalScrollBar().setValue(
                        self.scroll.verticalScrollBar().value() + 50
                    )

            elif key == Qt.Key.Key_Home:
                self.scroll.verticalScrollBar().setValue(0)
            elif key == Qt.Key.Key_End:
                self.scroll.verticalScrollBar().setValue(
                    self.scroll.verticalScrollBar().maximum()
                )

    def event(self, event: QEvent) -> bool:
        """Handles trackpad pinch-to-zoom."""
        if event.type() == QEvent.Type.NativeGesture:
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                delta = event.value()
                self.manual_scale = clamp_zoom(self.manual_scale * (1.0 + delta))
                self.zoom_mode = ZoomMode.MANUAL
                self.apply_visual_zoom()

                self._schedule_zoom_finished()
                return True
        return super().event(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if (
            self.anno_toolbar.isVisible()
            and self.current_tool != "nav"
        ):
            event.accept()
            return

        mod = event.modifiers()

        if mod & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_step(1.1)
            elif delta < 0:
                self.zoom_step(0.9)
            event.accept()
            return

        if mod & Qt.KeyboardModifier.AltModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                vbar = self.scroll.verticalScrollBar()
                vbar.setValue(vbar.value() - (delta * 3))
            event.accept()
            return

        if not self.continuous_scroll and self.view_mode == ViewMode.IMAGE:
            vbar = self.scroll.verticalScrollBar()
            if vbar.maximum() == 0:
                if self._scroll_accumulator is None:
                    self._scroll_accumulator = 0

                delta = event.angleDelta().y()
                self._scroll_accumulator += delta

                if self._scroll_accumulator >= 200:
                    self.prev_view()
                    self._scroll_accumulator = 0
                elif self._scroll_accumulator <= -200:
                    self.next_view()
                    self._scroll_accumulator = 0
                event.accept()
                return

        if self._scroll_accumulator is not None:
            self._scroll_accumulator = 0
        super().wheelEvent(event)

    def focusInEvent(self, event: Any) -> None:
        """
        Handles the event when the ReaderTab widget itself receives focus from the OS.
        Immediately forwards focus to the appropriate viewing canvas.
        """
        if self.view_mode == ViewMode.REFLOW:
            if self.web is not None:
                self.web.setFocus()
        else:
            self.scroll.setFocus()

        super().focusInEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        """
        Intercepts state changes, such as window activation from alt-tabbing,
        to ensure keyboard focus is restored to the correct canvas.
        """
        super().changeEvent(event)

        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            focus_widget = QApplication.focusWidget()
            if not isinstance(focus_widget, (QLineEdit, QComboBox)):
                if self.view_mode == ViewMode.REFLOW:
                    if self.web is not None:
                        self.web.setFocus()
                else:
                    self.setFocus()

    def focusNextPrevChild(self, next: bool) -> bool:
        """
        Preserves default Qt focus chaining without advancing PDF pages unexpectedly.
        """
        return super().focusNextPrevChild(next)

    def dragEnterEvent(self, event) -> None:
        """Accept drag if it contains local files."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        """Accept move if it contains local files."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        """Handle dropping files anywhere on the ReaderTab."""
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._on_file_dropped(url.toLocalFile())
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def _on_file_dropped(self, path: str) -> None:
        """Handles dropped file pathways."""
        if not path:
            return
        clean = QUrl(path).toLocalFile() if path.startswith("file://") else path
        abs_p = os.path.abspath(clean)
        if document_kind(abs_p) in TAB_KINDS:
            if not self.current_path:
                self.open_here(abs_p)
            else:
                main_win = self.window()
                if hasattr(main_win, "new_pdf_tab"):
                    main_win.new_pdf_tab(abs_p)
                else:
                    self.load_document(abs_p)
