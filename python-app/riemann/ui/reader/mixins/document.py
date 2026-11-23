"""
Loading, reloading, saving, exporting and printing documents, and releasing them when the tab closes.

Part of ReaderTab (riemann/ui/reader/tab.py). ReaderTab lists this mixin before
QWidget, so its Qt event handlers override QWidget's.

Notes:

load_document: Edits waiting for the debounced save belong to the current file.

open_here: The start page (recent list, path box, Browse), a file dropped
on the tab and the Open dialog load through it. Inside a window it hands the
path to the window's open_from_reader, which records it in history and, for a
tab still on its start page, puts the file in a tab of the mode the preview
setting asks for.

_on_document_loaded: Preview tabs skip annotations, comments, signature checks
and metadata extraction; that is what keeps them light.

_show_text_document: Markdown and other text files (core/doctypes.py decides
which) are shown as one HTML page in the web view: Markdown rendered, other
text highlighted by Pygments when it is installed. Files over TEXT_VIEW_LIMIT
are cut off with a note. QWebEngineView.setHtml refuses pages over 2 MB, so a
bigger page is written to a file in the cache folder (one per tab, deleted
with the tab) and loaded from there; such a page can't load KaTeX or the
emoji font, since local pages may not reach remote or other local files.

Kinds with a converter in core.convert_process.CONVERTERS (EPUB books, the
basic views of Office files) are turned into clean HTML first, in a
child process (core/convert_process.py) driven by a ConvertWorker, while the
page says the file is opening. The result is kept for the tab, so the N key
re-renders without converting again; reload converts again. The book's table
of contents goes to the sections sidebar (_document_sections), whose entries
scroll the page to their anchor. Closing the tab, or opening another file in
it, kills a conversion still running.

Office files (kind "office") are converted to PDF by LibreOffice
(core/office.py) on an OfficeWorker thread, then loaded like any PDF from
the converted copy: current_path stays the Office file, so the title,
history, session, notes and "changed on disk" refer to it, while pdf_path()
returns converted_pdf. Such a tab is read-only: annotations, page edits
(_editable wants a .pdf), forms, passwords, signing and metadata are off, and
a banner says so. Save as PDF (Ctrl+S) copies the converted PDF where the
person chooses and opens that file in the tab, where all of it works. In an
incognito window the conversion goes to a temporary folder of the tab's own,
removed when the tab closes, instead of the cache.

While a converted file is shown, a timer checks its modification time every
OFFICE_POLL_MS (it may be edited in LibreOffice through "Edit in LibreOffice",
or in Word). Once a new time has held for two checks, so a save still being
written isn't caught halfway, the file is converted again and the tab
reloads at the same page without asking: the view holds nothing to lose.

Without LibreOffice, an Office file with a basic view (core/office_basic.py:
Word, PowerPoint, OpenDocument text and slides, RTF) is shown like an
e-book, converted to one HTML page by the "office" converter, with a note
that the layout is simplified. Save as PDF of any page shown in the web view
(Markdown, text, e-books, basic views) prints that page to a PDF.
"""

import gc
import json
import os
import shutil
import sys
import tempfile

import shiboken6
from PySide6.QtCore import (
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPixmap,
    QPixmapCache,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
)

from ....core import page_edit, pdf_annotations, pdf_forms
from ....core.constants import ViewMode
from ....core.convert_process import CONVERTERS, Conversion, ConvertError
from ....core.doctypes import MARKDOWN_SUFFIXES, dialog_filter, document_kind, is_text_document
from ....core.log import log
from ....core.managers import PasswordDialog
from ....core.office import OfficeConversion, OfficeError, find_office, open_in_office
from ....core.office_basic import basic_format, is_spreadsheet, packages_ready
from ....core.render_process import PageRenderer
from ....core.settings import (
    NOTES_VISIBLE_KEY,
    OFFICE_PATH_KEY,
    SIDEBAR_MODE_KEY,
    SIDEBAR_VISIBLE_KEY,
    get_dialog_directory,
    save_last_directory,
)
from ...palette import tokens
from ...threads import release, track
from ..render_worker import PAGE_CACHE, PageRenderWorker
from ..utils import generate_document_html, generate_markdown_html, generate_text_html
from .host import ReaderHostBase

TEXT_VIEW_LIMIT = 20 * 1024 * 1024
SET_HTML_LIMIT = 1_900_000
TEXT_VIEW_FOLDER = "text-view"
OFFICE_POLL_MS = 2000


class DocumentLoadWorker(QThread):
    loaded = Signal(object, object)
    error = Signal(str)

    def __init__(self, engine, path, password):
        super().__init__()
        self.engine = engine
        self.path = path
        self.password = password

    def run(self):
        try:
            doc = self.engine.load_document(self.path, self.password)
        except Exception as e:
            self.error.emit(str(e))
            return
        self.loaded.emit(doc, page_edit.document_state(self.path, self.password))


