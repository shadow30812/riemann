"""
Background Workers for the Reader Module.
"""

import os
import subprocess
import sys
import urllib.request
import zipfile

from PySide6.QtCore import QThread, Signal


class ModelDownloader(QThread):
    """Downloads and extracts the LaTeX OCR model pack."""

    progress = Signal(int)
    finished = Signal(bool)

    def __init__(self, url: str, dest_folder: str) -> None:
        super().__init__()
        self.url = url
        self.dest_folder = dest_folder

    def run(self) -> None:
        try:
            os.makedirs(self.dest_folder, exist_ok=True)
            zip_path = os.path.join(self.dest_folder, "latex_ocr.zip")

            def report(block_num: int, block_size: int, total_size: int) -> None:
                if total_size > 0:
                    percent = int((block_num * block_size * 100) / total_size)
                    self.progress.emit(percent)

            urllib.request.urlretrieve(self.url, zip_path, report)
            self.progress.emit(99)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(self.dest_folder)

            os.remove(zip_path)
            self.finished.emit(True)
        except Exception as e:
            print(f"Download/Extraction failed: {e}")
            self.finished.emit(False)


class InstallerThread(QThread):
    """Runs pip install in background."""

    finished_install = Signal()
    install_error = Signal(str)

    def run(self) -> None:
        try:
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "pix2tex[gui]",
                "torch",
                "torchvision",
            ]
            subprocess.check_call(cmd)
            self.finished_install.emit()
        except Exception as e:
            self.install_error.emit(f"Installer Error: {e}")


class LoaderThread(QThread):
    """Initializes the AI model."""

    finished_loading = Signal(object)
    error_occurred = Signal(str)

    def run(self) -> None:
        try:
            from pix2tex.cli import LatexOCR

            model = LatexOCR()
            self.finished_loading.emit(model)
        except ImportError:
            self.error_occurred.emit("Module 'pix2tex' not found.")
        except Exception as e:
            self.error_occurred.emit(f"AI Initialization Failed:\n{str(e)}")
