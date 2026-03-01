"""
AI Mixin.

Handles OCR (Tesseract) and Math Snipping (Pix2Tex) logic.
"""

import atexit
import io
import os
import subprocess
import sys
import threading
import time
from typing import Any

import requests
from PIL import Image
from PySide6.QtCore import QBuffer, QRect, Qt
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QProgressDialog

from ..widgets import PageWidget
from ..workers import InferenceThread, InstallerThread, LoaderThread, ModelDownloader


class AiMixin:
    """Methods for AI features."""

    def toggle_snip_mode(self, checked: bool) -> None:
        """Toggles rubber-band selection mode for Math OCR."""
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
        """Captures image for LaTeX OCR."""
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
        """Runs Tesseract on current page."""
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
        """Runs OCR model."""
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
        """Callback for model load success."""
        self.progress.close()
        self.latex_model = model
        if self._pending_snip_image:
            self._execute_inference(self._pending_snip_image)
            self._pending_snip_image = None

    def _on_model_error(self, msg: str) -> None:
        """Callback for model load failure."""
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
        """Starts dependency installer."""
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
        """Initiates background download of model pack."""
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
        """Callback for download completion."""
        if success:
            QMessageBox.information(self, "Success", "Models installed successfully.")
            self._setup_external_env()
            if self._pending_snip_image:
                self.run_latex_inference(self._pending_snip_image)
        else:
            QMessageBox.critical(self, "Error", "Download failed.")
            self._pending_snip_image = None

    def _execute_inference(self, img: Image.Image) -> None:
        """Runs inference in a background thread to prevent UI freezing."""
        self.progress = QProgressDialog("Processing...", "Cancel", 0, 0, self)
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.show()

        self.inference_thread = InferenceThread(self.latex_model, img)
        self.inference_thread.finished_inference.connect(self._on_inference_finished)
        self.inference_thread.error_occurred.connect(self._on_inference_error)
        self.inference_thread.start()

    def _on_inference_finished(self, code: str) -> None:
        """Callback when inference completes successfully."""
        self.progress.close()
        self.toggle_snip_mode(False)
        QInputDialog.getMultiLineText(self, "LaTeX Result", "Code:", code)

    def _on_inference_error(self, error_msg: str) -> None:
        """Callback when inference fails."""
        self.progress.close()
        self.toggle_snip_mode(False)
        QMessageBox.critical(self, "Inference Error", error_msg)

    def _get_external_module_dir(self) -> str:
        """Returns the path for external models."""
        base = (
            os.getenv("APPDATA")
            if os.name == "nt"
            else os.path.expanduser("~/.local/share")
        )
        return os.path.join(base, "Riemann", "latex_modules")

    def _setup_external_env(self) -> None:
        """Adds external module path to sys.path if frozen."""
        if getattr(sys, "frozen", False):
            d = self._get_external_module_dir()
            if os.path.exists(d) and d not in sys.path:
                sys.path.append(d)

    def _start_ai_engine(self) -> None:
        """Locates and launches the AI sidecar executable lazily."""
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
            exe_name = "main.exe" if sys.platform == "win32" else "main.bin"
            ai_exe = os.path.join(ai_dir, exe_name)

            if not os.path.exists(ai_exe):
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
                    print(f"AI Engine not found at {ai_exe}")
                    return

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            log_path = os.path.join(base_path, "assets", "ai_engine_debug.log")
            self.ai_log_file = open(log_path, "w")
            print(f"AI Engine startup initiated. Logging output to: {log_path}")

            self.ai_process = subprocess.Popen(
                [ai_exe],
                cwd=ai_dir,
                startupinfo=startupinfo,
                stdout=self.ai_log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            atexit.register(self._kill_ai_engine)
            print("AI Engine startup initiated. Check terminal for Uvicorn logs.")

        except Exception as e:
            print(f"Failed to start AI Engine: {e}")

    def index_pdf_for_ai(self) -> None:
        """Sends the current PDF to the AI sidecar in a background thread."""
        if not hasattr(self, "current_path") or not self.current_path:
            return

        pdf_path = self.current_path

        def _indexing_task():
            self._start_ai_engine()

            # Allow the FastAPI server 2 seconds to boot up before sending requests
            time.sleep(2)

            try:
                res = requests.post(
                    f"{self.ai_base_url}/index",
                    json={"pdf_path": pdf_path},
                    timeout=30,  # Indexing large PDFs can take a moment
                )
                if res.status_code == 200:
                    print("PDF successfully indexed for semantic search.")
                else:
                    print(f"AI Engine indexing failed with status: {res.status_code}")
            except requests.exceptions.RequestException:
                print("AI Engine is not responding. Is it running?")

        # Run completely off the UI thread so the app stays buttery smooth
        threading.Thread(target=_indexing_task, daemon=True).start()

    def ai_search(self, query: str) -> None:
        """Queries the AI engine and returns matches (runs in background)."""
        self._start_ai_engine()

        def _search_task():
            try:
                res = requests.post(
                    f"{self.ai_base_url}/search",
                    json={"query": query, "top_k": 5},
                    timeout=10,
                )
                if res.status_code == 200:
                    results = res.json()
                    # THREAD SAFE UI UPDATE: We must use QTimer to push UI updates back to the main thread!
                    QTimer.singleShot(
                        0, lambda: self._handle_ai_search_results(results)
                    )
                else:
                    print("Failed to process search query.")
            except requests.exceptions.RequestException:
                print("AI Engine is unreachable.")

        threading.Thread(target=_search_task, daemon=True).start()

    def _handle_ai_search_results(self, results: list) -> None:
        """Processes the JSON results from the AI sidecar (Runs on Main Thread)."""
        if not results:
            self.show_toast("No semantic matches found.")
            return

        best_match = results[0]
        page_idx = best_match["page"]

        if hasattr(self, "current_page_index"):
            self.current_page_index = page_idx
            self.rebuild_layout()
            self.ensure_visible(page_idx)
            self.show_toast(f"AI Match found on page {page_idx + 1}")

    def _kill_ai_engine(self) -> None:
        """Cleans up the subprocess when the app closes."""
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
        """Toggles the visibility of the AI search bar."""
        is_visible = not self.ai_search_bar.isVisible()
        self.ai_search_bar.setVisible(is_visible)
        self.btn_ai_search.setChecked(is_visible)

        if is_visible:
            if hasattr(self, "search_bar") and self.search_bar.isVisible():
                self.toggle_search_bar()

            self.txt_ai_search.setFocus()
            self.txt_ai_search.selectAll()
