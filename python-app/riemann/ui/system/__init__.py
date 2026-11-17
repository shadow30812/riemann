"""
System tabs: Settings, History, Bookmarks, Downloads, Library and Help open as tabs
in the window, like documents and web pages, instead of as separate windows.

PAGES maps each page key to its tab class. The window keeps at most one tab
per page (riemann.window.system.SystemMixin.open_system_page) and focuses it
when asked again; sessions and "reopen closed tab" record a System tab as
{"type": "system", "data": <page>}.
"""

from .base import SectionedSystemTab, SystemTab
from .help_tab import HelpTab
from .lists import BookmarksTab, DownloadsTab, HistoryTab, LibraryTab
from .settings_tab import SettingsTab

PAGES = {cls.page: cls for cls in (SettingsTab, HistoryTab, BookmarksTab, DownloadsTab, LibraryTab, HelpTab)}

__all__ = ["PAGES", "SectionedSystemTab", "SystemTab"]
