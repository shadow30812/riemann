"""
AI Mixin.

Handles OCR (Tesseract), Math Snipping (Pix2Tex) and AI semantic search.

OCR of the current page uses OcrThread; "Recognise text in pages…" in the
Pages menu uses OcrPagesThread, which reads eight pages per Tesseract process,
shows progress with a Cancel button, and opens the text in a window that can
save it as a .txt file. A tab runs one OCR job at a time.

Semantic search talks to the engine in riemann.core.ai_engine (the server is
core/ai_server.py), shared by all tabs, over its HTTP API (/index, /search).
Each tab sends its requests one at a time, so a search never overtakes the
indexing of its document. While the engine starts, the connection is refused;
while it downloads or loads its model, it answers 503 "loading ..."; either
way the request is retried every second (for up to AI_CONNECT_ATTEMPTS and
AI_LOADING_ATTEMPTS seconds), and the loading detail is shown as
a toast when it changes. /index first goes without text; if the engine has no
cached vectors it answers "needs_text", and the tab reads every page's text
on a PageTextThread (from the open document, so password-protected files
work) and sends it ahead of anything else queued. A document is indexed when
the AI search bar opens or on the first search, not when it is opened, so the
engine only starts for people who use it. A reply about a document other than
the one the tab now shows is ignored.

Snip-to-LaTeX needs a model pack from the project's GitHub releases. It is
added to sys.path, so the download is checked against LATEX_PACK_SHA256 before
it is unpacked, and the packages it needs are installed at pinned versions.
"""

import io
import json
import os
import sys
import warnings
from typing import Any

from PIL import Image
from PySide6.QtCore import QBuffer, QObject, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QVBoxLayout,
)

from ....core import ai_engine
from ....core.settings import get_dialog_directory, save_last_directory
from ..widgets import PageWidget
from ..workers import (
    InferenceThread,
    InstallerThread,
    LoaderThread,
    ModelDownloader,
    OcrPagesThread,
    OcrThread,
    PageTextThread,
)
from .pages import format_page_ranges, parse_page_ranges
from ...palette import toast_style, tokens
from .host import ReaderHostBase

AI_CONNECT_ATTEMPTS = 90
AI_LOADING_ATTEMPTS = 1800
_ORPHANED_THREADS: set = set()
AI_INDEX_TIMEOUT_MS = 600_000
AI_SEARCH_TIMEOUT_MS = 60_000
LATEX_PACK_URL = "https://github.com/shadow30812/riemann/releases/download/v1.0/latex_ocr_modules.zip"
LATEX_PACK_SHA256 = "b5ce63262da29e1532fc4809b3b0f9dce01a7b05e99c083a64bb9faff6252ac0"


class AIEngineBridge(QObject):
    """Carries toasts and search results from background threads to the UI thread."""

    toast_requested = Signal(str)
    search_results_ready = Signal(list)


