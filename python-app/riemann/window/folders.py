"""
Folder sessions: the folder tree, its home tab, and closing a folder.

Part of RiemannWindow (riemann/app.py), split out by concern; the methods use
the window's attributes freely, as they did when they lived in one class.
"""

import os
from PySide6.QtGui import (
    QIcon,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
)
from ..ui.explorer import FolderHomeTab
from ..core.assets import icon_path
from ..core.settings import get_dialog_directory, save_last_directory


class FoldersMixin:
    def open_folder(self, path=None):
        if not path:
            start_dir = get_dialog_directory(self.settings)
            path = QFileDialog.getExistingDirectory(self, "Open Folder", start_dir)
            if not path:
                return

        save_last_directory(self.settings, path)
        self.history_manager.add(path, "folder")

        if self.tabs_side.count() > 0:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Active Side Tabs")
            msg_box.setText(
                "Opening a folder session disables split view. How do you want to handle existing side tabs?"
            )
            btn_move = msg_box.addButton(
                "Move to Main", QMessageBox.ButtonRole.AcceptRole
            )
            btn_new_win = msg_box.addButton(
                "New Window", QMessageBox.ButtonRole.AcceptRole
            )
            btn_close = msg_box.addButton(
                "Close Side Tabs", QMessageBox.ButtonRole.DestructiveRole
            )
            msg_box.exec()

            if msg_box.clickedButton() == btn_move:
                while self.tabs_side.count() > 0:
                    widget = self.tabs_side.widget(0)
                    text = self.tabs_side.tabText(0)
                    icon = self.tabs_side.tabIcon(0)
                    self.tabs_side.removeTab(0)
                    self.tabs_main.addTab(widget, icon, text)

            elif msg_box.clickedButton() == btn_new_win:
                self.new_window()
                urls = []

                for i in range(self.tabs_side.count()):
                    w = self.tabs_side.widget(i)
                    if hasattr(w, "current_path") and w.current_path:
                        urls.append(w.current_path)
                    elif hasattr(w, "web"):
                        urls.append(w.web.url().toString())

                for i in range(self.tabs_side.count() - 1, -1, -1):
                    self.close_side_tab(i)
                for u in urls:
                    if u.endswith(".pdf") or u.endswith(".md"):
                        self._new_window_ref.new_pdf_tab(u)
                    else:
                        self._new_window_ref.new_browser_tab(u)

            elif msg_box.clickedButton() == btn_close:
                for i in range(self.tabs_side.count() - 1, -1, -1):
                    self.close_side_tab(i)

        self.tabs_side.hide()
        self.is_folder_session_active = True
        self.explorer_panel.set_path(path)
        self.explorer_panel.show()
        self._sync_folder_toggle()
        if hasattr(self, "show_toast"):
            self.show_toast(f"Folder session connected: {os.path.basename(path)}")

    def close_folder(self):
        """Exits the folder session and restores normal app behavior."""
        if not getattr(self, "is_folder_session_active", False):
            return

        self.explorer_panel.hide()
        self.is_folder_session_active = False
        self._sync_folder_toggle()

        preview = self._live_preview_tab()
        if preview is not None:
            for pane in (self.tabs_main, self.tabs_side):
                if pane.indexOf(preview) != -1:
                    pane.removeTab(pane.indexOf(preview))
            if hasattr(preview, "cleanup"):
                preview.cleanup()
            preview.deleteLater()
            self.preview_tab_widget = None

        if hasattr(self, "show_toast"):
            self.show_toast("Folder session closed.")

    def new_folder_home_tab(self, target_widget=None):
        """Spawns the workspace/folder dashboard."""
        if target_widget is None:
            target_widget = self.tabs_main

        tab = FolderHomeTab(self, self.dark_mode)
        idx = target_widget.addTab(tab, QIcon(icon_path("folder.png")), "Workspaces")
        target_widget.setCurrentIndex(idx)
