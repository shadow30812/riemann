"""
Optional Python packages and the private virtual environment they live in.

Heavy features (snip-to-LaTeX, live captions, video streaming) need packages
that are not bundled with Riemann. They are installed on request into a venv in
the app's local data folder. Installs use the exact versions in PINNED, the
ones Riemann was tested with, and are made from the hashed locks in
assets/requirements/ (lock_file): pip runs with --require-hashes and
--no-deps, so every package, dependencies included, must match the version and
SHA-256 in the lock, and a substituted file on PyPI is refused.
scripts/lock_optional.py writes the locks from pyproject.toml; run it after
changing a version. yt-dlp needs updating more often than the rest, since
sites change; it comes with yt-dlp-ejs, the pinned JavaScript that solves
YouTube's challenges locally, and with Deno from PyPI, the JavaScript runtime
the solver runs in (ui/browser.py js_runtimes points yt-dlp at it; a Deno on
PATH works too), so yt-dlp never downloads solver code at run time.

Notes:

_venv_site_paths: Interrogate site-packages only; NEVER inject standard library
directories into sys.path.

setup_and_get_venv: If the venv exists, ensure its Python major/minor matches
the current interpreter. It may look for an installed Python and create the
venv, which takes seconds, so only the installers call it, on their worker
threads. Startup calls mount_existing_venv, which only adds the site-packages
of a venv that is already there (from a cached list) and never starts a
process for a missing one.
"""

import importlib
import json
import os
import re
import shutil
import site
import subprocess
import sys

from PySide6.QtCore import QStandardPaths, Qt, QThread, Signal
from PySide6.QtGui import QColor
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
from .log import log
from .assets import asset_path, is_frozen
from .processes import NO_WINDOW
from ..ui.palette import app_is_dark, dialog_icon, tokens


_NO_WINDOW = NO_WINDOW
PYTHON_PROBE_TIMEOUT = 5


def _is_frozen() -> bool:
    """True in a Nuitka or PyInstaller build, where sys.executable is Riemann itself."""
    return is_frozen()


