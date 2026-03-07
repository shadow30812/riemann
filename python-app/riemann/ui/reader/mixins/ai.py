"""
AI Mixin.

Handles OCR (Tesseract) and Math Snipping (Pix2Tex) logic.
"""

import atexit
import io
import json
import os
import subprocess
import sys
import threading
from typing import Any

from PIL import Image
from PySide6.QtCore import QBuffer, QObject, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
)

from ..widgets import PageWidget
from ..workers import InferenceThread, InstallerThread, LoaderThread, ModelDownloader


class AIEngineBridge(QObject):
    """
    Thread-safe bridge to pass data from background threads to the main UI thread.

    Attributes:
        toast_requested (Signal): Emits a string message for UI toast notifications.
        search_results_ready (Signal): Emits a list of search result dictionaries.
    """

    toast_requested = Signal(str)
    search_results_ready = Signal(list)


class AiMixin:
    """
    Provides methods for AI-assisted features including OCR and mathematical snipping.
    Intended to be mixed into the main reader application context.
    """

    def toggle_snip_mode(self, checked: bool) -> None:
        """
        Toggles the rubber-band selection mode for mathematical formula OCR.

        Args:
            checked (bool): True to enable snipping mode, False to revert to standard navigation.
        """
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
        """
        Captures an image segment from the page widget for LaTeX OCR processing.

        Args:
            label (PageWidget): The target page widget containing the rendered document.
            rect (QRect): The geometric selection rectangle defining the snip boundary.
        """
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
        Executes Tesseract OCR on the currently visible page and displays the extracted text.
        """
        if not self.current_doc:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            txt = self.current_doc.ocr_page(self.current_page_index, 2.0)
            QApplication.restoreOverrideCursor()
            QInputDialog.getMultiLineText(self, "OCR Result", "Text:", txt)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            print(f"OCR Error: {e}")

    def run_latex_inference(self, img: Image.Image) -> None:
        """
        Initiates the LaTeX OCR inference process on the provided image object.
        Loads the necessary AI model if it is not already initialized.

        Args:
            img (Image.Image): The cropped image object containing the mathematical formula.
        """
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
        """
        Callback handler invoked when the background AI model loader completes successfully.

        Args:
            model (Any): The instantiated OCR inference model.
        """
        self.progress.close()
        self.latex_model = model
        if self._pending_snip_image:
            self._execute_inference(self._pending_snip_image)
            self._pending_snip_image = None

    def _on_model_error(self, msg: str) -> None:
        """
        Callback handler invoked when the background AI model loader encounters an error.
        Triggers fallbacks such as downloading models or installing missing dependencies.

        Args:
            msg (str): The error message payload.
        """
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
        url = "https://github.com/shadow30812/riemann/releases/download/v1.0/latex_ocr_modules.zip"
        self.downloader = ModelDownloader(url, self._get_external_module_dir())

        self.dl_dialog = QProgressDialog(
            "Downloading AI Models...", "Cancel", 0, 100, self
        )
        self.dl_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dl_dialog.setAutoClose(True)

        self.downloader.progress.connect(self.dl_dialog.setValue)
        self.downloader.finished.connect(self.on_download_finished)
        self.dl_dialog.show()
        self.downloader.start()

    def on_download_finished(self, success: bool) -> None:
        """
        Callback handler invoked upon completion of the AI model pack download.

        Args:
            success (bool): True if the download and extraction succeeded, False otherwise.
        """
        if success:
            QMessageBox.information(self, "Success", "Models installed successfully.")
            self._setup_external_env()
            if self._pending_snip_image:
                self.run_latex_inference(self._pending_snip_image)
        else:
            QMessageBox.critical(self, "Error", "Download failed.")
            self._pending_snip_image = None

    def _execute_inference(self, img: Image.Image) -> None:
        """
        Dispatches the inference task to a background worker thread to prevent UI blocking.

        Args:
            img (Image.Image): The target image object for OCR inference.
        """
        self.progress = QProgressDialog("Processing...", "Cancel", 0, 0, self)
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.show()

        self.inference_thread = InferenceThread(self.latex_model, img)
        self.inference_thread.finished_inference.connect(self._on_inference_finished)
        self.inference_thread.error_occurred.connect(self._on_inference_error)
        self.inference_thread.start()

    def _on_inference_finished(self, code: str) -> None:
        """
        Callback handler invoked when the inference thread completes successfully.

        Args:
            code (str): The resulting LaTeX string extracted from the image.
        """
        self.progress.close()
        self.toggle_snip_mode(False)
        QInputDialog.getMultiLineText(self, "LaTeX Result", "Code:", code)

    def _on_inference_error(self, error_msg: str) -> None:
        """
        Callback handler invoked when the inference thread fails.

        Args:
            error_msg (str): The exception message describing the failure.
        """
        self.progress.close()
        self.toggle_snip_mode(False)
        QMessageBox.critical(self, "Inference Error", error_msg)

    def _get_external_module_dir(self) -> str:
        """
        Resolves the cross-platform absolute path for the external AI modules directory.

        Returns:
            str: The target path string based on the operating system context.
        """
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
        Locates and initializes the external AI sidecar executable process lazily.
        Ensures only a single instance of the AI engine process is spawned.
        """
        if not hasattr(self, "_ai_lock"):
            self._ai_lock = threading.Lock()

        if getattr(self, "ai_process", None) is not None:
            if self.ai_process.poll() is not None:
                print("AI Engine process died. Restarting...")
                self.ai_process = None
            else:
                return

        self.ai_port = 8080
        self.ai_base_url = f"http://localhost:{self.ai_port}"

        try:
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.dirname(os.path.abspath(__file__))
                base_path = os.path.abspath(os.path.join(base_path, "..", "..", ".."))

            ai_dir = os.path.join(base_path, "assets", "riemann_ai_engine")

            if sys.platform == "win32":
                python_exe = os.path.join(ai_dir, "env", "python.exe")
            else:
                python_exe = os.path.join(ai_dir, "env", "bin", "python")

            ai_script = os.path.join(ai_dir, "main.py")

            if not os.path.exists(python_exe) or not os.path.exists(ai_script):
                dev_script = os.path.join(
                    base_path, "..", "..", "riemann-ai", "main.py"
                )
                if os.path.exists(dev_script):
                    self.ai_process = subprocess.Popen(
                        [sys.executable, dev_script],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        close_fds=True,
                    )
                    atexit.register(self._kill_ai_engine)
                    return
                else:
                    print(f"AI Engine not found at {python_exe}")
                    return

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            log_path = os.path.join(base_path, "assets", "ai_engine_debug.log")
            self.ai_log_file = open(log_path, "w")

            self.ai_process = subprocess.Popen(
                [python_exe, ai_script],
                cwd=ai_dir,
                startupinfo=startupinfo,
                stdout=self.ai_log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            atexit.register(self._kill_ai_engine)

        except Exception as e:
            print(f"Failed to start AI Engine: {e}")

    def show_toast(self, msg: str) -> None:
        """
        Displays a temporary UI toast notification to the user.

        Args:
            msg (str): The notification text to display.
        """
        self.lbl_toast = QLabel(self)
        self.lbl_toast.setStyleSheet(
            "background: #333; color: white; padding: 10px; border-radius: 5px;"
        )
        self.lbl_toast.setText(msg)
        self.lbl_toast.adjustSize()
        self.lbl_toast.move(
            (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
        )
        self.lbl_toast.show()
        QTimer.singleShot(4000, self.lbl_toast.hide)

    def index_pdf_for_ai(self) -> None:
        """
        Signals the external AI engine over WebSocket to index the active PDF document.
        """
        if not hasattr(self, "current_path") or not self.current_path:
            return

        self._start_ai_engine()
        self._setup_websocket()

        QTimer.singleShot(
            2000,
            lambda: self._ws_client.sendTextMessage(
                json.dumps({"action": "index", "pdf_path": self.current_path})
            ),
        )

    def ai_search(self, query: str) -> None:
        """
        Sends a semantic search query to the external AI engine over WebSocket.

        Args:
            query (str): The user's search text payload.
        """
        self._start_ai_engine()
        self._setup_websocket()
        self._ws_client.sendTextMessage(
            json.dumps({"action": "search", "query": query, "top_k": 5})
        )

    def _setup_websocket(self) -> None:
        """
        Initializes the persistent WebSocket client connection to the external AI engine.
        """
        if not hasattr(self, "_ws_client"):
            self._ws_client = QWebSocket()
            self._ws_client.textMessageReceived.connect(self._on_ws_message)
            self._ws_client.open(QUrl("ws://localhost:8080/ws/ai"))

    def _on_ws_message(self, message: str) -> None:
        """
        Handles incoming messaging from the AI engine WebSocket connection.
        Updates internal search result state or dispatches toast notifications.

        Args:
            message (str): The JSON payload received over the socket.
        """
        try:
            res = json.loads(message)
            status = res.get("status")
            if status == "progress":
                self.show_toast(f"AI: {res.get('msg')}")
            elif status == "success":
                self.show_toast("AI Engine is ready! ✨")
            elif status == "error":
                self.show_toast(f"AI Error: {res.get('msg')}")
            elif status == "results":
                data = res.get("data", [])
                if not data:
                    self.show_toast("No semantic matches found.")
                    return
                self.ai_results = data
                self.ai_result_idx = 0
                self._render_ai_result()
        except json.JSONDecodeError:
            pass

    def ai_find_next(self) -> None:
        """
        Advances the focus cycle to the next sequential AI search result and triggers a re-render.
        """
        if hasattr(self, "ai_results") and self.ai_results:
            self.ai_result_idx = (self.ai_result_idx + 1) % len(self.ai_results)
            self._render_ai_result()

    def ai_find_prev(self) -> None:
        """
        Reverts the focus cycle to the previous sequential AI search result and triggers a re-render.
        """
        if hasattr(self, "ai_results") and self.ai_results:
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
        self.ensure_visible(page_idx)

        score_pct = int(match["score"] * 100)
        self.show_toast(
            f"AI Match {self.ai_result_idx + 1} of {len(self.ai_results)} (Confidence: {score_pct}%)"
        )

    def _kill_ai_engine(self) -> None:
        """
        Safely terminates the child AI engine subprocess during application teardown.
        """
        process = getattr(self, "ai_process", None)
        if process:
            process.terminate()
            self.ai_process = None

        if (
            hasattr(self, "ai_log_file")
            and self.ai_log_file
            and not self.ai_log_file.closed
        ):
            try:
                self.ai_log_file.close()
            except Exception:
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
            if hasattr(self, "search_bar") and self.search_bar.isVisible():
                self.toggle_search_bar()

            self.txt_ai_search.setFocus()
            self.txt_ai_search.selectAll()
