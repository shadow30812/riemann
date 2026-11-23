"""
Split view, vertical and collapsed tab bars, the AI assistant panel and the folder panel.

Part of RiemannWindow (riemann/app.py), split out by concern; the methods use
the window's attributes freely, as they did when they lived in one class.

Notes:

toggle_split_view: Keep something in the main pane after the tab moves over.

_refresh_split_style: This runs on every tab change, including the swaps of a
tab being dragged. Re-applying a stylesheet re-polishes the whole pane and
resets the tab bar mid-drag, so only apply changes.

_apply_vertical_tabs: Only the main pane gets the vertical strip: the split-
view pane is usually narrow and keeps its compact horizontal tabs.
"""


import shiboken6
from PySide6.QtCore import (
    QTimer,
)
from PySide6.QtGui import (
    QIcon,
)
from PySide6.QtWidgets import (
    QTabWidget,
    QWidget,
)

from ..core.assets import icon_path
from ..core.settings import TABS_COLLAPSED_KEY, VERTICAL_TABS_KEY
from ..ui.palette import tokens


class PanesMixin:
    def _split_view_active(self) -> bool:
        """True when the side pane is showing at least one real tab."""
        if self.tabs_side.isHidden():
            return False
        return any(
            self.tabs_side.widget(i) is not self.tree_signatures
            for i in range(self.tabs_side.count())
        )

    def _move_tab_between(
        self, source: QTabWidget, idx: int, target: QTabWidget
    ) -> QWidget | None:
        """Moves a tab (with its title, icon and tooltip) to the other pane."""
        widget = source.widget(idx)
        if widget is None:
            return None
        text = source.tabText(idx)
        icon = source.tabIcon(idx)
        tooltip = source.tabToolTip(idx)
        source.removeTab(idx)

        if target is self.tabs_side and self.tabs_side.isHidden():
            self.tabs_side.show()
            self._split_evenly()
            QTimer.singleShot(0, self, self._split_evenly)

        new_idx = target.addTab(widget, icon, text)
        target.setTabToolTip(new_idx, tooltip)
        target.setCurrentIndex(new_idx)
        widget.show()
        widget.setFocus()
        self._active_pane = target

        if source is self.tabs_side and self.tabs_side.count() == 0:
            self.tabs_side.hide()
        self._update_layout_button_icons()
        return widget

    def _split_evenly(self) -> None:
        """Gives the main and side panes equal width."""
        total = self.splitter.width()
        if total > 0 and not self.tabs_side.isHidden():
            self.splitter.setSizes([total // 2, total - total // 2])

    def move_tab_to_other_pane(self, tab_widget: QTabWidget, idx: int) -> None:
        """Sends a tab to the other half of the split view, opening it if needed."""
        if getattr(self, "is_folder_session_active", False):
            self.show_toast("Split view is disabled during an active folder session.")
            return
        target = self.tabs_side if tab_widget is self.tabs_main else self.tabs_main
        if tab_widget is self.tabs_main and self.tabs_main.count() == 1:
            self.new_pdf_tab()
            idx = 0
        self._move_tab_between(tab_widget, idx, target)

    def close_split_view(self) -> None:
        """Moves every side-pane tab back into the main pane and hides the side pane."""
        idx = 0
        while idx < self.tabs_side.count():
            if self.tabs_side.widget(idx) is self.tree_signatures:
                idx += 1
                continue
            self._move_tab_between(self.tabs_side, idx, self.tabs_main)
        if self.tabs_side.count() == 0:
            self.tabs_side.hide()
        self._update_layout_button_icons()

    def toggle_split_view(self) -> None:
        """
        Opens the split view by moving the current tab into a second pane next
        to the main one, or closes it by merging the side pane back.
        """
        if getattr(self, "is_folder_session_active", False):
            self.show_toast("Split view is disabled during an active folder session.")
            self._update_layout_button_icons()
            return

        if self._split_view_active():
            self.close_split_view()
            return

        current = self.tabs_main.currentWidget()
        if not current:
            self._update_layout_button_icons()
            return
        if self.tabs_main.count() == 1:
            self.new_pdf_tab()
            self.tabs_main.setCurrentWidget(current)

        self._move_tab_between(
            self.tabs_main, self.tabs_main.indexOf(current), self.tabs_side
        )

    def _update_layout_button_icons(self) -> None:
        """Syncs the split/vertical-tab toolbar buttons with the current layout."""
        if not hasattr(self, "btn_split_view"):
            return
        dark = getattr(self, "dark_mode", False)
        for btn, name in (
            (self.btn_split_view, "columns-2"),
            (self.btn_vertical_tabs, "panel-left"),
            (self.btn_collapse_tabs, "list-collapse"),
        ):
            btn.setIcon(QIcon(icon_path(f"{name}.svg", dark)))
        for btn, name in (
            (self.btn_toggle_folder, "folder"),
            (self.btn_ai_sidecar, "sparkles"),
        ):
            btn.setIcon(QIcon(icon_path(f"{name}.svg", dark)))
        self.btn_toggle_folder.setChecked(self.explorer_panel.isVisible())
        self.btn_ai_sidecar.setChecked(self.sidecar_panel.isVisible())
        self.btn_split_view.setChecked(self._split_view_active())
        self._refresh_split_style()

    def _resize_side_panel(self, index: int, width: int) -> None:
        """Gives the main splitter's panel at index (explorer or assistant) width, taking it from the tabs."""
        sizes = self.main_splitter.sizes()
        spare = sizes[1] + sizes[index]
        sizes[index] = width
        sizes[1] = max(200, spare - width)
        self.main_splitter.setSizes(sizes)

    def toggle_ai_sidecar(self) -> None:
        """Shows or hides the AI assistant beside the documents."""
        if self.sidecar_panel.isVisible():
            self._assistant_width = self.main_splitter.sizes()[2]
            self.sidecar_panel.hide()
        else:
            self.sidecar_panel.set_document(self._active_pane_widget())
            self.sidecar_panel.show()
            self._resize_side_panel(2, getattr(self, "_assistant_width", 0) or 400)
            self.sidecar_panel.input.setFocus()
        self._update_layout_button_icons()

    def _sync_assistant_document(self, *_args) -> None:
        panel = getattr(self, "sidecar_panel", None)
        if panel is not None and panel.isVisible():
            self.sidecar_panel.set_document(self._active_pane_widget())

    def _on_sidecar_page_jump(self, page_idx: int) -> None:
        reader = self.sidecar_panel.reader
        if (
            reader is not None
            and shiboken6.isValid(reader)
            and hasattr(reader, "navigate_to")
        ):
            reader.navigate_to(page_idx)

    def toggle_folder_view(self) -> None:
        """Collapses or restores the folder explorer without ending the folder session."""
        if not getattr(self, "is_folder_session_active", False):
            self.open_folder()
            return

        if self.explorer_panel.isVisible():
            self._explorer_width = self.main_splitter.sizes()[0]
            self.explorer_panel.hide()
        else:
            self.explorer_panel.show()
            self._resize_side_panel(0, getattr(self, "_explorer_width", 0) or 250)
        self._sync_folder_toggle()

    def _sync_folder_toggle(self) -> None:
        shown = self.explorer_panel.isVisible()
        session = getattr(self, "is_folder_session_active", False)
        self.btn_toggle_folder.setVisible(session)
        self.btn_toggle_folder.setChecked(shown)
        self.action_toggle_folder.setChecked(shown)

    def _on_focus_changed(self, _old, new) -> None:
        widget = new
        while widget is not None:
            if widget is self.tabs_side or widget is self.tabs_main:
                if widget is not self._active_pane:
                    self._active_pane = widget
                    self._refresh_split_style()
                    self._sync_assistant_document()
                return
            widget = widget.parentWidget()

    def _active_pane_widget(self) -> QWidget | None:
        pane = self._active_pane if self._split_view_active() else self.tabs_main
        return pane.currentWidget()

    def _focus_pane(self, pane: QTabWidget) -> None:
        self._active_pane = pane
        if pane.currentWidget() is not None:
            pane.currentWidget().setFocus()
        self._refresh_split_style()

    def _close_pane(self, pane: QTabWidget) -> None:
        """The badge's close button: ends the split, keeping the other pane's tab in front."""
        keep = (
            self.tabs_main if pane is self.tabs_side else self.tabs_side
        ).currentWidget()
        self.close_split_view()
        if keep is not None and self.tabs_main.indexOf(keep) != -1:
            self.tabs_main.setCurrentWidget(keep)

    def _refresh_split_style(self) -> None:
        """
        In split view each pane becomes a rounded card; the one being used gets
        an accent outline and the other shows a small badge naming its tab.
        """
        split = self._split_view_active()
        t = tokens(getattr(self, "dark_mode", False))
        for pane in (self.tabs_main, self.tabs_side):
            active = pane is self._active_pane
            if split:
                color = t["accent"] if active else t["border_strong"]
                sheet = (
                    f"QTabWidget#{pane.objectName()}::pane {{ border: 2px solid {color};"
                    f" border-radius: 10px; top: -1px; }}"
                )
            else:
                sheet = ""
            if pane.styleSheet() != sheet:
                pane.setStyleSheet(sheet)
            badge = self.pane_badges[pane]
            badge.update_theme(t)
            if split and not active and pane.count():
                badge.show_for(
                    pane.tabText(pane.currentIndex()), pane.tabIcon(pane.currentIndex())
                )
            else:
                badge.hide()

    def _apply_vertical_tabs(self, vertical: bool) -> None:
        self.tabs_main.set_vertical_tabs(vertical)
        self.tabs_main.setProperty("verticalTabs", vertical)
        self.tabs_main.style().unpolish(self.tabs_main)
        self.tabs_main.style().polish(self.tabs_main)

    def set_vertical_tabs(self, vertical: bool) -> None:
        """Switches the tab strip between the top edge and a vertical left column."""
        vertical = bool(vertical)
        self.settings.setValue(VERTICAL_TABS_KEY, vertical)
        self._apply_vertical_tabs(vertical)
        for control in (
            getattr(self, "action_vertical_tabs", None),
            getattr(self, "btn_vertical_tabs", None),
        ):
            if control is not None and control.isChecked() != vertical:
                control.blockSignals(True)
                control.setChecked(vertical)
                control.blockSignals(False)

    def _restore_tab_bars(self) -> None:
        """Shows the tab strips again, unless the user has collapsed them."""
        visible = not getattr(self, "_tabs_collapsed", False)
        self.tabs_main.tabBar().setVisible(visible)
        self.tabs_side.tabBar().setVisible(visible)

    def set_tabs_collapsed(self, collapsed: bool) -> None:
        """
        Hides or shows the tab strip of both panes: the row on top, or the
        column on the left with vertical tabs. Tabs still switch with
        Ctrl+Tab and Ctrl+Shift+Tab while collapsed.
        """
        collapsed = bool(collapsed)
        self._tabs_collapsed = collapsed
        self.settings.setValue(TABS_COLLAPSED_KEY, collapsed)
        if getattr(self, "_fullscreen_state", 0) == 0:
            self._restore_tab_bars()
        for control in (
            getattr(self, "action_collapse_tabs", None),
            getattr(self, "btn_collapse_tabs", None),
        ):
            if control is not None and control.isChecked() != collapsed:
                control.blockSignals(True)
                control.setChecked(collapsed)
                control.blockSignals(False)

    def toggle_tabs_collapsed(self) -> None:
        self.set_tabs_collapsed(not getattr(self, "_tabs_collapsed", False))

    def open_url_beside(self, url: str) -> None:
        """Opens a web page in the side pane, next to what is being read."""
        if getattr(self, "is_folder_session_active", False):
            self.new_browser_tab(url)
            return
        was_hidden = self.tabs_side.isHidden()
        tab = self._add_browser_tab(url, self.tabs_side)
        self.tabs_side.show()
        self.tabs_side.setCurrentWidget(tab)
        if was_hidden:
            self._split_evenly()
        self._update_layout_button_icons()