class AiMixin(ReaderHostBase):
    """
    Provides methods for AI-assisted features including OCR and mathematical snipping.
    Intended to be mixed into the main reader application context.
    """
    _ai_connect_attempts = 0
    _ai_indexed_path = None
    _ai_net = None
    _ai_queue = None
    ai_results = None
    _ai_reply = None
    _ocr_thread = None
    _ocr_progress = None
    _ai_text_thread = None
    _ai_loading_shown = ""
    _ai_loading_attempts = 0

    def toggle_snip_mode(self, checked: bool) -> None:
        self.is_snipping = checked
        self.btn_snip.setChecked(checked)
        if checked:
            self.is_annotating = False
            self.btn_annotate.setChecked(False)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if self.snip_band:
                self.snip_band.hide()

    def process_snip(self, label: PageWidget, rect: QRect) -> None:
        """Crops the selection from the page and sends it to LaTeX OCR."""
        pix = label.pixmap()
        if not pix:
            return
        dpr = pix.devicePixelRatio()
        cropped = pix.copy(
            int(rect.x() * dpr),
            int(rect.y() * dpr),
            int(rect.width() * dpr),
            int(rect.height() * dpr),
        )
        buf = QBuffer()
        buf.open(QBuffer.ReadWrite)
        cropped.save(buf, "PNG")
        self.run_latex_inference(Image.open(io.BytesIO(buf.data())))

    def perform_ocr_current_page(self) -> None:
        """
        Runs Tesseract OCR on the currently visible page in the background and
        shows the extracted text, or why it failed.
        """
        if not self.current_doc or self._ocr_busy():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        thread = OcrThread(self.current_doc, self.current_page_index, 2.0)
        thread.finished_ocr.connect(self._on_ocr_finished)
        thread.error_occurred.connect(self._on_ocr_failed)
        self._ocr_thread = thread
        thread.start()

    def _on_ocr_finished(self, page_index: int, text: str) -> None:
        QApplication.restoreOverrideCursor()
        if not text.strip():
            self.show_toast(f"OCR found no text on page {page_index + 1}.")
            return
        QInputDialog.getMultiLineText(self, "OCR Result", f"Text of page {page_index + 1}:", text)

    def _on_ocr_failed(self, message: str) -> None:
        QApplication.restoreOverrideCursor()
        QMessageBox.warning(self, "OCR Failed", message)

    def _ocr_busy(self) -> bool:
        running = self._ocr_thread
        if running is not None and running.isRunning():
            self.show_toast("OCR is already running in this tab.")
            return True
        return False

    def perform_ocr_pages(self, pages: list) -> None:
        if not self.current_doc or self._ocr_busy():
            return
        count = self.current_doc.page_count
        text, ok = QInputDialog.getText(
            self, "Recognise text", "Pages (for example 1-3, 5):", text=format_page_ranges(pages)
        )
        if not ok:
            return
        try:
            chosen = parse_page_ranges(text, count)
        except ValueError as e:
            QMessageBox.warning(self, "Recognise text", str(e))
            return
        if not chosen:
            return
        thread = OcrPagesThread(self.current_doc, chosen, 2.0)
        dialog = QProgressDialog(f"Recognising text in {len(chosen)} pages…", "Cancel", 0, len(chosen), self)
        dialog.setWindowTitle("Recognise text")
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        dialog.canceled.connect(thread.requestInterruption)
        thread.progress.connect(lambda done, total: dialog.setValue(done))
        thread.finished_ocr.connect(lambda results, problem: self._on_ocr_pages_finished(dialog, results, problem))
        self._ocr_thread = thread
        self._ocr_progress = dialog
        thread.start()

    def _on_ocr_pages_finished(self, dialog: QProgressDialog, results: list, problem: str) -> None:
        self._ocr_progress = None
        dialog.blockSignals(True)
        dialog.close()
        dialog.deleteLater()
        if problem and problem != "Cancelled":
            QMessageBox.warning(self, "OCR Failed", problem)
        if not results:
            return
        if not any(t.strip() for _, t in results):
            self.show_toast("OCR found no text on those pages.")
            return
        self._show_ocr_text(results)

    def _show_ocr_text(self, results: list) -> None:
        text = "\n\n".join(f"--- Page {page + 1} ---\n{body.strip()}" for page, body in results) + "\n"
        label = format_page_ranges([page for page, _ in results])
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Text of pages {label}")
        dialog.resize(640, 560)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(dialog)
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close, dialog)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save as text…")
        buttons.accepted.connect(lambda: self._save_ocr_text(text, label))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._ocr_text_dialog = dialog
        dialog.show()

    def _save_ocr_text(self, text: str, label: str) -> None:
        stem = os.path.splitext(os.path.basename(self.current_path or "document"))[0]
        start = os.path.join(get_dialog_directory(self.settings), f"{stem} (text of pages {label}).txt")
        dest, _ = QFileDialog.getSaveFileName(self, "Save recognised text", start, "Text Files (*.txt)")
        if not dest:
            return
        save_last_directory(self.settings, dest)
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            QMessageBox.warning(self, "Save recognised text", str(e))
            return
        self.show_toast(f"Saved text to {os.path.basename(dest)}")

    def run_latex_inference(self, img: Image.Image) -> None:
        """Runs LaTeX OCR on img, loading the model first if needed."""
        self._setup_external_env()
        if self.latex_model:
            self._execute_inference(img)
            return

        self._pending_snip_image = img
        self.toggle_snip_mode(False)

        self.progress = QProgressDialog("Initializing AI...", "Cancel", 0, 0, self)
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)

        self.loader_thread = LoaderThread()
        self.loader_thread.finished_loading.connect(self._on_model_loaded)
        self.loader_thread.error_occurred.connect(self._on_model_error)
        self.loader_thread.start()

    def _on_model_loaded(self, model: Any) -> None:
        self.progress.close()
        self.latex_model = model
        if self._pending_snip_image:
            self._execute_inference(self._pending_snip_image)
            self._pending_snip_image = None

    def _on_model_error(self, msg: str) -> None:
        """The model failed to load: offers to download it or install what it needs."""
        self.progress.close()
        if "not found" in msg and getattr(sys, "frozen", False):
            if (
                QMessageBox.question(
                    self,
                    "Download Required",
                    "Download models?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                == QMessageBox.Yes
            ):
                self.start_model_download()
            else:
                self._pending_snip_image = None
        elif "not found" in msg:
            if (
                QMessageBox.question(
                    self,
                    "Install Required",
                    "Install pip libs?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                == QMessageBox.Yes
            ):
                self.install_dependencies()
            else:
                self._pending_snip_image = None
        else:
            QMessageBox.critical(self, "Error", msg)
            self._pending_snip_image = None

    def install_dependencies(self) -> None:
        """
        Starts a background installer thread to fetch required pip dependencies for OCR.
        """
        self.progress = QProgressDialog("Installing...", None, 0, 0, self)
        self.progress.show()
        self.inst_thread = InstallerThread()
        self.inst_thread.finished_install.connect(
            lambda: (
                self.progress.close(),
                self.run_latex_inference(self._pending_snip_image),
            )
        )
        self.inst_thread.install_error.connect(
            lambda m: (self.progress.close(), QMessageBox.critical(self, "Error", m))
        )
        self.inst_thread.start()

    def start_model_download(self) -> None:
        """
        Initiates the background download and extraction of the external AI model pack.
        """
        self.downloader = ModelDownloader(
            LATEX_PACK_URL, self._get_external_module_dir(), LATEX_PACK_SHA256
        )

        self.dl_dialog = QProgressDialog(
            "Downloading AI Models...", "Cancel", 0, 100, self
        )
        self.dl_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dl_dialog.setAutoClose(True)

        self.downloader.progress.connect(self.dl_dialog.setValue)
        self.downloader.finished.connect(self.on_download_finished)
        self.dl_dialog.canceled.connect(self.downloader.cancel)
        self.dl_dialog.show()
        self.downloader.start()

    def on_download_finished(self, success: bool) -> None:
        if success:
            QMessageBox.information(self, "Success", "Models installed successfully.")
            self._setup_external_env()
            if self._pending_snip_image:
                self.run_latex_inference(self._pending_snip_image)
        else:
            self.dl_dialog.close()
            reason = getattr(self.downloader, "error", "") or "Download failed."
            if reason != "Download cancelled.":
                QMessageBox.critical(self, "Error", reason)
            self._pending_snip_image = None

    def _execute_inference(self, img: Image.Image) -> None:
        self.progress = QProgressDialog("Processing...", "Cancel", 0, 0, self)
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.show()

        self.inference_thread = InferenceThread(self.latex_model, img)
        self.inference_thread.finished_inference.connect(self._on_inference_finished)
        self.inference_thread.error_occurred.connect(self._on_inference_error)
        self.inference_thread.start()

    def _on_inference_finished(self, code: str) -> None:
        self.progress.close()
        self.toggle_snip_mode(False)
        QInputDialog.getMultiLineText(self, "LaTeX Result", "Code:", code)

    def _on_inference_error(self, error_msg: str) -> None:
        self.progress.close()
        self.toggle_snip_mode(False)
        QMessageBox.critical(self, "Inference Error", error_msg)

    def _get_external_module_dir(self) -> str:
        """Where the LaTeX OCR model pack lives on this operating system."""
        base = (
            os.getenv("APPDATA")
            if os.name == "nt"
            else os.path.expanduser("~/.local/share")
        )
        return os.path.join(base, "Riemann", "latex_modules")

    def _setup_external_env(self) -> None:
        """
        Appends the external module directory to the system path if running inside a frozen environment.
        """
        if getattr(sys, "frozen", False):
            d = self._get_external_module_dir()
            if os.path.exists(d) and d not in sys.path:
                sys.path.append(d)

    def _start_ai_engine(self) -> None:
        """
        Starts the AI sidecar shared by all reader tabs unless it is already running.
        """
        ai_engine.ensure_started()

    def show_toast(self, msg: str) -> None:
        self.lbl_toast = QLabel(self)
        self.lbl_toast.setStyleSheet(toast_style(tokens(self.theme_mode != 0)))
        self.lbl_toast.setText(msg)
        self.lbl_toast.adjustSize()
        self.lbl_toast.move(
            (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
        )
        self.lbl_toast.show()
        QTimer.singleShot(4000, self.lbl_toast.hide)

    def index_pdf_for_ai(self) -> None:
        """
        Asks the AI engine to index the active PDF, starting the engine if needed.
        """
        path = self.current_path
        if not path:
            return
        self._ai_indexed_path = path
        self._ai_send("index", {"pdf_path": path}, path)

    def ai_search(self, query: str) -> None:
        """
        Sends a semantic search query about the active PDF to the AI engine, indexing
        the PDF first when it has not been indexed yet.
        """
        path = self.current_path
        if not query.strip() or not path:
            return
        if self._ai_indexed_path != path:
            self.index_pdf_for_ai()
        self._ai_send("search", {"query": query, "top_k": 5, "pdf_path": path}, path)

    def _ai_send(self, endpoint: str, payload: dict, path: str) -> None:
        """Queues a request for the AI engine; requests are sent one at a time."""
        if self._ai_queue is None:
            self._ai_queue = []
        self._ai_queue.append((endpoint, payload, path))
        self._ai_pump()

    def _ai_pump(self) -> None:
        """
        Sends the next queued request unless one is in flight, starting the
        engine when needed. While the engine is still loading its model the
        connection is refused, and the request is retried every second.
        """
        if self._ai_reply is not None or not self._ai_queue or self._ai_text_thread is not None:
            return
        conn = ai_engine.ensure_started()
        if conn is None:
            self._ai_queue = []
            self.show_toast("AI Error: the engine could not be started.")
            return
        port, token = conn
        endpoint, payload, _ = self._ai_queue[0]

        if self._ai_net is None:
            self._ai_net = QNetworkAccessManager(self if isinstance(self, QObject) else None)
        request = QNetworkRequest(QUrl(f"http://127.0.0.1:{port}/{endpoint}"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"X-Riemann-Token", token.encode())
        request.setTransferTimeout(AI_INDEX_TIMEOUT_MS if endpoint == "index" else AI_SEARCH_TIMEOUT_MS)
        reply = self._ai_net.post(request, json.dumps(payload).encode())
        self._ai_reply = reply
        reply.finished.connect(lambda r=reply: self._on_ai_reply_finished(r))

    def _ai_retry_later(self) -> None:
        if isinstance(self, QObject):
            QTimer.singleShot(1000, self, self._ai_pump)
        else:
            QTimer.singleShot(1000, self._ai_pump)

    def _on_ai_reply_finished(self, reply) -> None:
        if reply is not self._ai_reply:
            reply.deleteLater()
            return
        self._ai_reply = None
        error = reply.error()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        body = bytes(reply.readAll().data())
        reply.deleteLater()

        if status is None and error == QNetworkReply.NetworkError.ConnectionRefusedError:
            attempts = self._ai_connect_attempts + 1
            self._ai_connect_attempts = attempts
            if attempts == 3:
                self.show_toast("AI: starting the engine (the first start can take half a minute)...")
            if attempts > AI_CONNECT_ATTEMPTS:
                self._ai_connect_attempts = 0
                self._ai_queue = []
                self._ai_indexed_path = None
                self.show_toast("AI Error: the engine did not start.")
                return
            self._ai_retry_later()
            return

        self._ai_connect_attempts = 0
        if not self._ai_queue:
            return
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = None
        detail = data.get("detail") if isinstance(data, dict) else None
        if status == 503 and isinstance(detail, str) and detail.startswith("loading"):
            self._ai_loading_attempts += 1
            if self._ai_loading_attempts > AI_LOADING_ATTEMPTS:
                self._ai_loading_attempts = 0
                self._ai_loading_shown = ""
                self._ai_queue = []
                self._ai_indexed_path = None
                self.show_toast("AI Error: the search model did not finish loading.")
                return
            if detail != self._ai_loading_shown:
                self._ai_loading_shown = detail
                self.show_toast(f"AI: {detail[len('loading'):].lstrip(': ') or 'loading the search model'}...")
            self._ai_retry_later()
            return
        self._ai_loading_shown = ""
        self._ai_loading_attempts = 0
        endpoint, _, path = self._ai_queue.pop(0)
        if status is None:
            data = {"detail": reply.errorString()}
            status = 0
        self._on_ai_reply(endpoint, path, int(status), data)
        self._ai_pump()

    def _on_ai_reply(self, endpoint: str, path: str, status: int, data: Any) -> None:
        """
        Handles a finished AI engine request (endpoint "index" or "search"): shows
        indexing progress, errors, or the search results. status is the HTTP status, 0
        when the request failed. Replies about a document other than the one this tab
        now shows are ignored.
        """
        if path != self.current_path:
            return
        if status != 200:
            detail = data.get("detail") if isinstance(data, dict) else None
            if endpoint == "index" or status == 400:
                self._ai_indexed_path = None
            self.show_toast(f"AI Error: {detail or f'HTTP {status}'}")
            return
        if endpoint == "index":
            if isinstance(data, dict) and data.get("status") == "needs_text":
                self._ai_read_pages(path)
                return
            self.show_toast("AI Engine is ready! ✨")
            return
        if not data:
            self.show_toast("No semantic matches found.")
            return
        self.ai_results = data
        self.ai_result_idx = 0
        self._render_ai_result()

    def _ai_read_pages(self, path: str) -> None:
        """Extracts the document's text off the UI thread, then sends it to /index ahead of anything queued."""
        if self.current_doc is None:
            return
        thread = PageTextThread(self.current_doc, path)
        thread.finished_text.connect(self._on_ai_pages_read)
        thread.error_occurred.connect(self._on_ai_pages_failed)
        self._ai_text_thread = thread
        self.show_toast("AI: reading the document...")
        thread.start()

    def _on_ai_pages_read(self, path: str, pages: list) -> None:
        self._ai_text_thread = None
        if self._ai_queue is None:
            self._ai_queue = []
        self._ai_queue.insert(0, ("index", {"pdf_path": path, "pages": pages}, path))
        self._ai_pump()

    def _on_ai_pages_failed(self, message: str) -> None:
        self._ai_text_thread = None
        self._ai_indexed_path = None
        self._ai_queue = []
        self.show_toast(f"AI Error: {message}")

    def ai_find_next(self) -> None:
        """
        Advances the focus cycle to the next sequential AI search result and triggers a re-render.
        """
        if self.ai_results:
            self.ai_result_idx = (self.ai_result_idx + 1) % len(self.ai_results)
            self._render_ai_result()

    def ai_find_prev(self) -> None:
        """
        Reverts the focus cycle to the previous sequential AI search result and triggers a re-render.
        """
        if self.ai_results:
            self.ai_result_idx = (self.ai_result_idx - 1) % len(self.ai_results)
            self._render_ai_result()

    def _render_ai_result(self) -> None:
        """
        Updates the UI layout and view state to visually frame the active AI search match.
        """
        match = self.ai_results[self.ai_result_idx]

        page_idx = match["page"] - 1
        self.current_page_index = page_idx

        words = match["text"].split()
        snippet = " ".join(words[:5]) if len(words) >= 5 else match["text"]

        try:
            rects = self.current_doc.search_page(page_idx, snippet)
            self.search_result = (page_idx, rects)
        except Exception:
            self.search_result = None

        if page_idx in self.rendered_pages:
            self.rendered_pages.remove(page_idx)

        self.rebuild_layout()
        self.update_view()
        self.navigate_to(page_idx)

        score_pct = int(match["score"] * 100)
        self.show_toast(
            f"AI Match {self.ai_result_idx + 1} of {len(self.ai_results)} (Confidence: {score_pct}%)"
        )

    def _kill_ai_engine(self) -> None:
        """
        Cancels this tab's AI requests when the tab goes away. The engine
        itself is shared by all tabs and stops when the app exits. An OCR run
        still in progress is kept alive until it ends, since destroying a
        running QThread aborts the app.
        """
        ocr = self._ocr_thread
        self._ocr_thread = None
        if ocr is not None and ocr.isRunning():
            ocr.requestInterruption()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                for name in ("finished_ocr", "error_occurred", "progress"):
                    try:
                        getattr(ocr, name).disconnect()
                    except (AttributeError, RuntimeError, TypeError):
                        pass
            _ORPHANED_THREADS.add(ocr)
            ocr.finished.connect(lambda t=ocr: _ORPHANED_THREADS.discard(t))
            if isinstance(ocr, OcrThread):
                QApplication.restoreOverrideCursor()
        text = self._ai_text_thread
        self._ai_text_thread = None
        if text is not None and text.isRunning():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                for signal in (text.finished_text, text.error_occurred):
                    try:
                        signal.disconnect()
                    except (RuntimeError, TypeError):
                        pass
            _ORPHANED_THREADS.add(text)
            text.finished.connect(lambda t=text: _ORPHANED_THREADS.discard(t))
        progress = self._ocr_progress
        self._ocr_progress = None
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
        self._ai_queue = []
        reply = self._ai_reply
        self._ai_reply = None
        if reply is not None:
            try:
                reply.abort()
                reply.deleteLater()
            except RuntimeError:
                pass

    def toggle_ai_search_bar(self) -> None:
        """
        Toggles the operational visibility of the dedicated AI semantic search bar.
        Overrides the standard search bar if active.
        """
        is_visible = not self.ai_search_bar.isVisible()
        self.ai_search_bar.setVisible(is_visible)
        self.btn_ai_search.setChecked(is_visible)

        if is_visible:
            if self.search_bar.isVisible():
                self.toggle_search_bar()

            self.txt_ai_search.setFocus()
            self.txt_ai_search.selectAll()
            if self._ai_indexed_path != self.current_path:
                self.index_pdf_for_ai()
