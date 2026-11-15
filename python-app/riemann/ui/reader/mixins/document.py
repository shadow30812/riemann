"""
Loading, reloading, saving, exporting and printing documents, and releasing them when the tab closes.

Part of ReaderTab (riemann/ui/reader/tab.py). ReaderTab lists this mixin before
QWidget, so its Qt event handlers override QWidget's.

Notes:

load_document: Edits waiting for the debounced save belong to the current file.

_on_document_loaded: Preview tabs skip annotations, comments, signature checks
and metadata extraction; that is what keeps them light.
"""

import gc
import os
import shutil
import sys
import urllib.parse
from typing import Optional
import shiboken6
from PySide6.QtCore import (
    QThread,
    Signal,
    Qt,
    QTimer,
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
from ....core.constants import ViewMode
from ....core import pdf_annotations, pdf_forms
from ....core.managers import PasswordDialog
from ..render_worker import PAGE_CACHE, PageRenderWorker
from ....core.render_process import PageRenderer
from ..utils import generate_markdown_html
from ....core.log import log
from ....core.settings import get_dialog_directory, save_last_directory
from ...palette import tokens
from .host import ReaderHostBase


class DocumentLoadWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, engine, path, password):
        super().__init__()
        self.engine = engine
        self.path = path
        self.password = password

    def run(self):
        try:
            doc = self.engine.load_document(self.path, self.password)
            self.finished.emit(doc)
        except Exception as e:
            self.error.emit(str(e))


