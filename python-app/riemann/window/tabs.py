"""
Tab switching, closing, titles and tooltips, the tab context menu, and drag and drop onto the window.

Part of RiemannWindow (riemann/app.py), split out by concern; the methods use
the window's attributes freely, as they did when they lived in one class.
"""

import os

import shiboken6
from PySide6.QtCore import (
    QUrl,
)
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
)
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
)
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QTabWidget,
)
from ..ui.browser import BrowserTab, is_local_homepage
from ..ui.components import (
    build_open_with_menu,
)
from ..ui.explorer import FolderHomeTab
from ..ui.reader.tab import ReaderTab
from ..ui.viewers import VIEWER_TABS


class TabsMixin:
    def next_tab(self):
        """
        Advances the focus to the next tab in the main tab widget, wrapping around if necessary.
        """
        idx = self.tabs_main.currentIndex()
        if idx < self.tabs_main.count() - 1:
            self.tabs_main.setCurrentIndex(idx + 1)
        else:
            self.tabs_main.setCurrentIndex(0)

    def prev_tab(self):
        """
        Reverts the focus to the previous tab in the main tab widget, wrapping around if necessary.
        """
        idx = self.tabs_main.currentIndex()
        if idx > 0:
            self.tabs_main.setCurrentIndex(idx - 1)
        else:
            self.tabs_main.setCurrentIndex(self.tabs_main.count() - 1)

    def may_close(self, widget) -> bool:
        """Asks about a reader's page edits waiting for Save, or lets a web page with unsaved changes ask."""
        if isinstance(widget, ReaderTab):
            return widget.resolve_page_edits()
        if isinstance(widget, BrowserTab):
            return widget.confirm_leave() and shiboken6.isValid(widget)
        return True

    def close_tab(self, index: int) -> None:
        """Closes a main tab once its reader edits or web page allow it."""
        widget = self.tabs_main.widget(index)
        if not self.may_close(widget):
            return
        index = self.tabs_main.indexOf(widget) if widget is not None else index
        if index == -1:
            return
        if widget is not None and widget is getattr(self, "preview_tab_widget", None):
            self.preview_tab_widget = None
        if widget:
            self._record_closed_tab(widget)

            if isinstance(widget, BrowserTab):
                widget.web.triggerPageAction(QWebEnginePage.WebAction.Stop)
                if widget.web.page():
                    widget.web.page().setAudioMuted(True)
                    widget.web.page().deleteLater()
                widget.web.deleteLater()
            elif isinstance(widget, (ReaderTab,) + VIEWER_TABS):
                widget.cleanup()
            elif getattr(widget, "is_system_tab", False):
                widget.release()

        self.tabs_main.removeTab(index)
        try:
            if shiboken6.isValid(widget):
                widget.deleteLater()
        except RuntimeError:
            pass
        self._check_all_tabs_closed()
        self._auto_save_session()

    def close_side_tab(self, index: int) -> None:
        """Closes a side tab once its reader edits or web page allow it."""
        widget = self.tabs_side.widget(index)
        if not self.may_close(widget):
            return
        index = self.tabs_side.indexOf(widget) if widget is not None else index
        if index == -1:
            return
        if widget is not None and widget is getattr(self, "preview_tab_widget", None):
            self.preview_tab_widget = None
        if widget:
            self._record_closed_tab(widget)

            if isinstance(widget, BrowserTab):
                widget.web.triggerPageAction(QWebEnginePage.WebAction.Stop)
                if widget.web.page():
                    widget.web.page().setAudioMuted(True)
                    widget.web.page().deleteLater()
            elif isinstance(widget, (ReaderTab,) + VIEWER_TABS):
                widget.cleanup()
            elif getattr(widget, "is_system_tab", False):
                widget.release()

            if widget == getattr(self, "tree_signatures", None):
                active_main = self.tabs_main.currentWidget()
                if active_main:
                    active_main._sig_panel_dismissed = True
            else:
                try:
                    if shiboken6.isValid(widget):
                        widget.deleteLater()
                except RuntimeError:
                    pass

        self.tabs_side.removeTab(index)
        if self.tabs_side.count() == 0:
            self.tabs_side.hide()
        self._update_layout_button_icons()
        self._check_all_tabs_closed()
        self._auto_save_session()

    def _check_all_tabs_closed(self) -> None:
        """
        Checks if all tabs are closed and exits fullscreen if necessary.
        """
        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if getattr(self, "_reader_fullscreen", False):
                self.exit_fullscreen()

    def _handle_escape(self) -> None:
        """
        Handles the Escape key event to exit fullscreen.
        """
        if getattr(self, "_reader_fullscreen", False):
            self.exit_fullscreen()

    def close_active_tab(self) -> None:
        """
        Closes the tab currently holding focus.
        """
        focus_widget = QApplication.focusWidget()
        target = None
        curr = focus_widget

        while curr:
            if curr == self.tabs_main:
                target = self.tabs_main
                break
            elif curr == self.tabs_side:
                target = self.tabs_side
                break
            curr = curr.parent()

        if not target:
            target = (
                self.tabs_main
                if not (self.tabs_side.isVisible() and self.tabs_side.count() > 0)
                else self.tabs_main
            )

        idx = target.currentIndex()
        if idx != -1:
            if target == self.tabs_main:
                self.close_tab(idx)
            else:
                self.close_side_tab(idx)

    def _update_tab_title(self, browser, *args):
        title = browser.web.title()
        if not title:
            url_str = browser.web.url().toString()
            if url_str and url_str != "about:blank":
                title = QUrl(url_str).host()
            else:
                title = "New Tab"
        idx = self.tabs_main.indexOf(browser)
        if idx != -1:
            self.tabs_main.setTabText(idx, title)
            self._update_tab_tooltip(self.tabs_main, idx)
            self._update_window_title()
            return

        if self.tabs_side.isVisible():
            idx = self.tabs_side.indexOf(browser)
            if idx != -1:
                self.tabs_side.setTabText(idx, title)
                self._update_tab_tooltip(self.tabs_side, idx)
                self._update_window_title()

    def _update_tab_tooltip(self, tab_widget: QTabWidget, idx: int) -> None:
        """
        Updates the tab tooltip with full name and full file location for PDFs,
        or full title and top level subdomain for browser pages.
        """
        if idx < 0 or idx >= tab_widget.count():
            return
        widget = tab_widget.widget(idx)
        if not widget:
            return

        if isinstance(widget, ReaderTab) or hasattr(widget, "current_path"):
            path = getattr(widget, "current_path", None)
            if path:
                filename = os.path.basename(path)
                abs_path = os.path.abspath(path)
                doc_title = None
                if hasattr(widget, "document_metadata") and isinstance(
                    widget.document_metadata, dict
                ):
                    doc_title = widget.document_metadata.get("title")

                if doc_title and doc_title.strip() and doc_title.strip() != filename:
                    tooltip = (
                        f"{doc_title.strip()}\nFile: {filename}\nLocation: {abs_path}"
                    )
                else:
                    tooltip = f"{filename}\nLocation: {abs_path}"
            else:
                tooltip = "New PDF Document"

        elif isinstance(widget, BrowserTab) or hasattr(widget, "web"):
            title = widget.web.title() if hasattr(widget, "web") else ""
            url = widget.web.url() if hasattr(widget, "web") else QUrl()

            if is_local_homepage(url):
                subdomain = "Start Page"
                display_name = title or "Riemann Home"
            elif url.isLocalFile():
                subdomain = "Local File"
                display_name = title or os.path.basename(url.toLocalFile())
            else:
                host = url.host() if hasattr(url, "host") else ""
                subdomain = host or "Web"
                display_name = title or host or "Web Page"

            if display_name and subdomain and display_name != subdomain:
                tooltip = f"{display_name}\nDomain: {subdomain}"
            else:
                tooltip = display_name or subdomain

        elif isinstance(widget, FolderHomeTab) or hasattr(widget, "current_path"):
            path = getattr(widget, "current_path", "Workspace")
            tooltip = f"Workspace\nLocation: {path}"
        else:
            tooltip = tab_widget.tabText(idx)

        if getattr(widget, "is_preview", False) and getattr(
            widget, "current_path", None
        ):
            tooltip += "\n\nPreview mode: double-click the tab title to open it fully."
        tab_widget.setTabToolTip(idx, tooltip)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        """Opens dropped files of supported types in new tabs."""
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isdir(path):
                    self.open_folder(path)
                elif os.path.isfile(path):
                    self._open_document(path)
        event.acceptProposedAction()

    def _show_tab_context_menu(self, pos, tab_widget: QTabWidget) -> None:
        idx = tab_widget.tabBar().tabAt(pos)
        if idx == -1:
            return

        widget = tab_widget.widget(idx)
        menu = QMenu(self)

        rename_action = menu.addAction("Rename Tab (Custom)")
        revert_action = menu.addAction("Revert to Original Name")
        meta_action = None

        if hasattr(widget, "document_metadata") and widget.document_metadata.get(
            "title"
        ):
            meta_title = widget.document_metadata["title"]
            meta_action = menu.addAction(f"Rename to '{meta_title[:30]}...'")

        menu.addSeparator()
        action_duplicate = menu.addAction("Duplicate Tab")
        action_other_pane = None
        if widget is not self.tree_signatures:
            action_other_pane = menu.addAction(
                "Move to Other Pane (Split View)"
                if tab_widget is self.tabs_main
                else "Move Back to Main Pane"
            )
        action_open_full = None
        if getattr(widget, "is_preview", False) and getattr(
            widget, "current_path", None
        ):
            action_open_full = menu.addAction("Open in Full Mode")

        external_target = self._external_target(widget)
        if external_target:
            menu.addMenu(
                build_open_with_menu(
                    menu, external_target, "Open in External Application ↗"
                )
            )

        menu.addSeparator()
        action_close = menu.addAction("Close Tab")
        action_close_other = menu.addAction("Close Other Tabs")
        action_close_right = menu.addAction("Close Tabs to the Right")

        action = menu.exec(tab_widget.tabBar().mapToGlobal(pos))

        if not action:
            return

        if action_other_pane is not None and action == action_other_pane:
            self.move_tab_to_other_pane(tab_widget, idx)
            return
        if action_open_full is not None and action == action_open_full:
            self._replace_tab_document(tab_widget, idx, preview=False)
            return

        if action == rename_action:
            current_name = tab_widget.tabText(idx)
            new_name, ok = QInputDialog.getText(
                self, "Rename Tab", "Enter new tab name:", text=current_name
            )
            if ok and new_name.strip():
                tab_widget.setTabText(idx, new_name.strip())
                self._update_window_title()

        elif action == revert_action:
            if hasattr(widget, "current_path") and widget.current_path:
                original_name = os.path.basename(widget.current_path)
                tab_widget.setTabText(idx, original_name)
                self._update_window_title()
            elif hasattr(widget, "web") and hasattr(widget.web, "title"):
                original_name = widget.web.title()
                if not original_name:
                    original_name = "New Tab"
                tab_widget.setTabText(idx, original_name)
                self._update_window_title()
            elif hasattr(widget, "view") and hasattr(widget.view, "title"):
                original_name = widget.view.title()
                if not original_name:
                    original_name = "New Tab"
                tab_widget.setTabText(idx, original_name)
                self._update_window_title()

        elif meta_action and action == meta_action:
            tab_widget.setTabText(idx, widget.document_metadata["title"])
            self._update_window_title()

        elif action == action_close:
            if tab_widget == self.tabs_main:
                self.close_tab(idx)
            else:
                self.close_side_tab(idx)

        elif action == action_close_other:
            for i in range(tab_widget.count() - 1, -1, -1):
                if i != idx:
                    if tab_widget == self.tabs_main:
                        self.close_tab(i)
                    else:
                        self.close_side_tab(i)

        elif action == action_close_right:
            for i in range(tab_widget.count() - 1, idx, -1):
                if tab_widget == self.tabs_main:
                    self.close_tab(i)
                else:
                    self.close_side_tab(i)

        elif action == action_duplicate:
            if hasattr(widget, "current_path") and widget.current_path:
                self._open_document(
                    widget.current_path,
                    tab_widget,
                    preview=bool(getattr(widget, "is_preview", False)),
                )
            elif hasattr(widget, "web"):
                self._add_browser_tab(widget.web.url().toString(), tab_widget)

    def _on_tab_bar_double_clicked(self, tab_widget: QTabWidget, idx: int) -> None:
        """Double-clicking a preview tab's title opens it in full mode."""
        if idx < 0:
            return
        widget = tab_widget.widget(idx)
        if widget is not None and getattr(widget, "is_preview", False):
            self._replace_tab_document(tab_widget, idx, preview=False)
