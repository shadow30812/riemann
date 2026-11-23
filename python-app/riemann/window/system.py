"""
Opening System tabs (Settings, History, Bookmarks, Downloads, Library, Help) in the
window. Each page has at most one tab per window; asking for it again brings
that tab to the front, optionally at a given section ("permissions" in
Settings, "videos" in Downloads).
"""


from PySide6.QtWidgets import QTabWidget, QWidget

from ..ui.system import PAGES


class SystemMixin:
    def find_system_tab(self, page: str) -> tuple[QTabWidget | None, int]:
        for tabs in (self.tabs_main, self.tabs_side):
            for i in range(tabs.count()):
                w = tabs.widget(i)
                if getattr(w, "is_system_tab", False) and w.page == page:
                    return tabs, i
        return None, -1

    def open_system_page(self, page: str, section: str = "", target: QTabWidget | None = None) -> QWidget:
        """Shows the System tab for page, creating it if this window has none."""
        tabs, index = self.find_system_tab(page)
        if tabs is not None:
            tabs.setCurrentIndex(index)
            tab = tabs.widget(index)
            tab.refresh()
        else:
            tabs = target or self.tabs_main
            tab = PAGES[page](self)
            index = tabs.addTab(tab, tab.tab_icon(self.dark_mode), tab.title)
            tabs.tabBar().setTabData(index, "system")
            tabs.setCurrentIndex(index)
        if section:
            tab.show_section(section)
        return tab

    def show_settings(self, section: str = "") -> None:
        self.open_system_page("settings", section)

    def show_site_permissions(self) -> None:
        self.open_system_page("settings", "permissions")

    def show_history(self) -> None:
        self.open_system_page("history")

    def show_bookmarks(self) -> None:
        self.open_system_page("bookmarks")

    def show_downloads(self) -> None:
        self.open_system_page("downloads")

    def show_ytdlp_downloads(self) -> None:
        self.open_system_page("downloads", "videos")

    def show_library_search(self) -> None:
        self.open_system_page("library")

    def show_help(self, section: str = "") -> None:
        self.open_system_page("help", section)