class DocumentMixin(ReaderHostBase):
    web = None
    load_worker = None
    _loaded_mtime = None
    pending_initial_page = None
    _char_geometry_cache = None

    def load_document(
        self,
        path: str,
        restore_state: bool = False,
        password: Optional[str] = None,
        is_retry: bool = False,
    ) -> None:
        """
        Loads a PDF or Markdown file on a background thread. restore_state goes back to
        where it was last read.
        """
        if not is_retry and self.current_path:
            self.flush_annotations_to_pdf()

        if path.lower().endswith(".md"):
            self._load_markdown(path)
            return

        self.load_progress = QProgressDialog(
            f"Loading {os.path.basename(path)}...", None, 0, 0, self
        )
        self.load_progress.setWindowTitle("Please Wait")
        self.load_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.load_progress.setCancelButton(None)
        self.load_progress.show()

        self._load_path = path
        self._load_restore_state = restore_state
        self._load_password = password
        self._load_is_retry = is_retry

        self.load_worker = DocumentLoadWorker(self.engine, path, password)
        self.load_worker.finished.connect(self._on_document_loaded)
        self.load_worker.error.connect(self._on_document_load_error)
        self.load_worker.start()

    def _on_document_loaded(self, doc):
        """Callback for when the thread successfully returns the PDF."""
        self.load_progress.accept()
        self.current_doc = doc
        self._replace_page_renderer(PageRenderer(doc, self._load_path, self._load_password))
        self.current_signatures = []
        self.signature_appearances = {}
        self._reset_document_caches()
        self.reset_navigation_history()
        self._forms_blocked = False
        self._reference_cache = {}
        if self.notes_panel.isVisible() or self.settings.value("reader/notes_visible", False, type=bool):
            self.notes_panel.load(self.current_path)
            self.notes_panel.show()
            self.btn_notes.setChecked(True)
        self._start_render_worker()

        path = self._load_path
        restore_state = self._load_restore_state
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
                50, lambda: self.show_toast("Document unlocked successfully.")
            )

        if not self.is_preview:
            self.toolbar.show()
            self.sidebar_nav.load_document(path, self.page_renderer, self.theme_mode)
            self._set_sidebar_visible(
                self.settings.value("reader/sidebar_visible", False, type=bool)
            )
        self.stack.setCurrentIndex(0)
        self.view_mode = ViewMode.IMAGE
        if hasattr(self.window(), "_update_window_title"):
            self.window()._update_window_title()

        if not self.is_preview:
            self.load_annotations()
            self._load_external_comments_async(path)
            QTimer.singleShot(500, lambda: self._detect_signatures(path))

        initial_page = self.pending_initial_page
        self.pending_initial_page = None
        if initial_page is not None:
            self.current_page_index = max(
                0, min(int(initial_page), self.current_doc.page_count - 1)
            )
            self.rebuild_layout()
            self.jump_to_page(self.current_page_index)
        elif restore_state:
            saved_page = self.settings.value("lastPage", 0, type=int)
            saved_scroll = self.settings.value("lastScrollY", 0, type=int)
            self.current_page_index = min(saved_page, self.current_doc.page_count - 1)
            self.rebuild_layout()
            self.update_view()
            QTimer.singleShot(
                100, lambda: self.scroll.verticalScrollBar().setValue(saved_scroll)
            )
        else:
            self.current_page_index = 0
            self.rebuild_layout()
            self.update_view()

        if not self.is_preview:
            QTimer.singleShot(2000, self.extract_document_metadata)

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
            self._render_worker.start()
        self._render_worker.set_document(self.page_renderer, self._render_owner())

    def _replace_page_renderer(self, renderer: Optional[PageRenderer]) -> None:
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
        except (RuntimeError, TypeError):
            pass
        worker.stop()
        if not worker.wait(3000):
            worker.terminate()
            worker.wait()
        worker.deleteLater()

    def _on_document_load_error(self, err_str):
        """Callback if the background thread encounters a loading exception."""
        self.load_progress.accept()
        path = self._load_path
        restore_state = self._load_restore_state
        is_retry = self._load_is_retry

        err_str_lower = err_str.lower()
        if "password" in err_str_lower or "encrypted" in err_str_lower:
            error_text = "Incorrect password. Please try again." if is_retry else None
            dialog = PasswordDialog(self, error_msg=error_text)
            if dialog.exec():
                pw = dialog.get_password()
                if pw:
                    self.load_document(path, restore_state, pw, is_retry=True)
            return

        QMessageBox.critical(
            self,
            "Document Load Error",
            f"Could not load the document. Please ensure it is a valid PDF format.\n\nDetails: {err_str}",
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
        self.load_document(self.current_path, restore_state=False)

        def _restore_pos():
            if self.current_doc:
                self.current_page_index = min(
                    saved_page, self.current_doc.page_count - 1
                )
                self.ensure_visible(self.current_page_index)
            self.scroll.verticalScrollBar().setValue(saved_scroll)

        QTimer.singleShot(350, _restore_pos)
        self.show_toast("Refreshed document 🔄")

    def _load_markdown(self, path: str) -> None:
        self.current_path = path
        try:
            self._loaded_mtime = os.path.getmtime(path) if os.path.exists(path) else None
        except Exception:
            self._loaded_mtime = None
        self._update_tab_title(os.path.basename(path))

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            full_html = generate_markdown_html(text, self.theme_mode != 0)

            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                base_path = getattr(sys, "_MEIPASS")
                font_path = os.path.join(
                    base_path, "riemann", "assets", "fonts", "NotoColorEmoji.ttf"
                )
            else:
                base_path = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                )
                font_path = os.path.join(
                    base_path, "assets", "fonts", "NotoColorEmoji.ttf"
                )

            font_uri = "file:///" + urllib.parse.quote(font_path.replace("\\", "/"))

            emoji_style = f"""
            <style>
                @font-face {{
                    font-family: "Riemann Noto Emoji";
                    src: url("{font_uri}") format("truetype");
                }}
                body, p, span, div, h1, h2, h3, h4, h5, h6, table, th, td, li, pre, code {{ 
                    font-family: inherit, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Riemann Noto Emoji", "Twemoji Mozilla" !important; 
                }}
            </style>
            """

            full_html += emoji_style
            web_view = self._get_or_create_web_view()

            web_view.page().settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False
            )
            web_view.setZoomFactor(self.calculate_scale())

            web_view.setHtml(full_html)
            self.toolbar.show()
            self.stack.setCurrentIndex(1)
            self.view_mode = ViewMode.REFLOW

            if hasattr(self.window(), "_update_window_title"):
                self.window()._update_window_title()

            self.btn_facing.setEnabled(False)
            self.btn_ocr.setEnabled(False)

        except Exception as e:
            sys.stderr.write(f"Markdown Load Error: {e}\n")

    def save_document(self) -> None:
        """Saves a copy of the document, with its annotations embedded when it has any."""
        if not self.current_path or not os.path.exists(self.current_path):
            QMessageBox.warning(self, "Save Error", "No document loaded.")
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
                            self.current_path, self._annotation_snapshot(), dest=dest
                        )
                    except Exception as e:
                        print(f"[Warning] Could not add annotations to the copy: {e}")
                if not written:
                    shutil.copy2(self.current_path, dest)
                if self.form_values_cache and self.current_path.lower().endswith(".pdf"):
                    try:
                        pdf_forms.write_form_values(dest, dict(self.form_values_cache))
                    except Exception as e:
                        print(f"[Warning] Could not add form entries to the copy: {e}")
                QMessageBox.information(self, "Success", f"Saved to {dest}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file:\n{e}")

    def export_annotations(self) -> None:
        """
        Traverses deeply nested annotation JSON layouts outputting clean markdown textual variants suitable for academic review.
        """
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
            "Open PDF",
            start_dir,
            "Supported Files (*.pdf *.PDF *.md);;All Files (*)",
        )
        if not paths:
            return

        save_last_directory(self.settings, paths[0])
        self.load_document(paths[0])

        if len(paths) > 1:
            main_win = self.window()
            if hasattr(main_win, "new_pdf_tab"):
                for path in paths[1:]:
                    if hasattr(main_win, "add_to_history"):
                        main_win.add_to_history(path, "pdf")
                    main_win.new_pdf_tab(path)

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
                os.path.basename(self.current_path).replace(".pdf", "_secure.pdf"),
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
                self, "Export Error", f"Failed to encrypt PDF:\n{str(e)}"
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
                        self.show_toast(f"Print failed: {str(e)}")
                        print(e)
                    finally:
                        painter.end()

    def cleanup(self) -> None:
        """Explicitly severs references to heavy resources to trigger native destructors."""
        self.notes_panel.save()
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
            try:
                self.load_worker.finished.disconnect()
                self.load_worker.error.disconnect()
            except Exception:
                log.debug("cleanup failed", exc_info=True)
            self.load_worker.engine = None
            if self.load_worker.isRunning():
                self.load_worker.requestInterruption()
                self.load_worker.quit()

                if not self.load_worker.wait(1000):
                    print("Worker thread stuck! Forcing termination...")
                    self.load_worker.terminate()
                    self.load_worker.wait()

            try:
                self.load_worker.deleteLater()
            except RuntimeError:
                pass
            self.load_worker = None

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
