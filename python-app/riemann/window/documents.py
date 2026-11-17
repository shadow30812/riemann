"""
Opening documents and web pages in tabs, previews, and the PDF tools (split, join, convert, compress).

Part of RiemannWindow (riemann/app.py), split out by concern; the methods use
the window's attributes freely, as they did when they lived in one class.

Notes:

open_path: A tab still loading in the background only knows its _load_path.

_office_ready: opening an Office file (not a preview) without LibreOffice
asks to install it (ui/office_install.py), once per kind of Office file per
session and only while Settings allows asking. Declined, the file isn't
opened and a message says why. Tabs restored from a session never ask: they
open and say in the page that LibreOffice is needed. When Riemann can read
the file itself (core/office_basic.py), the prompt also offers the basic
view, and once asked (or with asking turned off) the basic view opens
without a prompt. When the basic view's packages are missing, choosing it
offers to install them (ui/office_install.py, offer_basic_packages).

Spreadsheets open in a SpreadsheetTab (the table view, _viewer_kind gives
them the kind "spreadsheet"); show_as_pages adds a reader tab next to one,
which shows the workbook as LibreOffice prints it.
"""

import os
from typing import Optional, Tuple
import shiboken6
from PySide6.QtCore import (
    QUrl,
)
from PySide6.QtGui import (
    QIcon,
)
from PySide6.QtWebEngineCore import (
    QWebEngineProfile,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QTabWidget,
    QTreeWidgetItem,
    QWidget,
)
from ..core.features import (
    CompressDialog,
    ConvertDialog,
)
from ..ui.browser import BrowserTab, PreviewTextTab
from ..ui.palette import tinted_icon, tokens
from ..ui.reader.tab import PreviewReaderTab, ReaderTab
from ..ui.viewers import ImageTab, SpreadsheetTab, TableTab
from ..core.assets import icon_path
from ..core import office
from ..core.office_basic import basic_format, packages_ready
from ..core.doctypes import HTML_SUFFIXES, VIEWER_KINDS, dialog_filter, document_kind, is_reader_name, office_type
from ..core.settings import OFFICE_ASK_KEY, OFFICE_PATH_KEY, get_dialog_directory, save_last_directory


