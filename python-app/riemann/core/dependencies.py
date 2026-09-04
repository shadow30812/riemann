"""Dependency hell"""

import importlib
import json
import os
import shutil
import site
import subprocess
import sys

from PySide6.QtCore import QStandardPaths, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def setup_and_get_venv() -> str:
    """
    Ensures a local virtual environment exists, dynamically interrogates it
    for its exact path structure, and mounts them cleanly.
    """
    data_dir = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    venv_dir = os.path.join(data_dir, "venv")

    win = os.name == "nt"
    python_exe = (
        os.path.join(venv_dir, "Scripts", "python.exe")
        if win
        else os.path.join(venv_dir, "bin", "python")
    )

    # If the venv exists, ensure its Python major/minor matches the current interpreter
    if os.path.exists(python_exe):
        try:
            ver = subprocess.check_output(
                [python_exe, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                text=True,
            ).strip()
            curr_ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
            if ver != curr_ver:
                print(f"[Riemann] Python version mismatch in venv ({ver} vs current {curr_ver}). Recreating...")
                shutil.rmtree(venv_dir, ignore_errors=True)
        except Exception:
            shutil.rmtree(venv_dir, ignore_errors=True)

    if not os.path.exists(python_exe):
        os.makedirs(data_dir, exist_ok=True)
        sys_py = sys.executable or ("python" if win else "python3")
        print("[Riemann] Initializing local virtual environment...")
        subprocess.check_call(
            [sys_py, "-m", "venv", "--system-site-packages", venv_dir]
        )

    try:
        # Interrogate site-packages only; NEVER inject standard library directories into sys.path
        out = subprocess.check_output(
            [
                python_exe,
                "-c",
                "import site, sysconfig, json; "
                "paths = list(dict.fromkeys(site.getsitepackages() + [sysconfig.get_path('purelib'), sysconfig.get_path('platlib')])); "
                "print(json.dumps(paths))",
            ],
            text=True,
        )
        site_paths = json.loads(out.strip())

        for sp in site_paths:
            if os.path.exists(sp) and sp not in sys.path:
                site.addsitedir(sp)

        importlib.invalidate_caches()

    except Exception as e:
        print(f"[Riemann Warning] Failed to mount local virtual environment: {e}")

    return python_exe


class DependencyWorker(QThread):
    """Asynchronous worker to install or uninstall pip packages without freezing the UI."""

    finished_task = Signal(str, str, bool)
    error_occurred = Signal(str, str, str)
    progress_update = Signal(str)

    def __init__(self, action: str, pip_name: str, parent=None):
        super().__init__(parent)
        self.action = action
        self.pip_name = pip_name

    def run(self):
        try:
            cmd = (
                [
                    setup_and_get_venv(),
                    "-m",
                    "pip",
                    "install",
                    self.pip_name,
                ]
                if self.action == "install"
                else [
                    setup_and_get_venv(),
                    "-m",
                    "pip",
                    "uninstall",
                    "-y",
                    self.pip_name,
                ]
            )

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in iter(process.stdout.readline, ""):
                text = line.strip()
                if text:
                    clean_text = text.split("\r")[-1].strip()
                    self.progress_update.emit(clean_text)

            process.stdout.close()
            return_code = process.wait()

            if return_code == 0:
                self.finished_task.emit(self.pip_name, self.action, True)
            else:
                self.error_occurred.emit(
                    self.pip_name, self.action, f"Exited with code {return_code}"
                )

        except Exception as e:
            self.error_occurred.emit(self.pip_name, self.action, str(e))


class DependenciesDialog(QDialog):
    """A modal dialog to manage excluded/heavy external dependencies."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("External Dependency Manager")
        self.resize(550, 400)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Module (Feature)", "Status", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table)

        self.DEPENDENCIES = {
            "PyMuPDF (Advanced PDF Tools)": "PyMuPDF",
            "PyTorch (AI Inference Engine)": "torch",
            "TorchVision (Image Tensors)": "torchvision",
            "OpenCV (Image Processing)": "opencv-python",
            "Pix2Tex (Snip-to-LaTeX OCR)": "pix2tex[gui]",
            "Transformers (HuggingFace)": "transformers",
            "SciPy (Math/DSP Engine)": "scipy",
            "Pandas (Data Parsing)": "pandas",
            "YT-DLP (Video Streaming Engine)": "yt-dlp",
            "Pygments (Markdown Syntax)": "Pygments",
            "Faster-Whisper (Live Captions)": "faster-whisper",
        }

        self.populate_table()

    def populate_table(self):
        self.table.setRowCount(0)
        for display_name, pip_name in self.DEPENDENCIES.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(display_name))

            is_installed = self._check_pip_installed(pip_name)
            status_item = QTableWidgetItem("Installed" if is_installed else "Missing")
            status_item.setForeground(
                Qt.GlobalColor.green if is_installed else Qt.GlobalColor.red
            )
            self.table.setItem(row, 1, status_item)

            btn = QPushButton("Uninstall" if is_installed else "Install")
            btn.setStyleSheet(
                "background-color: #d32f2f; color: white;"
                if is_installed
                else "background-color: #2e7d32; color: white;"
            )
            btn.clicked.connect(
                lambda checked,
                p=pip_name,
                r=row,
                inst=is_installed: self.handle_action(p, r, not inst)
            )
            self.table.setCellWidget(row, 2, btn)

    def _check_pip_installed(self, pip_name: str) -> bool:
        """Safely checks if a pip package is installed via subprocess."""
        base_pkg = pip_name.split("[")[0]
        try:
            subprocess.check_output(
                [setup_and_get_venv(), "-m", "pip", "show", base_pkg],
                stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def handle_action(self, pip_name: str, row: int, install: bool):
        action = "install" if install else "uninstall"

        for r in range(self.table.rowCount()):
            self.table.cellWidget(r, 2).setEnabled(False)

        verb = "Installing" if install else "Uninstalling"
        self.progress_dialog = QProgressDialog(
            f"Preparing to {verb.lower()} {pip_name}...", None, 0, 0, self
        )
        self.progress_dialog.setWindowTitle("Dependency Manager")
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.resize(400, 150)
        self.progress_dialog.show()

        self.worker = DependencyWorker(action, pip_name)
        self.worker.progress_update.connect(self.on_progress_update)
        self.worker.finished_task.connect(self.on_task_finished)
        self.worker.error_occurred.connect(self.on_task_error)
        self.worker.start()

    def on_progress_update(self, msg: str):
        """Receives live terminal text from the pip worker and updates the dialog."""
        display_msg = msg if len(msg) < 70 else msg[:67] + "..."
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.setLabelText(f"Working...\n{display_msg}")

    def on_task_finished(self, pip_name: str, action: str, success: bool):
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.close()

        try:
            import importlib

            importlib.invalidate_caches()
        except Exception:
            pass

        self.populate_table()
        action_verb = "installed" if action == "install" else "uninstalled"
        QMessageBox.information(
            self, "Success", f"Successfully {action_verb} {pip_name}."
        )

    def on_task_error(self, pip_name: str, action: str, error_msg: str):
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.close()

        self.populate_table()
        verb = "install" if action == "install" else "uninstall"
        QMessageBox.critical(
            self, "Error", f"Failed to {verb} {pip_name}:\n{error_msg}"
        )
