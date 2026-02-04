"""
AI Mixin.

Handles OCR (Tesseract) and Math Snipping (Pix2Tex) logic.
"""

import io
import os
import sys
from typing import Any

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