class DocumentsMixin:
    def split_pdf(self) -> None:
        """
        Utility to extract specific pages into a new PDF using pypdf.
        """
        current = self.tabs_main.currentWidget()
        source_path = ""
        start_dir = get_dialog_directory(self.settings)

        if isinstance(current, ReaderTab) and current.current_path:
            source_path = current.current_path
        else:
            source_path, _ = QFileDialog.getOpenFileName(
                self, "Select PDF to Split", start_dir, "PDF Files (*.pdf)"
            )
        if not source_path:
            return

        pages_str, ok = QInputDialog.getText(
            self, "Split PDF", "Enter page ranges to extract (e.g., 1-5, 8, 11-15):"
        )
        if not ok or not pages_str.strip():
            return

        current_dir = get_dialog_directory(self.settings)
        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Split PDF As",
            os.path.join(current_dir, "split.pdf"),
            "PDF Files (*.pdf)",
        )
        if not dest_path:
            return

        save_last_directory(self.settings, dest_path)
        try:
            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(source_path)
            writer = PdfWriter()
            max_idx = len(reader.pages) - 1

            for part in pages_str.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    start_idx = min(max(0, start - 1), max_idx)
                    end_idx = min(max(0, end - 1), max_idx)
                    if start_idx <= end_idx:
                        for p_idx in range(start_idx, end_idx + 1):
                            writer.add_page(reader.pages[p_idx])
                else:
                    page_idx = int(part) - 1
                    if 0 <= page_idx <= max_idx:
                        writer.add_page(reader.pages[page_idx])

            with open(dest_path, "wb") as f_out:
                writer.write(f_out)

            if (
                QMessageBox.question(
                    self, "Success", f"Saved to {dest_path}.\nOpen in new tab?"
                )
                == QMessageBox.StandardButton.Yes
            ):
                self.new_pdf_tab(dest_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to split PDF: {e}")

    def join_pdfs(self) -> None:
        """
        Utility to merge multiple PDFs into one using pypdf.
        """
        start_dir = get_dialog_directory(self.settings)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs to Merge", start_dir, "PDF Files (*.pdf)"
        )
        if not paths or len(paths) < 2:
            if paths:
                QMessageBox.warning(
                    self, "Merge PDFs", "Please select at least two PDF files."
                )
            return

        save_dir = os.path.dirname(paths[0])
        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Merged PDF As",
            os.path.join(save_dir, "merged.pdf"),
            "PDF Files (*.pdf)",
        )
        if not dest_path:
            return

        save_last_directory(self.settings, dest_path)
        paths.sort()
        try:
            from pypdf import PdfReader, PdfWriter

            writer = PdfWriter()
            for path in paths:
                reader = PdfReader(path)
                for page in reader.pages:
                    writer.add_page(page)

            with open(dest_path, "wb") as f_out:
                writer.write(f_out)

            if (
                QMessageBox.question(
                    self,
                    "Success",
                    f"Merged PDF saved to {dest_path}.\nOpen in new tab?",
                )
                == QMessageBox.StandardButton.Yes
            ):
                self.new_pdf_tab(dest_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to merge PDFs: {e}")

    def add_to_history(self, item: str, item_type: str = "web") -> None:
        """Adds to history and refreshes address-bar completion."""
        if self.incognito or not item:
            return

        clean_item = item
        if clean_item.startswith("file://"):
            clean_item = QUrl(clean_item).toLocalFile()

        if is_reader_name(clean_item):
            item_type = "pdf"
            if os.path.exists(clean_item):
                clean_item = os.path.abspath(clean_item)
        elif clean_item.lower().endswith(HTML_SUFFIXES):
            item_type = "web"
            if os.path.exists(clean_item):
                clean_item = os.path.abspath(clean_item)

        self.history_manager.add(clean_item, item_type)
        self.history_model.setStringList(self.history_manager.get_model_data())

    def refresh_signature_panel(self) -> None:
        """Shows or hides the signature dock for the active tab."""
        active_widget = self.tabs_main.currentWidget()

        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            side_widget = self.tabs_side.currentWidget()
            if side_widget and side_widget != self.tree_signatures:
                active_widget = side_widget

        dismissed = getattr(active_widget, "_sig_panel_dismissed", False)
        signatures = (
            getattr(active_widget, "current_signatures", [])
            if hasattr(active_widget, "current_signatures")
            else []
        )

        sig_idx = self.tabs_side.indexOf(self.tree_signatures)
        if signatures and not dismissed:
            self.tree_signatures.clear()

            for sig in signatures:
                item = QTreeWidgetItem(self.tree_signatures)
                item.setText(0, f"  {sig.get('subject', 'Unknown')}")

                if sig.get("valid"):
                    if sig.get("is_trusted"):
                        icon_name = "circle-check"
                    else:
                        icon_name = "circle-question-mark"
                else:
                    icon_name = "circle-slash"

                item.setIcon(0, QIcon(icon_path(f"{icon_name}.svg", self.dark_mode)))
                item.setText(1, sig.get("field_name", "Unknown"))

                item.setIcon(1, QIcon(icon_path("pen-line.svg", self.dark_mode)))

                child_cert = QTreeWidgetItem(item)
                child_cert.setText(0, f"Cert Hash: {sig.get('cert_hash', '')[:15]}...")

                if not sig.get("is_trusted") and sig.get("valid"):
                    child_warn = QTreeWidgetItem(item)
                    child_warn.setText(0, "Identity Unknown (Not in Trust Store)")
                if not sig.get("valid"):
                    child_err = QTreeWidgetItem(item)
                    child_err.setText(0, "CRITICAL: Document Altered!")

            self.tree_signatures.expandAll()
            if sig_idx == -1:
                self.tabs_side.addTab(self.tree_signatures, "Signatures")
            if self.tabs_side.isHidden():
                self.tabs_side.show()

        else:
            if sig_idx != -1:
                self.tabs_side.removeTab(sig_idx)
            if self.tabs_side.count() == 0:
                self.tabs_side.hide()

    def _add_pdf_tab(
        self,
        path: str,
        target_widget: QTabWidget,
        is_preview: Optional[bool] = None,
    ) -> None:
        """
        Opens a document in a new tab of target_widget (kept for callers that predate
        _open_document, which handles every supported file type). is_preview, if given,
        forces preview or full mode.
        """
        self._open_document(path, target_widget, preview=is_preview)

    def _preview_mode_enabled(self) -> bool:
        return self.settings.value("reader/open_in_preview_mode", False, type=bool)

    @staticmethod
    def _document_kind(path: str) -> str:
        """'pdf', 'markdown', 'html', 'ebook', 'office', 'image', 'table', 'text' or 'binary' (core/doctypes.py)."""
        return document_kind(path)

    def _office_ready(self, path: str) -> bool:
        if getattr(self, "_restoring_tabs", False):
            return True
        if office.find_office(self.settings.value(OFFICE_PATH_KEY, "", type=str)) is not None:
            return True
        fmt = basic_format(path)
        asked = self.__dict__.setdefault("_office_asked", set())
        kind = office_type(path) or "document"
        if self.settings.value(OFFICE_ASK_KEY, True, type=bool) and kind not in asked:
            asked.add(kind)
            from ..ui.office_install import offer_office

            choice = offer_office(self, path, basic=fmt is not None)
            if choice == "office":
                return True
            if choice == "basic":
                return self._basic_view_ready(path, fmt)
        elif fmt is not None and packages_ready(fmt):
            return True
        self.show_toast(f"{os.path.basename(path)} needs LibreOffice. Settings > Documents can install it.")
        return False

    def _basic_view_ready(self, path: str, fmt: str) -> bool:
        if packages_ready(fmt):
            return True
        from ..ui.office_install import offer_basic_packages

        if offer_basic_packages(self) and packages_ready(fmt):
            return True
        self.show_toast(f"{os.path.basename(path)} can't be shown without LibreOffice or the basic-view packages.")
        return False

    def _refuse_binary(self, path: str) -> None:
        self.show_toast(f"{os.path.basename(path)} isn't a document, image or text file, so Riemann can't show it.")

    def _create_reader(self, preview: bool) -> "ReaderTab":
        """Creates an empty reader tab, lightweight when preview is True; it knows whether this window is incognito."""
        reader = PreviewReaderTab() if preview else ReaderTab()
        reader.incognito = bool(getattr(self, "incognito", False))
        if preview:
            return reader
        reader.signatures_detected.connect(lambda _: self.refresh_signature_panel())
        return reader

    @staticmethod
    def _viewer_kind(kind: str, path: str) -> str:
        """kind, or "spreadsheet" for an Office spreadsheet, which opens in the table view."""
        return "spreadsheet" if kind == "office" and office_type(path) == "spreadsheet" else kind

    def _create_viewer(self, kind: str, path: str, preview: bool) -> QWidget:
        """The ImageTab, TableTab or SpreadsheetTab for path."""
        viewer = {"image": ImageTab, "spreadsheet": SpreadsheetTab}.get(kind, TableTab)
        return viewer(path, getattr(self, "dark_mode", False), preview=preview)

    def show_as_pages(self, table: QWidget) -> Optional[QWidget]:
        """Opens a reader tab next to a SpreadsheetTab, showing its workbook as LibreOffice prints it."""
        tab_widget = next((tw for tw in (self.tabs_main, self.tabs_side) if tw.indexOf(table) != -1), None)
        if tab_widget is None:
            return None
        reader = self._create_reader(False)
        reader.load_document(table.current_path)
        idx = tab_widget.indexOf(table) + 1
        tab_widget.insertTab(idx, reader, QIcon(icon_path("pdf.png")), os.path.basename(table.current_path))
        tab_widget.setCurrentIndex(idx)
        self._update_tab_tooltip(tab_widget, idx)
        return reader

    @staticmethod
    def _viewer_icon(kind: str) -> str:
        return "image.svg" if kind == "image" else "file-text.svg"

    def open_as_text(self, table: QWidget) -> Optional[QWidget]:
        """Replaces a TableTab, in the same spot, by a reader showing its file as plain text."""
        tab_widget = next((tw for tw in (self.tabs_main, self.tabs_side) if tw.indexOf(table) != -1), None)
        if tab_widget is None:
            return None
        idx = tab_widget.indexOf(table)
        reader = self._create_reader(False)
        reader.load_document(table.current_path)
        tab_widget.insertTab(idx, reader, QIcon(icon_path("pdf.png")), os.path.basename(table.current_path))
        tab_widget.removeTab(tab_widget.indexOf(table))
        if getattr(self, "preview_tab_widget", None) is table:
            self.preview_tab_widget = None
        table.cleanup()
        table.deleteLater()
        tab_widget.setCurrentWidget(reader)
        self._update_tab_tooltip(tab_widget, tab_widget.indexOf(reader))
        return reader

    def _preview_icon(self) -> QIcon:
        """The eye shown on preview tabs, in the theme's accent so it reads on both backgrounds."""
        return tinted_icon(
            "scan-eye", tokens(getattr(self, "dark_mode", False))["accent"], 16
        )

    def _open_document(
        self,
        path: str,
        target_widget: Optional[QTabWidget] = None,
        preview: Optional[bool] = None,
        page: Optional[int] = None,
        insert_idx: Optional[int] = None,
        activate: bool = True,
    ) -> Optional[QWidget]:
        """
        Opens any supported file as a tab. PDFs, Office files, e-books, Markdown and
        other text files go to the reader, images to an ImageTab, CSV and TSV to a
        TableTab, spreadsheets to a SpreadsheetTab, HTML to the browser;
        binary files are refused. Preview tabs use the
        lightweight viewers instead. When preview is None, the "Open Documents in
        Preview Mode" setting decides. Returns the new tab, or None if nothing was opened.
        """
        if not path:
            return None
        if target_widget is None:
            target_widget = self.tabs_main
        if preview is None:
            preview = self._preview_mode_enabled()

        kind = self._document_kind(path)
        if kind == "binary":
            self._refuse_binary(path)
            return None
        if kind == "office" and not preview and not self._office_ready(path):
            return None
        self.add_to_history(path, "web" if kind == "html" else "pdf")

        if not preview and kind == "html":
            url = QUrl.fromLocalFile(os.path.abspath(path)).toString()
            browser = self._add_browser_tab(url, target_widget)
            idx = target_widget.indexOf(browser)
            if insert_idx is not None and idx != -1 and idx != insert_idx:
                target_widget.tabBar().moveTab(idx, insert_idx)
            if activate:
                target_widget.setCurrentWidget(browser)
            return browser

        kind = self._viewer_kind(kind, path)
        if kind in VIEWER_KINDS or kind == "spreadsheet":
            widget = self._create_viewer(kind, path, preview)
            icon = self._preview_icon() if preview else QIcon(icon_path(self._viewer_icon(kind), getattr(self, "dark_mode", False)))
        elif kind in ("pdf", "ebook", "office") or (kind in ("markdown", "text") and not preview):
            widget = self._create_reader(preview)
            if page is not None:
                widget.pending_initial_page = page
            widget.load_document(path)
            icon = (
                self._preview_icon()
                if preview
                else QIcon(
                    icon_path("pdf.png")
                )
            )
        else:
            widget = PreviewTextTab(path, getattr(self, "dark_mode", False))
            icon = self._preview_icon()

        if insert_idx is None:
            current_idx = target_widget.currentIndex()
            insert_idx = current_idx + 1 if current_idx != -1 else target_widget.count()

        target_widget.insertTab(insert_idx, widget, icon, os.path.basename(path))
        self._update_tab_tooltip(target_widget, insert_idx)
        if activate:
            target_widget.setCurrentIndex(insert_idx)
        return widget

    def open_path(self, path: str) -> None:
        """
        Opens a file handed over from outside (command line, file manager,
        another instance), switching to it instead if it is already open.
        """
        target = os.path.abspath(path)
        for tab_widget in (self.tabs_main, self.tabs_side):
            for i in range(tab_widget.count()):
                widget = tab_widget.widget(i)
                current = getattr(widget, "current_path", None) or getattr(
                    widget, "_load_path", None
                )
                if current and os.path.abspath(current) == target:
                    tab_widget.setCurrentIndex(i)
                    return
        self.new_pdf_tab(target)

    def open_from_reader(self, reader: QWidget, path: str) -> None:
        """
        Opens path from inside reader (its start page, Open dialog or a file
        dropped on it). A reader that shows a document loads path in place; one
        still on its start page is replaced, in the same spot, by the tab
        _open_document makes, so the file opens in the mode the preview setting
        asks for, with that mode's icon.
        """
        tab_widget = next(
            (tw for tw in (self.tabs_main, self.tabs_side) if tw.indexOf(reader) != -1),
            None,
        )
        if tab_widget is None or getattr(reader, "current_path", None):
            kind = self._document_kind(path)
            if kind == "binary":
                self._refuse_binary(path)
                return
            if kind in VIEWER_KINDS or self._viewer_kind(kind, path) == "spreadsheet":
                self._open_document(path, tab_widget)
                return
            if kind == "office" and not getattr(reader, "is_preview", False) and not self._office_ready(path):
                return
            self.add_to_history(path, "web" if kind == "html" else "pdf")
            reader.load_document(path)
            return
        idx = tab_widget.indexOf(reader)
        was_current = tab_widget.currentIndex() == idx
        widget = self._open_document(path, tab_widget, insert_idx=idx, activate=False)
        if widget is None:
            return
        tab_widget.removeTab(tab_widget.indexOf(reader))
        if hasattr(reader, "cleanup"):
            reader.cleanup()
        reader.deleteLater()
        if was_current:
            tab_widget.setCurrentWidget(widget)
        self._auto_save_session()

    def _replace_tab_document(
        self, tab_widget: QTabWidget, idx: int, preview: bool
    ) -> Optional[QWidget]:
        """
        Reopens the document shown in tab idx in preview or full mode, keeping
        its position in the tab bar and, for PDFs, the current page.
        """
        old = tab_widget.widget(idx)
        path = getattr(old, "current_path", None) if old else None
        if not path or not os.path.exists(path):
            return None
        if getattr(old, "edit_scratch", None):
            return None

        page = getattr(old, "current_page_index", None)
        if not isinstance(page, int) or not getattr(old, "current_doc", None):
            page = None
        was_current = tab_widget.currentIndex() == idx

        new_widget = self._open_document(
            path, tab_widget, preview=preview, page=page, insert_idx=idx, activate=False
        )
        if new_widget is None:
            return None

        old_idx = tab_widget.indexOf(old)
        if old_idx != -1:
            tab_widget.removeTab(old_idx)
        if getattr(self, "preview_tab_widget", None) is old:
            self.preview_tab_widget = None
        if hasattr(old, "cleanup"):
            old.cleanup()
        old.deleteLater()

        new_idx = tab_widget.indexOf(new_widget)
        if was_current and new_idx != -1:
            tab_widget.setCurrentIndex(new_idx)
            new_widget.setFocus()
        self._auto_save_session()
        return new_widget

    def _add_browser_tab(self, url: str, target_widget: QTabWidget) -> BrowserTab:
        """
        Adds a BrowserTab on the shared profile; separate profiles would lock the same
        database.
        """
        if self.incognito:
            use_profile = self.web_profile
        else:
            use_profile = self.web_profile

        browser = BrowserTab(url, profile=use_profile, dark_mode=self.dark_mode)
        browser.completer.setModel(self.history_model)

        default_icon = QIcon(icon_path("browser.png"))

        current_idx = target_widget.currentIndex()
        insert_idx = current_idx + 1 if current_idx != -1 else target_widget.count()

        target_widget.insertTab(insert_idx, browser, default_icon, "Loading...")
        self._update_tab_tooltip(target_widget, insert_idx)
        target_widget.setCurrentIndex(insert_idx)

        browser.web.urlChanged.connect(lambda qurl: self._update_tab_title(browser))
        browser.web.loadFinished.connect(lambda ok: self._update_tab_title(browser))
        browser.web.titleChanged.connect(lambda title: self._update_tab_title(browser))
        return browser

    def new_pdf_tab(self, path: Optional[str] = None) -> None:
        """With no path, opens an empty reader tab."""
        if path:
            self._open_document(path, self.tabs_main)
        else:
            preview = self._preview_mode_enabled()
            reader = self._create_reader(preview)
            icon = (
                self._preview_icon()
                if preview
                else QIcon(icon_path("pdf.png"))
            )

            current_idx = self.tabs_main.currentIndex()
            insert_idx = (
                current_idx + 1 if current_idx != -1 else self.tabs_main.count()
            )

            self.tabs_main.insertTab(insert_idx, reader, icon, "New Tab")
            self._update_tab_tooltip(self.tabs_main, insert_idx)
            self.tabs_main.setCurrentWidget(reader)

    def new_browser_tab(
        self, url: str = "", incognito: bool = False, background: bool = False
    ) -> BrowserTab:
        """Opens a browser tab, in the side pane if it has focus."""
        is_incognito = self.incognito or incognito
        target = self.tabs_main

        if self.tabs_side.isVisible() and self.tabs_side.hasFocus():
            target = self.tabs_side

        tab_profile = (
            QWebEngineProfile()
            if (incognito and not self.incognito)
            else self.web_profile
        )

        browser = BrowserTab(
            url, profile=tab_profile, dark_mode=self.dark_mode, incognito=is_incognito
        )
        browser.completer.setModel(self.history_model)
        label = "Incognito" if incognito else "Loading..."

        current_idx = target.currentIndex()
        insert_idx = current_idx + 1 if current_idx != -1 else target.count()

        target.insertTab(insert_idx, browser, label)
        self._update_tab_tooltip(target, insert_idx)
        new_tab = target.widget(insert_idx)

        browser.web.urlChanged.connect(lambda qurl: self._update_tab_title(browser))
        browser.web.loadFinished.connect(lambda ok: self._update_tab_title(browser))
        browser.web.titleChanged.connect(lambda title: self._update_tab_title(browser))

        if not background:
            target.setCurrentWidget(new_tab)
            if hasattr(new_tab, "txt_url") and not url:
                new_tab.txt_url.setFocus()
                new_tab.txt_url.selectAll()

        return browser

    def open_file_smart(self) -> None:
        """
        Handles the "Open Document" action.
        Routes PDFs, Markdown and text to ReaderTab and HTML to BrowserTab.
        Opens in the current tab if it's an empty reader, otherwise opens a new tab.
        Supports selecting multiple files.
        """
        start_dir = get_dialog_directory(self.settings)
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Document",
            start_dir,
            dialog_filter(),
        )
        if not paths:
            return

        save_last_directory(self.settings, paths[0])

        preview = self._preview_mode_enabled()
        for i, path in enumerate(paths):
            current = self.tabs_main.currentWidget()
            reuse_empty_reader = (
                i == 0
                and self._document_kind(path) == "pdf"
                and isinstance(current, ReaderTab)
                and not current.current_path
                and bool(getattr(current, "is_preview", False)) == preview
            )
            if reuse_empty_reader:
                self.add_to_history(path, "pdf")
                current.load_document(path)
                self.tabs_main.setTabText(
                    self.tabs_main.currentIndex(), os.path.basename(path)
                )
            else:
                self._open_document(path, self.tabs_main, preview=preview)

    def toggle_auto_pdf(self, checked: bool) -> None:
        self.settings.setValue("browser/auto_open_pdf", checked)

    def open_pdf_in_new_tab(self, path: str) -> None:
        """Opens a PDF in the side pane if it is active, otherwise the main one."""
        target = self.tabs_side if (self.tabs_side.isVisible()) else self.tabs_main

        if target == self.tabs_main and self.tabs_side.isHidden():
            self.toggle_split_view()
            target = self.tabs_side

        self._add_pdf_tab(path, target)
        target.setCurrentIndex(target.count() - 1)

    def show_convert_dialog(self) -> None:
        """Triggers the new conversion interface."""
        dialog = ConvertDialog(self)
        if dialog.exec():
            out_path = dialog.txt_output.text()
            if out_path.endswith(".html") and os.path.exists(out_path):
                reply = QMessageBox.question(
                    self,
                    "Open Output",
                    "Conversion successful. Open the HTML file in a new tab now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.new_browser_tab(f"file:///{out_path.replace(chr(92), '/')}")

    def show_compress_dialog(self) -> None:
        """Triggers the new compression interface."""
        dialog = CompressDialog(self)
        if dialog.exec():
            out_path = dialog.txt_output.text()
            if out_path.endswith(".pdf") and os.path.exists(out_path):
                reply = QMessageBox.question(
                    self,
                    "Open Output",
                    "Compression successful. Open the compressed PDF in a new tab?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.new_pdf_tab(out_path)

    def _open_single_file(self, path: str, insert_idx: int = -1):
        """Opens a file from the explorer or favourites, honouring preview mode."""
        self._open_document(
            path, self.tabs_main, insert_idx=None if insert_idx == -1 else insert_idx
        )

    def _find_document_tab(self, path: str) -> Tuple[Optional[QTabWidget], int]:
        """The pane and index of the tab showing path, or (None, -1)."""
        target = os.path.normcase(os.path.abspath(path))
        for tab_widget in (self.tabs_main, self.tabs_side):
            for i in range(tab_widget.count()):
                widget = tab_widget.widget(i)
                current = getattr(widget, "current_path", None) or getattr(
                    widget, "_load_path", None
                )
                if current and os.path.normcase(os.path.abspath(current)) == target:
                    return tab_widget, i
        return None, -1

    def _live_preview_tab(self) -> Optional[QWidget]:
        """The explorer's preview tab, if it is still open."""
        widget = getattr(self, "preview_tab_widget", None)
        if widget is not None and not shiboken6.isValid(widget):
            widget = None
        if (
            widget is not None
            and self.tabs_main.indexOf(widget) == -1
            and self.tabs_side.indexOf(widget) == -1
        ):
            widget = None
        self.preview_tab_widget = widget
        return widget

    def _on_file_single_clicked(self, path: str):
        """
        A single click in the explorer shows the file in a transient preview
        tab, replaced by the next single click (like VS Code's preview tab).
        A file that is already open is brought to the front instead.
        """
        if not path or not os.path.isfile(path):
            return

        tab_widget, idx = self._find_document_tab(path)
        if tab_widget is not None:
            tab_widget.setCurrentIndex(idx)
            return

        kind = self._document_kind(path)
        if kind == "binary":
            self._refuse_binary(path)
            return
        self.add_to_history(path, "web" if kind == "html" else "pdf")

        kind = self._viewer_kind(kind, path)
        if kind in ("pdf", "ebook", "office"):
            new_preview = self._create_reader(preview=True)
            new_preview.load_document(path)
        elif kind in VIEWER_KINDS or kind == "spreadsheet":
            new_preview = self._create_viewer(kind, path, preview=True)
        else:
            new_preview = PreviewTextTab(path, getattr(self, "dark_mode", False))
        title = f"Preview: {os.path.basename(path)}"

        old = self._live_preview_tab()
        pane = self.tabs_main
        if old is not None:
            pane = (
                self.tabs_main if self.tabs_main.indexOf(old) != -1 else self.tabs_side
            )
            idx = pane.indexOf(old)
            pane.removeTab(idx)
            if hasattr(old, "cleanup"):
                old.cleanup()
            old.deleteLater()
            pane.insertTab(idx, new_preview, self._preview_icon(), title)
        else:
            idx = pane.addTab(new_preview, self._preview_icon(), title)
        pane.setCurrentIndex(idx)
        self._update_tab_tooltip(pane, idx)

        self.preview_tab_widget = new_preview
        self._update_window_title()

    def _on_file_double_clicked(self, path: str):
        """
        Double-clicking a file opens it in full mode: a preview of it is
        promoted in place, and an already open full tab is brought forward.
        """
        if not path or not os.path.isfile(path):
            return

        tab_widget, idx = self._find_document_tab(path)
        if tab_widget is None:
            self._open_document(path, self.tabs_main, preview=False)
        elif getattr(tab_widget.widget(idx), "is_preview", False):
            self._replace_tab_document(tab_widget, idx, preview=False)
        else:
            tab_widget.setCurrentIndex(idx)

    def promote_preview_tab(self):
        """Turns the transient explorer preview into a regular full-mode tab."""
        preview = self._live_preview_tab()
        if preview is None:
            return
        pane = (
            self.tabs_main if self.tabs_main.indexOf(preview) != -1 else self.tabs_side
        )
        self._replace_tab_document(pane, pane.indexOf(preview), preview=False)

    def _detect_file_type(self, path: str) -> str:
        """Determines if a file is a PDF (by extension or magic bytes) or generic text/web."""
        return "pdf" if self._document_kind(path) == "pdf" else "text"

    def force_show_signatures(self) -> None:
        """Manually un-dismisses and reopens the signature panel."""
        active_main = self.tabs_main.currentWidget()
        if active_main:
            active_main._sig_panel_dismissed = False
            self.refresh_signature_panel()
