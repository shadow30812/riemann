"""
Document tabs that aren't the reader: images (image_tab.ImageTab).

VIEWER_TABS lists their classes. Each has current_path and is_preview like a
ReaderTab, so sessions, reopen-closed-tab, preview mode and the tab tooltip
treat it as a document, plus cleanup(), toggle_theme() (the N key) and a
toolbar the window hides in full screen.
"""

from .image_tab import ImageTab

VIEWER_TABS = (ImageTab,)

__all__ = ["ImageTab", "VIEWER_TABS"]