def _find_base_python() -> str:
    """
    Returns a Python interpreter that can create the venv.

    From source that is the running interpreter. In a packaged build
    sys.executable is the Riemann binary, which Nuitka refuses to run with
    "-m venv" (it exits with "the program tried to call itself with '-m'"), so
    the venv was never created there. So look for an installed Python with the
    same major.minor version, since packages installed into the venv must be
    importable by the bundled interpreter.
    """
    if not _is_frozen():
        return sys.executable
    want = f"{sys.version_info[0]}.{sys.version_info[1]}"
    candidates = []
    if os.name == "nt":
        candidates += [["py", f"-{want}"], ["python"], ["python3"]]
    else:
        candidates += [[f"python{want}"], ["python3"], ["python"]]
    for cmd in candidates:
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            out = subprocess.check_output(
                [exe, *cmd[1:], "-c", "import sys; print(sys.executable); print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                text=True,
                timeout=PYTHON_PROBE_TIMEOUT,
                **_NO_WINDOW,
            ).split()
        except Exception:
            continue
        if len(out) >= 2 and out[-1] == want:
            return out[0]
    raise RuntimeError(
        f"Optional dependencies need Python {want} installed on this computer; none was found."
    )


def _venv_version(venv_dir: str, python_exe: str) -> str:
    """
    The venv's Python major.minor, read from pyvenv.cfg. Starting the venv's
    interpreter to ask took tens of milliseconds on every launch.
    """
    try:
        with open(os.path.join(venv_dir, "pyvenv.cfg"), encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip() in ("version", "version_info"):
                    return ".".join(value.strip().split(".")[:2])
    except OSError:
        pass
    return subprocess.check_output(
        [python_exe, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
        text=True,
        **_NO_WINDOW,
    ).strip()


def _venv_site_paths(venv_dir: str, python_exe: str) -> list:
    """
    The venv's site-packages folders. Asking its interpreter costs a process
    start, so the answer is cached in the venv and reused until pyvenv.cfg
    changes (the venv was recreated).
    """
    cache_path = os.path.join(venv_dir, "riemann_site_paths.json")
    try:
        st = os.stat(os.path.join(venv_dir, "pyvenv.cfg"))
        key = [st.st_mtime_ns, st.st_size]
    except OSError:
        key = None

    if key is not None:
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("key") == key and isinstance(cached.get("paths"), list):
                return cached["paths"]
        except (OSError, ValueError):
            pass

    out = subprocess.check_output(
        [
            python_exe,
            "-c",
            "import site, sysconfig, json; "
            "paths = list(dict.fromkeys(site.getsitepackages() + [sysconfig.get_path('purelib'), sysconfig.get_path('platlib')])); "
            "print(json.dumps(paths))",
        ],
        text=True,
        **_NO_WINDOW,
    )
    paths = json.loads(out.strip())

    if key is not None:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"key": key, "paths": paths}, f)
        except OSError:
            pass
    return paths


def _venv_paths() -> tuple:
    data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    venv_dir = os.path.join(data_dir, "venv")
    if os.name == "nt":
        return data_dir, venv_dir, os.path.join(venv_dir, "Scripts", "python.exe")
    return data_dir, venv_dir, os.path.join(venv_dir, "bin", "python")


def _mount(venv_dir: str, python_exe: str) -> None:
    for sp in _venv_site_paths(venv_dir, python_exe):
        if os.path.exists(sp) and sp not in sys.path:
            site.addsitedir(sp)
    importlib.invalidate_caches()


def venv_python() -> str:
    """The optional-packages venv's Python, or "" when the venv has not been created."""
    python_exe = _venv_paths()[2]
    return python_exe if os.path.exists(python_exe) else ""


def mount_existing_venv() -> bool:
    """Adds an existing venv's packages to sys.path; True when there was one for this Python to mount."""
    _, venv_dir, python_exe = _venv_paths()
    if not os.path.exists(python_exe):
        return False
    try:
        if _venv_version(venv_dir, python_exe) != f"{sys.version_info[0]}.{sys.version_info[1]}":
            return False
        _mount(venv_dir, python_exe)
    except Exception as e:
        print(f"[Riemann Warning] Failed to mount local virtual environment: {e}")
        return False
    return True


def setup_and_get_venv() -> str:
    """
    Creates (or, after a Python upgrade, recreates) the optional-packages venv in the
    app data folder, adds its site-packages to sys.path, and returns its Python.
    """
    data_dir, venv_dir, python_exe = _venv_paths()

    if os.path.exists(python_exe):
        try:
            ver = _venv_version(venv_dir, python_exe)
            curr_ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
            if ver != curr_ver:
                print(f"[Riemann] Python version mismatch in venv ({ver} vs current {curr_ver}). Recreating...")
                shutil.rmtree(venv_dir, ignore_errors=True)
        except Exception:
            shutil.rmtree(venv_dir, ignore_errors=True)

    if not os.path.exists(python_exe):
        os.makedirs(data_dir, exist_ok=True)
        sys_py = _find_base_python()
        print("[Riemann] Initializing local virtual environment...")
        subprocess.check_call(
            [sys_py, "-m", "venv", "--system-site-packages", venv_dir], **_NO_WINDOW
        )

    try:
        _mount(venv_dir, python_exe)
    except Exception as e:
        print(f"[Riemann Warning] Failed to mount local virtual environment: {e}")

    return python_exe


PINNED = {
    "torch": "torch==2.14.0",
    "torchvision": "torchvision==0.29.0",
    "opencv-python-headless": "opencv-python-headless==5.0.0.93",
    "pix2tex[gui]": "pix2tex[gui]==0.1.4",
    "transformers": "transformers==5.16.1",
    "scipy": "scipy==1.17.1",
    "pandas": "pandas==3.0.5",
    "yt-dlp": "yt-dlp==2026.10.26",
    "yt-dlp-ejs": "yt-dlp-ejs==0.8.0",
    "deno": "deno==2.9.8",
    "Pygments": "Pygments==2.21.0",
    "faster-whisper": "faster-whisper==1.2.1",
    "mammoth": "mammoth==1.13.0",
    "python-pptx": "python-pptx==1.0.2",
    "openpyxl": "openpyxl==3.1.5",
    "xlrd": "xlrd==2.0.2",
    "striprtf": "striprtf==0.0.35",
}

LATEX_OCR_REQUIREMENTS = ["pix2tex[gui]", "torch", "torchvision"]


LATEX_OCR_LOCK = "latex-ocr"

OFFICE_VIEWERS_REQUIREMENTS = ["mammoth", "python-pptx", "openpyxl", "xlrd", "striprtf"]
OFFICE_VIEWERS_LOCK = "office-viewers"


def lock_file(pip_name: str) -> str:
    """The hashed lock a catalogue package (or LATEX_OCR_LOCK) is installed from."""
    name = re.split(r"[\[<>=! ]", pip_name, 1)[0].lower().replace("_", "-")
    return asset_path("requirements", f"{name}.txt")


def pip_install_command(python: str, pip_name: str) -> list:
    """Installs exactly what the lock lists, refusing any file whose hash differs."""
    return [python, "-m", "pip", "install", "--require-hashes", "--no-deps", "-r", lock_file(pip_name)]


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
                pip_install_command(setup_and_get_venv(), self.pip_name)
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
                **_NO_WINDOW,
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
        dialog_icon(self, "package")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Module (Feature)", "Status", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table)

        self.DEPENDENCIES = {
            "PyTorch (AI Inference Engine)": "torch",
            "TorchVision (Image Tensors)": "torchvision",
            "OpenCV (Image Processing)": "opencv-python-headless",
            "Pix2Tex (Snip-to-LaTeX OCR)": "pix2tex[gui]",
            "Transformers (HuggingFace)": "transformers",
            "SciPy (Math/DSP Engine)": "scipy",
            "Pandas (Data Parsing)": "pandas",
            "YT-DLP (Video Streaming Engine)": "yt-dlp",
            "Deno (runs YouTube's challenge solver)": "deno",
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
            t = tokens(app_is_dark())
            status_item.setForeground(QColor(t["success"] if is_installed else t["danger"]))
            self.table.setItem(row, 1, status_item)

            btn = QPushButton("Uninstall" if is_installed else "Install")
            btn.setProperty("role", "danger" if is_installed else "success")
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
        python_exe = venv_python()
        if not python_exe:
            return False
        try:
            subprocess.check_output(
                [python_exe, "-m", "pip", "show", base_pkg],
                stderr=subprocess.DEVNULL,
                **_NO_WINDOW,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, RuntimeError):
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
            log.debug("on_task_finished failed", exc_info=True)

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
