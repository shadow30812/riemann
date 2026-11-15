"""
Saving and restoring windows and tabs: sessions, closed-tab history, and the window registry.

Part of RiemannWindow (riemann/app.py), split out by concern; the methods use
the window's attributes freely, as they did when they lived in one class.

Notes:

_auto_save_session: Crash recovery reads "session/windows" first, so the
periodic save has to refresh that record (for every window), not just the
legacy per-window keys; otherwise "Restore" brings back a stale session.

_save_all_windows_session: If this save is closing the last open window, merge
any valid recently closed windows.
"""

import os
import time
from typing import List, Optional
from PySide6.QtCore import (
    QUrl,
)
from PySide6.QtWidgets import (
    QMessageBox,
    QTabWidget,
    QWidget,
)
from ..ui.browser import BrowserTab, PreviewTextTab, is_local_homepage
from ..ui.explorer import FolderHomeTab
from ..ui.reader.tab import ReaderTab
from ..core.log import log
from PySide6.QtWidgets import QMainWindow
from ..ui.system import PAGES as SYSTEM_PAGES
from .common import window_class
from ..core.settings import app_settings


class SessionMixin:
    def _restore_session(self) -> None:
        """
        Restores the window geometry and open tabs from the previous session.
        Defaults to opening both PDF and browser homepages if no session exists or incognito is active
        UNLESS app is opened externally.
        """
        if getattr(self, "is_secondary_restoration", False):
            return

        if (
            getattr(self, "_was_unclean_exit", False)
            and not self.external_files
            and not self.incognito
            and getattr(self, "restore_session", True)
        ):
            saved_windows = self.settings.value("session/windows", None)
            main_saved = self.settings.value("session/main_tabs", [])
            side_saved = self.settings.value("session/side_tabs", [])

            has_tabs = bool(main_saved or side_saved)
            if not has_tabs and isinstance(saved_windows, list):
                for w_data in saved_windows:
                    if isinstance(w_data, dict) and (
                        w_data.get("main_tabs")
                        or w_data.get("side_tabs")
                        or w_data.get("active_folder")
                    ):
                        has_tabs = True
                        break

            if has_tabs:
                ret = QMessageBox.question(
                    self,
                    "Restore Last Opened Tabs",
                    "Riemann detected that the application did not close properly.\n\n"
                    "Would you like to restore your last opened tabs?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    self.settings.setValue("session/windows", [])
                    self.settings.setValue("session/main_tabs", [])
                    self.settings.setValue("session/side_tabs", [])
                    self.settings.setValue("session/main_active_idx", -1)
                    self.settings.setValue("session/side_active_idx", -1)
                    self.settings.setValue("session/active_folder", "")
                    self.settings.setValue("session/clean_exit", False)
                    self.settings.sync()

                    if not self.external_files:
                        self.new_pdf_tab()
                        self.new_browser_tab()
                    self.resize(1200, 900)
                    return

        if self.incognito or not self.restore_session:
            if not self.external_files:
                self.new_pdf_tab()
                self.new_browser_tab()
            self.resize(1200, 900)
            return

        saved_windows = self.settings.value("session/windows", None)
        if saved_windows and isinstance(saved_windows, list) and len(saved_windows) > 0:
            first_win_data = saved_windows[0]
            if first_win_data.get("geometry"):
                self.restoreGeometry(first_win_data["geometry"])  # type: ignore

            self._restore_tabs_from_items(
                first_win_data.get("main_tabs", []), self.tabs_main
            )
            self._restore_tabs_from_items(
                first_win_data.get("side_tabs", []), self.tabs_side
            )

            main_idx = first_win_data.get("main_active_idx", -1)
            if isinstance(main_idx, int) and 0 <= main_idx < self.tabs_main.count():
                self.tabs_main.setCurrentIndex(main_idx)

            side_idx = first_win_data.get("side_active_idx", -1)
            if isinstance(side_idx, int) and 0 <= side_idx < self.tabs_side.count():
                self.tabs_side.setCurrentIndex(side_idx)

            if self.tabs_side.count() > 0:
                self.tabs_side.show()
                if first_win_data.get("state"):
                    try:
                        self.restoreState(first_win_data["state"])  # type: ignore
                    except Exception:
                        log.debug("_restore_session failed", exc_info=True)
            else:
                self.tabs_side.hide()

            active_folder = first_win_data.get("active_folder", "")
            if active_folder and os.path.exists(active_folder):
                self.tabs_side.hide()
                self.is_folder_session_active = True
                if hasattr(self, "explorer_panel"):
                    self.explorer_panel.set_path(active_folder)
                    self.explorer_panel.show()
                self.history_manager.add(active_folder, "folder")

            if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
                if not self.external_files:
                    self.new_pdf_tab()
                    self.new_browser_tab()

            self._extra_windows = []
            for win_data in saved_windows[1:]:
                if not isinstance(win_data, dict):
                    continue
                has_content = bool(
                    win_data.get("main_tabs")
                    or win_data.get("side_tabs")
                    or win_data.get("active_folder")
                )
                if not has_content:
                    continue
                sec_win = window_class()(
                    incognito=False,
                    restore_session=False,
                    is_secondary_restoration=True,
                )
                if win_data.get("geometry"):
                    sec_win.restoreGeometry(win_data["geometry"])  # type: ignore

                sec_win._restore_tabs_from_items(
                    win_data.get("main_tabs", []), sec_win.tabs_main
                )
                sec_win._restore_tabs_from_items(
                    win_data.get("side_tabs", []), sec_win.tabs_side
                )

                m_idx = win_data.get("main_active_idx", -1)
                if isinstance(m_idx, int) and 0 <= m_idx < sec_win.tabs_main.count():
                    sec_win.tabs_main.setCurrentIndex(m_idx)

                s_idx = win_data.get("side_active_idx", -1)
                if isinstance(s_idx, int) and 0 <= s_idx < sec_win.tabs_side.count():
                    sec_win.tabs_side.setCurrentIndex(s_idx)

                if sec_win.tabs_side.count() > 0:
                    sec_win.tabs_side.show()
                    if win_data.get("state"):
                        try:
                            sec_win.restoreState(win_data["state"])  # type: ignore
                        except Exception:
                            log.debug("_restore_session failed", exc_info=True)
                else:
                    sec_win.tabs_side.hide()

                af = win_data.get("active_folder", "")
                if af and os.path.exists(af):
                    sec_win.tabs_side.hide()
                    sec_win.is_folder_session_active = True
                    if hasattr(sec_win, "explorer_panel"):
                        sec_win.explorer_panel.set_path(af)
                        sec_win.explorer_panel.show()
                    sec_win.history_manager.add(af, "folder")

                sec_win.show()
                self._extra_windows.append(sec_win)
            return

        if self.settings.value("window/geometry"):
            self.restoreGeometry(self.settings.value("window/geometry"))  # type: ignore

        self._restore_tabs_from_settings("session/main_tabs", self.tabs_main)
        self._restore_tabs_from_settings("session/side_tabs", self.tabs_side)

        main_idx = self.settings.value("session/main_active_idx", -1, type=int)
        if isinstance(main_idx, int):
            if main_idx >= 0 and main_idx < self.tabs_main.count():
                self.tabs_main.setCurrentIndex(main_idx)

        side_idx = self.settings.value("session/side_active_idx", -1, type=int)
        if isinstance(side_idx, int):
            if side_idx >= 0 and side_idx < self.tabs_side.count():
                self.tabs_side.setCurrentIndex(side_idx)

        if self.tabs_side.count() > 0:
            self.tabs_side.show()
            if self.settings.value("splitter/state"):
                self.splitter.restoreState(self.settings.value("splitter/state"))  # type: ignore
        else:
            self.tabs_side.hide()

        if self.tabs_main.count() == 0 and self.tabs_side.count() == 0:
            if not self.external_files:
                self.new_pdf_tab()
                self.new_browser_tab()

        active_folder = self.settings.value("session/active_folder", "", type=str)
        if active_folder and os.path.exists(active_folder):
            self.tabs_side.hide()
            self.is_folder_session_active = True
            if hasattr(self, "explorer_panel"):
                self.explorer_panel.set_path(active_folder)
                self.explorer_panel.show()
            self.history_manager.add(active_folder, "folder")

    def _restore_tabs_from_items(self, items: list, target_widget: QTabWidget) -> None:
        """Helper to restore a collection of serialized tab records into a target tab widget."""
        if isinstance(items, str):
            items = [items]

        for item in items:
            if isinstance(item, str) and os.path.exists(item):
                self._add_pdf_tab(item, target_widget, True)
            elif isinstance(item, dict):
                kind = item.get("type")
                data = item.get("data")
                if kind in ("pdf", "doc") and data and os.path.exists(data):
                    page = item.get("page")
                    try:
                        page = int(page) if page is not None else None
                    except (TypeError, ValueError):
                        page = None
                    self._open_document(
                        data,
                        target_widget,
                        preview=self._as_bool(item.get("preview")),
                        restore_state=page is None,
                        page=page,
                    )
                elif kind == "web" and data:
                    self._add_browser_tab(data, target_widget)
                elif kind == "folder_home":
                    self.new_folder_home_tab(target_widget)
                elif kind == "system" and data in SYSTEM_PAGES:
                    self.open_system_page(data, target=target_widget)

    @staticmethod
    def _as_bool(value) -> Optional[bool]:
        """Reads a boolean back from QSettings, which may hand back strings."""
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    def _restore_tabs_from_settings(self, key: str, target_widget: QTabWidget) -> None:
        """Recreates the tabs saved under key."""
        items = self.settings.value(key, [], type=list)
        self._restore_tabs_from_items(items, target_widget)

    def _record_closed_tab(self, widget: QWidget) -> None:
        """Remembers a closed tab so it can be reopened."""
        if isinstance(widget, (ReaderTab, PreviewTextTab)) and widget.current_path:
            self.closed_tabs_stack.append(
                {
                    "type": "pdf",
                    "data": widget.current_path,
                    "preview": bool(getattr(widget, "is_preview", False)),
                }
            )
        elif getattr(widget, "is_system_tab", False):
            self.closed_tabs_stack.append({"type": "system", "data": widget.page})
        elif isinstance(widget, BrowserTab):
            url_str = getattr(
                widget, "_original_url_before_close", widget.web.url().toString()
            )
            if url_str and url_str != "about:blank":
                self.closed_tabs_stack.append({"type": "web", "data": url_str})

    def restore_last_closed_tab(self) -> None:
        """
        Restores the most recently closed tab from the stack.
        """
        if not self.closed_tabs_stack:
            return

        last = self.closed_tabs_stack.pop()
        target = (
            self.tabs_side
            if self.tabs_side.isVisible() and self.tabs_side.hasFocus()
            else self.tabs_main
        )

        if last["type"] == "pdf":
            self._open_document(
                last["data"], target, preview=last.get("preview"), restore_state=True
            )
        elif last["type"] == "web":
            self._add_browser_tab(last["data"], target)
        elif last["type"] == "system" and last.get("data") in SYSTEM_PAGES:
            self.open_system_page(last["data"], target=target)
            return

        target.setCurrentIndex(target.count() - 1)

    def _serialize_tab_state(self, tab_widget: QTabWidget) -> List[dict]:
        """The session data of each tab, leaving out empty reader tabs and browser start pages."""
        tabs_data = []
        media_domains = [
            "youtube.com",
            "dailymotion.com",
            "reddit.com",
            "vimeo.com",
            "twitch.tv",
            "spotify.com",
            "netflix.com",
        ]

        for i in range(tab_widget.count()):
            wid = tab_widget.widget(i)
            if isinstance(wid, FolderHomeTab):
                tabs_data.append({"type": "folder_home", "data": ""})
            elif getattr(wid, "is_system_tab", False):
                tabs_data.append({"type": "system", "data": wid.page})
            elif isinstance(wid, ReaderTab) and getattr(wid, "current_path", None):
                entry = {
                    "type": "pdf",
                    "data": os.path.abspath(wid.current_path),
                    "preview": bool(getattr(wid, "is_preview", False)),
                }
                page = getattr(wid, "current_page_index", None)
                if isinstance(page, int) and getattr(wid, "current_doc", None):
                    entry["page"] = page
                tabs_data.append(entry)
            elif isinstance(wid, PreviewTextTab) and getattr(wid, "current_path", None):
                tabs_data.append(
                    {
                        "type": "doc",
                        "data": os.path.abspath(wid.current_path),
                        "preview": True,
                    }
                )

            elif isinstance(wid, BrowserTab):
                if not getattr(wid, "incognito", False):
                    url_str = wid.web.url().toString()

                    for domain in media_domains:
                        if domain in url_str.lower():
                            url_obj = wid.web.url()
                            url_str = f"{url_obj.scheme()}://{url_obj.host()}"
                            break

                    if (
                        not is_local_homepage(QUrl(url_str))
                        and url_str != "about:blank"
                        and url_str
                    ):
                        tabs_data.append({"type": "web", "data": url_str})
        return tabs_data

    def _auto_save_session(self) -> None:
        """
        Periodically writes the active session state to settings so an abrupt
        PC shutdown or application termination does not lose the session.
        """
        if self.incognito:
            return

        window_class()._save_all_windows_session(clean_exit=False)

    def restore_previous_session(self) -> None:
        """
        Explicitly restores the last opened tabs from history/settings.
        """
        main_saved = self.settings.value("session/main_tabs", [])
        side_saved = self.settings.value("session/side_tabs", [])

        if not main_saved and not side_saved:
            QMessageBox.information(
                self,
                "Restore Session",
                "No previous session tab data was found in history.",
            )
            return

        self._restore_tabs_from_settings("session/main_tabs", self.tabs_main)
        self._restore_tabs_from_settings("session/side_tabs", self.tabs_side)

        if self.tabs_side.count() > 0:
            self.tabs_side.show()
            if self.settings.value("splitter/state"):
                self.splitter.restoreState(self.settings.value("splitter/state"))  # type: ignore

        self.show_toast("Restored last session tabs 📂")

    def _serialize_window_state(self) -> dict:
        """Serializes the current window's geometry, state, tabs, and folder session."""
        main_tabs = self._serialize_tab_state(self.tabs_main)
        side_tabs = self._serialize_tab_state(self.tabs_side)
        active_folder = ""
        if getattr(self, "is_folder_session_active", False) and hasattr(
            self, "explorer_panel"
        ):
            active_folder = self.explorer_panel.current_path

        return {
            "geometry": self.saveGeometry(),
            "state": self.saveState(),
            "main_tabs": main_tabs,
            "side_tabs": side_tabs,
            "main_active_idx": self.tabs_main.currentIndex(),
            "side_active_idx": self.tabs_side.currentIndex(),
            "active_folder": active_folder,
        }

    @classmethod
    def _save_all_windows_session(
        cls,
        clean_exit: bool = True,
        closing_window: Optional["QMainWindow"] = None,
    ) -> None:
        """Saves session state for all currently open non-incognito windows."""
        settings = app_settings()
        now = time.time()

        cls._recently_closed_windows = [
            (t, data) for (t, data) in cls._recently_closed_windows if now - t < 15.0
        ]

        current_open = [
            w for w in cls._all_open_windows if not getattr(w, "incognito", False)
        ]

        windows_to_save = list(current_open)
        if (
            closing_window
            and closing_window not in windows_to_save
            and not getattr(closing_window, "incognito", False)
        ):
            windows_to_save.append(closing_window)

        if not windows_to_save and not cls._recently_closed_windows:
            if clean_exit:
                settings.setValue("session/clean_exit", True)
                settings.sync()
            return

        windows_data = []
        for win in windows_to_save:
            windows_data.append(win._serialize_window_state())

        remaining_open = [w for w in current_open if w != closing_window]
        if not remaining_open and cls._recently_closed_windows:
            for _, recent_data in cls._recently_closed_windows:
                if (
                    recent_data.get("main_tabs")
                    or recent_data.get("side_tabs")
                    or recent_data.get("active_folder")
                ):
                    windows_data.append(recent_data)
            cls._recently_closed_windows.clear()

        if windows_data:
            settings.setValue("session/windows", windows_data)
            first_win = windows_data[0]
            settings.setValue("session/main_tabs", first_win["main_tabs"])
            settings.setValue("session/side_tabs", first_win["side_tabs"])
            settings.setValue("session/main_active_idx", first_win["main_active_idx"])
            settings.setValue("session/side_active_idx", first_win["side_active_idx"])
            settings.setValue("session/active_folder", first_win["active_folder"])
            settings.setValue("window/geometry", first_win["geometry"])
            settings.setValue("window/state", first_win["state"])
            if clean_exit:
                settings.setValue("session/clean_exit", True)
            settings.sync()
