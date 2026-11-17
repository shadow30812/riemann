"""
Document tabs that aren't the reader: images (image_tab.ImageTab), tables
(table_tab.TableTab), spreadsheets (spreadsheet_tab.SpreadsheetTab, a
TableTab) and any other file as bytes (hex_tab.HexTab).

VIEWER_TABS lists their classes. Each has current_path and is_preview like a
ReaderTab, so sessions, reopen-closed-tab, preview mode and the tab tooltip
treat it as a document, plus cleanup(), toggle_theme() (the N key) and a
toolbar the window hides in full screen.
"""

from .hex_tab import HexTab
from .image_tab import ImageTab
from .spreadsheet_tab import SpreadsheetTab
from .table_tab import TableTab

VIEWER_TABS = (ImageTab, TableTab, HexTab)

__all__ = ["HexTab", "ImageTab", "SpreadsheetTab", "TableTab", "VIEWER_TABS"]