class ConvertWorker(QThread):
    converted = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, kind: str, path: str):
        super().__init__()
        self.path = path
        self.conversion = Conversion(kind, path)

    def run(self):
        try:
            result = self.conversion.run()
        except ConvertError as e:
            self.failed.emit(self.path, str(e))
            return
        except Exception as e:
            log.warning("Converting %s failed: %s", self.path, e)
            self.failed.emit(self.path, str(e) or type(e).__name__)
            return
        self.converted.emit(self.path, result)


class OfficeWorker(QThread):
    converted = Signal(str, object)
    failed = Signal(str, str)
    missing = Signal(str)

    def __init__(self, path: str, override: str = "", folder: str | None = None):
        super().__init__()
        self.path = path
        self.override = override
        self.conversion = OfficeConversion(path, folder=folder)

    def run(self):
        try:
            self.conversion.office = find_office(self.override)
            if self.conversion.office is None:
                self.missing.emit(self.path)
                return
            result = self.conversion.run()
        except OfficeError as e:
            self.failed.emit(self.path, str(e))
            return
        except Exception as e:
            log.warning("Converting %s failed: %s", self.path, e)
            self.failed.emit(self.path, str(e) or type(e).__name__)
            return
        self.converted.emit(self.path, result)


class DocumentMixin(ReaderHostBase):
    web = None
    load_worker = None
    _loaded_mtime = None
    pending_initial_page = None
    _char_geometry_cache = None
    text_document = False
    _text_page_file: str | None = None
    convert_worker: ConvertWorker | None = None
    _converted: dict | None = None
    _convert_error = ""
    _document_sections: tuple = ()
    _converter_kind = ""
    converted_pdf: str | None = None
    converted_banner = None
    _office_folder: str | None = None
    incognito = False
    _office_timer: QTimer | None = None
    _office_seen_mtime: float | None = None
    document_password: str | None = None
    document_encrypted = False
    document_signed = False
    _signed_edit_accepted = False

    def load_document(
        self,
        path: str,
        password: str | None = None,
        is_retry: bool = False,
    ) -> None:
        """
        Loads a PDF on a background thread, or shows a Markdown or text file; set pending_initial_page first to open
        at a page.
        """
        if self.has_unsaved_page_edits() and path != self.current_path and not self.resolve_page_edits():
            return
        if not is_retry and self.current_path:
            self.flush_annotations_to_pdf()
        if path != self.current_path:
            self._signed_edit_accepted = False

        if is_text_document(path):
            self._show_text_document(path)
            return
        if document_kind(path) == "office":
            self._convert_office(path)
            return

        self.converted_pdf = None
        source = self.edit_scratch if self.edit_scratch and path == self.current_path else path
        self._load_pdf(path, source, password, is_retry)

    def _load_pdf(self, path: str, source: str, password: str | None, is_retry: bool) -> None:
        self.load_progress = QProgressDialog(
            f"Loading {os.path.basename(path)}...", None, 0, 0, self
        )
        self.load_progress.setWindowTitle("Please Wait")
        self.load_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.load_progress.setCancelButton(None)
        self.load_progress.show()

        self._load_path = path
        self._load_source = source
        self._load_password = password
        self._load_is_retry = is_retry

        self.load_worker = DocumentLoadWorker(self.engine, self._load_source, password)
        self.load_worker.loaded.connect(self._on_document_loaded)
        self.load_worker.error.connect(self._on_document_load_error)
        track(self.load_worker).start()

    def _on_document_loaded(self, doc, state=(False, False)):
        """Callback for when the thread returns the PDF; state is (encrypted, signed) from page_edit.document_state."""
        self.load_progress.accept()
        self.current_doc = doc
        self.document_password = self._load_password
        self.document_encrypted, self.document_signed = state
        self.text_document = False
        self._forget_conversion()
        self._drop_text_page_file()
        self._update_protect_button()
        self._replace_page_renderer(PageRenderer(doc, self._load_source, self._load_password))
        self.current_signatures = []
        self.signature_appearances = {}
        self._reset_document_caches()
        self.reset_navigation_history()
        self.citations.forget()
        self.forms.blocked = bool(self.converted_pdf)
        self._show_converted_banner()
        if self.notes_panel.isVisible() or self.settings.value(NOTES_VISIBLE_KEY, False, type=bool):
            self.notes_panel.load(self.current_path)
            self.notes_panel.show()
            self.btn_notes.setChecked(True)
        self._start_render_worker()

        path = self._load_path
        is_retry = self._load_is_retry

        self._probe_base_page_size()
        self.current_path = path
        try:
            self._loaded_mtime = os.path.getmtime(path) if os.path.exists(path) else None
        except Exception:
            self._loaded_mtime = None
        self._update_tab_title(os.path.basename(path))

        if is_retry:
            QTimer.singleShot(
                50, self, lambda: self.show_toast("Document unlocked successfully.")
            )

        if not self.is_preview:
            self.toolbar.show()
            self.sidebar_nav.set_mode(self.settings.value(SIDEBAR_MODE_KEY, "outline", type=str))
            self.sidebar_nav.load_document(path, self.page_renderer, self.theme_mode)
            self._set_sidebar_visible(
                self.settings.value(SIDEBAR_VISIBLE_KEY, False, type=bool)
            )
        self.stack.setCurrentIndex(0)
        self.view_mode = ViewMode.IMAGE
        if hasattr(self.window(), "_update_window_title"):
            self.window()._update_window_title()

        if not self.is_preview:
            self.load_annotations()
            self._load_external_comments_async(self.pdf_path())
            QTimer.singleShot(500, self, lambda: self._detect_signatures(self.pdf_path()))

        initial_page = self.pending_initial_page
        self.pending_initial_page = None
        if initial_page is not None:
            self.current_page_index = max(
                0, min(int(initial_page), self.current_doc.page_count - 1)
            )
            self.rebuild_layout()
            self.jump_to_page(self.current_page_index)
        else:
            self.current_page_index = 0
            self.rebuild_layout()
            self.update_view()

        if not self.is_preview:
            QTimer.singleShot(2000, self, self.extract_document_metadata)

    def _reset_document_caches(self) -> None:
        """Drops everything derived from the previously loaded document."""
        PAGE_CACHE.drop_owner(self._render_owner())
        self._doc_generation += 1
        self._cached_base_size = None
        self._raw_links_cache = {}
        self._raw_forms_cache = {}
        self.text_segments_cache.clear()
        if self._char_geometry_cache is not None:
            self._char_geometry_cache.clear()
        self.clear_text_selection_state()

    def _start_render_worker(self) -> None:
        """Points the background renderer at the current document."""
        if self._render_worker is None:
            self._render_worker = PageRenderWorker()
            self._render_worker.page_rendered.connect(self._on_page_rendered)
            self._render_worker.page_failed.connect(self._on_page_failed)
            self._render_worker.start()
        self._render_worker.set_document(self.page_renderer, self._render_owner())

    def _replace_page_renderer(self, renderer: PageRenderer | None) -> None:
        old, self.page_renderer = self.page_renderer, renderer
        if old is not None:
            old.close()

    def _stop_render_worker(self) -> None:
        worker = self._render_worker
        self._render_worker = None
        if worker is None:
            return
        try:
            worker.page_rendered.disconnect(self._on_page_rendered)
            worker.page_failed.disconnect(self._on_page_failed)
        except (RuntimeError, TypeError):
            pass
        worker.stop()
        if worker.wait(3000):
            worker.deleteLater()
        else:
            release(worker)

    def _on_document_load_error(self, err_str):
        """Callback if the background thread encounters a loading exception."""
        self.load_progress.accept()
        path = self._load_path
        is_retry = self._load_is_retry

        err_str_lower = err_str.lower()
        if "password" in err_str_lower or "encrypted" in err_str_lower:
            error_text = "Incorrect password. Please try again." if is_retry else None
            dialog = PasswordDialog(self, error_msg=error_text)
            if dialog.exec():
                pw = dialog.get_password()
                if pw:
                    self.load_document(path, pw, is_retry=True)
            return

        QMessageBox.critical(
            self,
            "Document Load Error",
            f"Could not load the document. The file may be damaged or not what its name says.\n\nDetails: {err_str}",
        )
        sys.stderr.write(f"Load error: {err_str}\n")

    def reload_document(self) -> None:
        """
        Refreshes the currently open PDF or Markdown tab.
        Checks if the file has changed on disk or is no longer present,
        prompting the user whether to reload or keep the existing state.
        """
        if not self.current_path:
            return

        if not os.path.exists(self.current_path):
            ret = QMessageBox.question(
                self,
                "File No Longer Present",
                f"The file is no longer present at:\n{self.current_path}\n\nDo you want to close this tab?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret == QMessageBox.StandardButton.Yes:
                tw = self._get_tab_widget()
                if tw:
                    idx = tw.indexOf(self)
                    if idx != -1:
                        main_win = self.window()
                        if hasattr(main_win, "close_tab") and tw == getattr(
                            main_win, "tabs_main", None
                        ):
                            main_win.close_tab(idx)
                        elif hasattr(main_win, "close_side_tab") and tw == getattr(
                            main_win, "tabs_side", None
                        ):
                            main_win.close_side_tab(idx)
                        else:
                            tw.removeTab(idx)
                            self.deleteLater()
            return

        current_mtime = None
        try:
            current_mtime = os.path.getmtime(self.current_path)
        except Exception:
            log.debug("reload_document failed", exc_info=True)

        has_changed = (
            self._loaded_mtime is not None
            and current_mtime is not None
            and current_mtime != self._loaded_mtime
        )

        if has_changed:
            ret = QMessageBox.question(
                self,
                "File Changed On Disk",
                f"The file '{os.path.basename(self.current_path)}' has been modified on disk.\n\n"
                f"Do you want to open the new file, or keep the currently open version?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        saved_page = self.current_page_index
        saved_scroll = self.scroll.verticalScrollBar().value()
        self._loaded_mtime = current_mtime
        self.load_document(self.current_path)

        def _restore_pos():
            if self.current_doc:
                self.current_page_index = min(
                    saved_page, self.current_doc.page_count - 1
                )
                self.ensure_visible(self.current_page_index)
            self.scroll.verticalScrollBar().setValue(saved_scroll)

        QTimer.singleShot(350, self, _restore_pos)
        self.show_toast("Refreshed document 🔄")

    def _show_text_document(self, path: str, converter: str = "") -> None:
        """
        Shows a Markdown or plain text file as a page in the web view, in place of a PDF; converter names a
        CONVERTERS kind to turn it into HTML first.
        """
        if self.current_doc is not None:
            self._release_document()
        self.current_path = path
        self.text_document = True
        try:
            self._loaded_mtime = os.path.getmtime(path) if os.path.exists(path) else None
        except OSError:
            self._loaded_mtime = None
        self._update_tab_title(os.path.basename(path))
        self._forget_conversion()
        self.converted_pdf = None
        self._show_converted_banner()
        self.sidebar_nav.load_document("", None, self.theme_mode)
        self._set_sidebar_visible(False)
        kind = converter or document_kind(path)
        self._converter_kind = kind if kind in CONVERTERS else ""
        if self._converter_kind:
            self.convert_worker = ConvertWorker(kind, path)
            self.convert_worker.converted.connect(self._on_converted)
            self.convert_worker.failed.connect(self._on_convert_failed)
            track(self.convert_worker).start()
        try:
            self._render_text_page()
        except OSError as e:
            log.warning("Could not read %s: %s", path, e)
            self.show_toast(f"Could not read {os.path.basename(path)}")
        self.toolbar.show()
        self.stack.setCurrentIndex(1)
        self.view_mode = ViewMode.REFLOW
        if hasattr(self.window(), "_update_window_title"):
            self.window()._update_window_title()
        self.btn_facing.setEnabled(False)
        self.btn_ocr.setEnabled(False)
        self._update_protect_button()

    def _convert_office(self, path: str) -> None:
        """
        Starts converting an Office file; the tab shows a message until the PDF loads, or keeps the old pages on
        reload.
        """
        self._forget_conversion()
        reloading = path == self.current_path and self.current_doc is not None
        self.current_path = path
        self._update_tab_title(os.path.basename(path))
        if not reloading:
            self._show_message_page(f"Converting {os.path.basename(path)} with LibreOffice…")
        folder = None
        if self.incognito or getattr(self.window(), "incognito", False):
            if not self._office_folder:
                self._office_folder = tempfile.mkdtemp(prefix="riemann-office-")
            folder = self._office_folder
        self.convert_worker = OfficeWorker(path, self.settings.value(OFFICE_PATH_KEY, "", type=str), folder)
        self.convert_worker.converted.connect(self._on_office_converted)
        self.convert_worker.failed.connect(self._on_office_failed)
        self.convert_worker.missing.connect(self._on_office_missing)
        track(self.convert_worker).start()

    def _show_message_page(self, message: str) -> None:
        if self.current_doc is not None:
            self._release_document()
        self.converted_pdf = None
        self._show_converted_banner()
        self.text_document = False
        web_view = self._get_or_create_web_view()
        self._drop_text_page_file()
        web_view.setHtml(generate_document_html("", self.theme_mode != 0, message))
        self.stack.setCurrentIndex(1)
        self.view_mode = ViewMode.REFLOW
        if hasattr(self.window(), "_update_window_title"):
            self.window()._update_window_title()

    def _on_office_converted(self, path: str, pdf: str) -> None:
        if path != self.current_path:
            return
        self.convert_worker = None
        self.converted_pdf = pdf
        self._load_pdf(path, pdf, None, False)

    def _on_office_failed(self, path: str, message: str) -> None:
        if path != self.current_path:
            return
        self.convert_worker = None
        self._show_message_page(message)
        self.show_toast(f"Could not open {os.path.basename(path)}")

    def _on_office_missing(self, path: str) -> None:
        if path != self.current_path:
            return
        self.convert_worker = None
        fmt = basic_format(path)
        name = os.path.basename(path)
        if fmt and not is_spreadsheet(fmt) and packages_ready(fmt):
            self._show_text_document(path, converter="office")
            return
        if fmt and not is_spreadsheet(fmt):
            self._show_message_page(
                f"Riemann needs LibreOffice, or the basic-view packages, to show {name}. "
                "Settings > Documents can install LibreOffice."
            )
            return
        self._show_message_page(f"Riemann needs LibreOffice to show {name}, and it isn't installed.")

    def _show_converted_banner(self) -> None:
        watching = bool(self.converted_pdf) and not self.is_preview
        if self.converted_banner is not None:
            self.converted_banner.setVisible(watching)
        if watching:
            if self._office_timer is None:
                self._office_timer = QTimer(self)
                self._office_timer.setInterval(OFFICE_POLL_MS)
                self._office_timer.timeout.connect(self._check_office_source)
            self._office_seen_mtime = self._loaded_mtime
            self._office_timer.start()
        elif self._office_timer is not None:
            self._office_timer.stop()

    def _check_office_source(self) -> None:
        if not self.converted_pdf or self.convert_worker is not None or not self.current_path:
            return
        if self.load_worker is not None and shiboken6.isValid(self.load_worker) and self.load_worker.isRunning():
            return
        try:
            mtime = os.path.getmtime(self.current_path)
        except OSError:
            return
        if mtime == self._loaded_mtime:
            self._office_seen_mtime = mtime
            return
        if mtime != self._office_seen_mtime:
            self._office_seen_mtime = mtime
            return
        self._loaded_mtime = mtime
        self.pending_initial_page = self.current_page_index
        self.show_toast(f"{os.path.basename(self.current_path)} changed, updating…")
        self.load_document(self.current_path)

    def edit_in_office(self) -> None:
        """Opens the Office file in LibreOffice's editor; the tab follows its saves."""
        office = find_office(self.settings.value(OFFICE_PATH_KEY, "", type=str))
        if office is None or not self.current_path:
            self.show_toast("LibreOffice isn't installed.")
            return
        try:
            open_in_office(self.current_path, office)
        except OSError as e:
            self.show_toast(f"Could not start LibreOffice: {e}")
            return
        self.show_toast("Opening in LibreOffice. This tab updates when you save there.")

    def save_converted_pdf(self) -> None:
        """Saves the converted PDF of an Office file where the person chooses, then shows that file here."""
        stem = os.path.splitext(os.path.basename(self.current_path))[0]
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save as PDF",
            os.path.join(get_dialog_directory(self.settings), stem + ".pdf"),
            "PDF Files (*.pdf)",
        )
        if not dest:
            return
        if not dest.lower().endswith(".pdf"):
            dest += ".pdf"
            if os.path.exists(dest):
                answer = QMessageBox.question(
                    self, "Save as PDF", f"{os.path.basename(dest)} already exists. Replace it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        save_last_directory(self.settings, dest)
        try:
            shutil.copyfile(self.converted_pdf, dest)
        except OSError as e:
            QMessageBox.critical(self, "Save as PDF", f"Could not save the PDF:\n{e}")
            return
        self.open_here(dest)

    def save_page_as_pdf(self) -> None:
        """Prints the page shown in the web view (a text file, e-book or basic view) to a PDF the person names."""
        if self.web is None:
            return
        stem = os.path.splitext(os.path.basename(self.current_path))[0]
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save as PDF",
            os.path.join(get_dialog_directory(self.settings), stem + ".pdf"),
            "PDF Files (*.pdf)",
        )
        if not dest:
            return
        if not dest.lower().endswith(".pdf"):
            dest += ".pdf"
            if os.path.exists(dest):
                answer = QMessageBox.question(
                    self, "Save as PDF", f"{os.path.basename(dest)} already exists. Replace it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        save_last_directory(self.settings, dest)
        page = self.web.page()

        def done(path: str, ok: bool) -> None:
            try:
                page.pdfPrintingFinished.disconnect(done)
            except (RuntimeError, TypeError):
                pass
            if path == dest:
                self.show_toast(f"Saved {os.path.basename(dest)}" if ok else "Could not save the PDF")

        page.pdfPrintingFinished.connect(done)
        page.printToPdf(dest)

    def _forget_conversion(self) -> None:
        worker, self.convert_worker = self.convert_worker, None
        if worker is not None and shiboken6.isValid(worker):
            worker.conversion.cancel()
            release(worker, worker.converted, worker.failed)
        self._converted = None
        self._convert_error = ""
        self._document_sections = ()

    def _on_converted(self, path: str, result: dict) -> None:
        if path != self.current_path or not self.text_document:
            return
        self.convert_worker = None
        self._converted = result
        self._document_sections = tuple(tuple(entry) for entry in result.get("sections") or ())
        if self._document_sections and not self.is_preview:
            self.sidebar_nav.show_sections(list(self._document_sections))
            self._set_sidebar_visible(self.settings.value(SIDEBAR_VISIBLE_KEY, False, type=bool))
        self._render_text_page()

    def _on_convert_failed(self, path: str, message: str) -> None:
        if path != self.current_path or not self.text_document:
            return
        self.convert_worker = None
        self._convert_error = f"Riemann couldn't open {os.path.basename(path)}. {message}"
        self.show_toast(f"Could not open {os.path.basename(path)}")
        self._render_text_page()

    def scroll_to_anchor(self, anchor: str) -> None:
        """Scrolls a converted document's page to the element with id anchor ("#id")."""
        if self.web is None or not anchor.startswith("#"):
            return
        target = json.dumps(anchor[1:])
        self.web.page().runJavaScript(
            f"(function(){{var e=document.getElementById({target});if(e)e.scrollIntoView();}})()"
        )

    def _converted_page_html(self) -> str:
        dark = self.theme_mode != 0
        if self._converted is not None:
            return generate_document_html(self._converted.get("html", ""), dark, self._converted.get("note", ""))
        message = self._convert_error or f"Opening {os.path.basename(self.current_path)}…"
        return generate_document_html("", dark, message)

    def _text_page_html(self) -> str:
        path = self.current_path
        if self._converter_kind:
            return self._converted_page_html()
        with open(path, "rb") as f:
            data = f.read(TEXT_VIEW_LIMIT + 1)
        note = ""
        if len(data) > TEXT_VIEW_LIMIT:
            data = data[:TEXT_VIEW_LIMIT]
            size_mb = os.path.getsize(path) / (1024 * 1024)
            note = f"Showing the first {TEXT_VIEW_LIMIT // (1024 * 1024)} MB of {size_mb:.0f} MB."
        text = data.decode("utf-8", errors="replace")
        dark = self.theme_mode != 0
        if path.lower().endswith(MARKDOWN_SUFFIXES):
            return generate_markdown_html(text, dark, note)
        return generate_text_html(text, path, dark, note)

    def _render_text_page(self) -> None:
        page = self._text_page_html().encode("utf-8")
        web_view = self._get_or_create_web_view()
        web_view.page().settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False
        )
        web_view.setZoomFactor(self.calculate_scale())
        if len(page) <= SET_HTML_LIMIT:
            self._drop_text_page_file()
            web_view.setHtml(page.decode("utf-8"))
            return
        if not self._text_page_file:
            folder = os.path.join(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation), TEXT_VIEW_FOLDER
            )
            os.makedirs(folder, exist_ok=True)
            handle, self._text_page_file = tempfile.mkstemp(suffix=".html", dir=folder)
            os.close(handle)
        with open(self._text_page_file, "wb") as f:
            f.write(page)
        web_view.load(QUrl.fromLocalFile(self._text_page_file))

    def _drop_text_page_file(self) -> None:
        page_file, self._text_page_file = self._text_page_file, None
        if page_file:
            try:
                os.remove(page_file)
            except OSError:
                pass

    def save_document(self) -> None:
        """
        Saves a copy of the document, with its annotations embedded when it has any; with page edits waiting, offers
        to save them into the file.
        """
        if not self.current_path or not os.path.exists(self.current_path):
            QMessageBox.warning(self, "Save Error", "No document loaded.")
            return
        if self.converted_pdf:
            self.save_converted_pdf()
            return
        if self.text_document:
            self.save_page_as_pdf()
            return
        if self.has_unsaved_page_edits():
            box = QMessageBox(self)
            box.setWindowTitle("Save")
            box.setText(
                f"Save the page changes to {os.path.basename(self.current_path)}, or save the edited document as a "
                "copy?"
            )
            save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Save a Copy…", QMessageBox.ButtonRole.ActionRole)
            cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is save:
                self.save_page_edits()
                return
            if box.clickedButton() is cancel:
                return

        start_dir = get_dialog_directory(self.settings)
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save PDF As",
            os.path.join(start_dir, os.path.basename(self.current_path)),
            "PDF Files (*.pdf)",
        )

        if dest:
            save_last_directory(self.settings, dest)
            try:
                written = False
                if any(self.annotations.values()) and self.current_path.lower().endswith(".pdf"):
                    try:
                        written = pdf_annotations.write_annotations(
                            self.pdf_path(), self._annotation_snapshot(), dest=dest
                        )
                    except Exception as e:
                        print(f"[Warning] Could not add annotations to the copy: {e}")
                if not written:
                    shutil.copy2(self.pdf_path(), dest)
                if self.form_values_cache and self.current_path.lower().endswith(".pdf"):
                    try:
                        pdf_forms.write_form_values(dest, dict(self.form_values_cache))
                    except Exception as e:
                        print(f"[Warning] Could not add form entries to the copy: {e}")
                QMessageBox.information(self, "Success", f"Saved to {dest}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file:\n{e}")

    def export_annotations(self) -> None:
        """Writes the document's annotations to a Markdown file the person chooses."""
        if not self.current_path or not self.annotations:
            QMessageBox.information(self, "Export", "No annotations to export.")
            return

        default_name = (
            os.path.splitext(os.path.basename(self.current_path))[0] + "_notes.md"
        )
        start_dir = get_dialog_directory(self.settings)
        dest_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Notes",
            os.path.join(start_dir, default_name),
            "Markdown (*.md)",
        )

        if not dest_path:
            return

        save_last_directory(self.settings, dest_path)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with open(dest_path, "w", encoding="utf-8") as f:
                doc_title = os.path.basename(self.current_path)
                f.write(f"# Notes: {doc_title}\n\n")

                sorted_pages = sorted(self.annotations.keys(), key=lambda x: int(x))

                for pid in sorted_pages:
                    page_idx = int(pid)
                    page_num = page_idx + 1
                    f.write(f"## Page {page_num}\n\n")

                    for anno in self.annotations[pid]:
                        atype = anno.get("type")
                        if atype in ("note", "text"):
                            content = anno.get("text", "").replace("\n", "\n> ")
                            if content:
                                f.write(f"- **Note:** {content}\n")
                        elif atype == "markup":
                            subtype = anno.get("subtype", "highlight")
                            f.write(f"- *{subtype.capitalize()}*\n")

                    f.write("\n---\n")

            QApplication.restoreOverrideCursor()
            self.show_toast(f"Exported to {os.path.basename(dest_path)}")

        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export Failed", str(e))

    def open_pdf_dialog(self) -> None:
        start_dir = get_dialog_directory(self.settings)
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Document",
            start_dir,
            dialog_filter(html=False),
        )
        if not paths:
            return

        save_last_directory(self.settings, paths[0])
        main_win = self.window()
        self.open_here(paths[0])
        if hasattr(main_win, "new_pdf_tab"):
            for path in paths[1:]:
                main_win.new_pdf_tab(path)

    def open_here(self, path: str) -> None:
        main_win = self.window()
        if hasattr(main_win, "open_from_reader"):
            main_win.open_from_reader(self, path)
        else:
            self.load_document(path)

    def _update_protect_button(self) -> None:
        """
        The Protect button offers a password-protected copy, or, while an
        encrypted PDF is shown, removing its password. The toolbar is laid out
        again when that changes, so menus and the ribbon pick up the new label.
        """
        unlock = self.current_doc is not None and self.document_encrypted
        button = self.btn_secure_export
        button.setIcon(self._get_icon("file-key.svg" if unlock else "file-lock.svg"))
        button.setToolTip(
            "Remove the password from this PDF (Ctrl+Shift+S)"
            if unlock
            else "Export a password-protected copy of this PDF (Ctrl+Shift+S)"
        )
        if bool(button.property("riemannUnlock")) == unlock:
            return
        button.setProperty("riemannUnlock", unlock)
        button.setProperty("riemannLabel", "Remove password…" if unlock else None)
        button.setProperty("riemannShort", "Unlock" if unlock else None)
        layout = getattr(self, "toolbar_layout", None)
        if layout is not None:
            layout.apply()

    def protect_or_unlock(self) -> None:
        if self.converted_pdf:
            self.show_toast("Save as PDF (Ctrl+S) first to protect it.")
            return
        if self.current_doc is not None and self.document_encrypted:
            self.remove_password()
        else:
            self.export_secure_pdf()

    def remove_password(self) -> None:
        """Removes the password from this file (through the scratch copy, so on Save) or writes an unlocked copy."""
        if self.current_doc is None or not self.document_encrypted:
            return
        name = os.path.basename(self.current_path)
        box = QMessageBox(self)
        box.setWindowTitle("Remove password")
        box.setText(f"Remove the password from {name}?")
        box.setInformativeText(
            "Riemann uses the password you opened it with, and everything happens on this computer. "
            "Removing it from this file takes effect when you save. An unlocked copy is written "
            "straight away, and this file keeps its password."
        )
        in_file = box.addButton("Remove from this file", QMessageBox.ButtonRole.AcceptRole)
        as_copy = box.addButton("Save an unlocked copy…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is in_file:
            self._edit_pages(page_edit.remove_password, done="Removed the password.")
        elif box.clickedButton() is as_copy:
            self._save_unlocked_copy()

    def _save_unlocked_copy(self) -> None:
        stem = os.path.splitext(os.path.basename(self.current_path))[0]
        start = os.path.join(get_dialog_directory(self.settings), f"{stem}_unlocked.pdf")
        dest, _ = QFileDialog.getSaveFileName(self, "Save an unlocked copy", start, "PDF Files (*.pdf)")
        if not dest:
            return
        save_last_directory(self.settings, dest)
        self.flush_annotations_to_pdf()
        try:
            page_edit.save_unlocked_copy(self.pdf_path(), dest, self.document_password)
        except Exception as e:
            QMessageBox.warning(self, "Remove password", str(e) or type(e).__name__)
            return
        self.show_toast(f"Saved an unlocked copy as {os.path.basename(dest)}")

    def export_secure_pdf(self) -> None:
        """Prompts the user for a password and saves an encrypted copy."""
        if not self.current_path:
            return

        password, ok = QInputDialog.getText(
            self,
            "Secure PDF",
            "Enter a password to lock this PDF:",
            QLineEdit.EchoMode.Password,
        )

        if not ok or not password:
            return

        start_dir = get_dialog_directory(self.settings)
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Encrypted PDF",
            os.path.join(
                start_dir,
                os.path.splitext(os.path.basename(self.current_path))[0] + "_secure.pdf",
            ),
            "PDF Files (*.pdf)",
        )

        if not save_path:
            return

        save_last_directory(self.settings, save_path)
        try:
            import pikepdf

            with pikepdf.Pdf.open(self.current_path) as pdf:
                encryption = pikepdf.Encryption(
                    user=password,
                    owner=password,
                    allow=pikepdf.Permissions(extract=False, modify_assembly=False),
                )

                pdf.save(save_path, encryption=encryption)

            QMessageBox.information(
                self, "Success", "Encrypted PDF saved successfully!"
            )

        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to encrypt PDF:\n{e!s}"
            )

    def _get_or_create_web_view(self) -> QWebEngineView:
        """Instantiates the Chromium view only when actively needed."""
        if self.web is None:
            self.web = QWebEngineView()
            self.web.installEventFilter(self)
            self.stack.removeWidget(self._web_placeholder)
            self.stack.insertWidget(1, self.web)
            self.web.page().setBackgroundColor(QColor(tokens(self.theme_mode != 0)["canvas"]))
        return self.web

    def print_document(self) -> None:
        """
        Opens the system print dialog and prints the current document.
        Handles both REFLOW (WebEngine) and IMAGE (PDF) view modes.
        """
        if not self.current_path and self.view_mode != ViewMode.REFLOW:
            self.show_toast("No document to print.")
            return

        printer = QPrinter()
        printer.setResolution(300)
        dialog = QPrintDialog(printer, self)

        if dialog.exec() == QPrintDialog.DialogCode.Accepted:
            self.show_toast("Preparing print job...")

            if self.view_mode == ViewMode.REFLOW:
                self.web.page().print(
                    printer,
                    lambda success: self.show_toast(
                        "Print complete!" if success else "Print failed."
                    ),
                )
                return

            if self.current_doc:
                num_pages = self.current_doc.page_count
                progress = QProgressDialog(
                    "Preparing print job...", "Cancel", 0, num_pages, self
                )
                progress.setWindowTitle("Printing PDF")
                progress.setWindowModality(Qt.WindowModality.WindowModal)
                progress.setMinimumDuration(0)
                painter = QPainter()
                if painter.begin(printer):
                    try:
                        for page_idx in range(num_pages):
                            QApplication.processEvents()
                            if progress.wasCanceled():
                                printer.abort()
                                self.show_toast("Printing canceled.")
                                break

                            progress.setLabelText(
                                f"Rendering page {page_idx + 1} of {num_pages}..."
                            )
                            progress.setValue(page_idx)
                            if page_idx > 0:
                                printer.newPage()

                            res = (self.page_renderer or self.current_doc).render_page(
                                page_idx, 2.5, self.theme_mode
                            )

                            if res and res.data:
                                img = QImage(
                                    res.data,
                                    res.width,
                                    res.height,
                                    QImage.Format.Format_RGB32,
                                )
                                pixmap = QPixmap.fromImage(img)
                                rect = printer.pageRect(QPrinter.Unit.DevicePixel)
                                print_w = int(rect.width())
                                print_h = int(rect.height())

                                scaled_pixmap = pixmap.scaled(
                                    print_w,
                                    print_h,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.FastTransformation,
                                )

                                x = int((rect.width() - scaled_pixmap.width()) / 2)
                                y = int((rect.height() - scaled_pixmap.height()) / 2)

                                painter.drawPixmap(x, y, scaled_pixmap)

                        if not progress.wasCanceled():
                            progress.setValue(num_pages)
                            self.show_toast("Print complete!")
                        else:
                            raise Exception

                    except Exception as e:
                        self.show_toast(f"Print failed: {e!s}")
                        print(e)
                    finally:
                        painter.end()

    def cleanup(self) -> None:
        """Explicitly severs references to heavy resources to trigger native destructors."""
        self.notes_panel.save()
        self._drop_text_page_file()
        self._forget_conversion()
        if self._office_timer is not None:
            self._office_timer.stop()
        if self._office_folder:
            shutil.rmtree(self._office_folder, ignore_errors=True)
            self._office_folder = None
        try:
            self.flush_forms_to_pdf()
        except Exception as e:
            print(f"[Warning] Could not store form entries in the PDF: {e}")
        try:
            self.flush_annotations_to_pdf()
        except Exception as e:
            print(f"[Warning] Could not store annotations in the PDF: {e}")
        self.sidebar_nav.shutdown()
        self.scroll_timer.stop()
        self.autoscroll_timer.stop()
        self._stop_render_worker()
        self._replace_page_renderer(None)
        PAGE_CACHE.drop_owner(self._render_owner())
        if self._zoom_debounce_timer is not None:
            self._zoom_debounce_timer.stop()
        if self._rebuild_debounce_timer is not None:
            self._rebuild_debounce_timer.stop()

        if self.load_worker:
            self.load_worker.engine = None
            release(self.load_worker, self.load_worker.loaded, self.load_worker.error)
            self.load_worker = None
        worker = self._live_metadata_worker()
        if worker is not None:
            release(worker, worker.finished_extraction)
        self.metadata_worker = None
        if self.sig_worker is not None:
            release(self.sig_worker, self.sig_worker.finished_validation)
            self.sig_worker = None

        self.rendered_pages.clear()
        self.text_segments_cache.clear()
        self.form_values_cache.clear()

        for w in self.page_widgets.values():
            w.clear()
            w.setParent(None)
            self.scroll_layout.removeWidget(w)
            w.deleteLater()
        self.page_widgets.clear()

        for widgets_list in self.form_widgets.values():
            for fw in widgets_list:
                fw.deleteLater()
        self.form_widgets.clear()

        self.current_doc = None
        self.engine = None
        self._drop_scratch()

        if self.web is not None:
            try:
                if shiboken6.isValid(self.web):
                    self.web.stop()
                    page = self.web.page()
                    if page and shiboken6.isValid(page):
                        profile = page.profile()
                        if profile:
                            profile.clearHttpCache()
                        page.deleteLater()

                    self.web.setHtml("")
                    self.web.setParent(None)
                    self.web.deleteLater()
            except RuntimeError:
                pass

            self.web = None

        self._kill_ai_engine()

        if hasattr(self.window(), "_caption_worker"):
            worker = self.window()._caption_worker
            if worker and worker.isRunning():
                worker.requestInterruption()
                worker.quit()
                worker.wait(1000)

        QPixmapCache.clear()
        QApplication.processEvents()
        gc.collect()
