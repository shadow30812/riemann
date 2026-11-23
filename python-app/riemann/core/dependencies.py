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

Packaged builds: sys.executable is Riemann itself, which cannot run pip or
"-m venv", and the packages must suit the bundled interpreter's version, not
whichever Python the computer has. So a packaged build installs into a plain
folder (packages_dir, one per bundled Python version) with any installed
Python 3 that has pip: pip install --target with --python-version, which picks
the wheels built for the bundled version. pip evaluates the locks' environment
markers (python_full_version < '3.12') against the Python running it, not
against --python-version, so target_lock first writes a copy of the lock that
keeps only the lines meant for the bundled version, judged by pip's own
packaging module in that Python (LOCK_FILTER). pip cannot uninstall from a
--target folder; _remove_from_folder deletes the files the package's RECORD
lists. A venv from an earlier version, made with a matching Python, is still
mounted.

setup_and_get_venv: From source, the venv's Python major/minor must match the
current interpreter. It may look for an installed Python and create the venv,
which takes seconds, so only the installers call it, on their worker threads.
Startup calls mount_existing_venv, which only adds the site-packages of a venv
or packages folder that is already there (from a cached list) and never starts
a process for a missing one.
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

from ..ui.palette import app_is_dark, dialog_icon, tokens
from .assets import asset_path, is_frozen
from .log import log
from .processes import NO_WINDOW

_NO_WINDOW = NO_WINDOW
PYTHON_PROBE_TIMEOUT = 5


def _is_frozen() -> bool:
    """True in a Nuitka or PyInstaller build, where sys.executable is Riemann itself."""
    return is_frozen()


def _find_base_python() -> str:
    """
    Returns a Python interpreter that can run pip for the installers.

    From source that is the running interpreter. In a packaged build
    sys.executable is the Riemann binary, which Nuitka refuses to run with
    "-m" (it exits with "the program tried to call itself with '-m'"), so look
    for any installed Python 3 that has pip; pip_install_command makes it
    install wheels for the bundled version.
    """
    if not _is_frozen():
        return sys.executable
    candidates = [["py", "-3"], ["python"], ["python3"]] if os.name == "nt" else [["python3"], ["python"]]
    for cmd in candidates:
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            out = subprocess.check_output(
                [exe, *cmd[1:], "-c", "import sys, pip; print(sys.version_info[0]); print(sys.executable)"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=PYTHON_PROBE_TIMEOUT,
                **_NO_WINDOW,
            ).splitlines()
        except Exception:
            continue
        if len(out) >= 2 and out[0].strip() == "3":
            return out[1].strip()
    raise RuntimeError(
        "Installing optional packages needs Python 3 with pip on this computer; none was found. "
        "Install Python 3 (any version) and try again."
    )


def _bundled_version(full: bool = False) -> str:
    v = sys.version_info
    return f"{v[0]}.{v[1]}.{v[2]}" if full else f"{v[0]}.{v[1]}"


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
            "paths = list(dict.fromkeys(site.getsitepackages() + [sysconfig.get_path('purelib'), "
            "sysconfig.get_path('platlib')])); "
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


def packages_dir() -> str:
    """The folder a packaged build installs optional packages into, one per bundled Python version."""
    data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return os.path.join(data_dir, "packages", "py" + _bundled_version().replace(".", ""))


def _mount_folder(folder: str) -> None:
    if folder not in sys.path:
        site.addsitedir(folder)
    importlib.invalidate_caches()


def _mount(venv_dir: str, python_exe: str) -> None:
    for sp in _venv_site_paths(venv_dir, python_exe):
        if os.path.exists(sp) and sp not in sys.path:
            site.addsitedir(sp)
    importlib.invalidate_caches()


def venv_python() -> str:
    """The optional-packages venv's Python, or "" when the venv has not been created."""
    python_exe = _venv_paths()[2]
    return python_exe if os.path.exists(python_exe) else ""


def script_dirs() -> list:
    """Folders that hold the programs optional packages install (Deno), venv first."""
    dirs = []
    python = venv_python()
    if python:
        dirs.append(os.path.dirname(python))
    if _is_frozen():
        dirs.append(os.path.join(packages_dir(), "Scripts" if os.name == "nt" else "bin"))
    return dirs


def mount_existing_venv() -> bool:
    """Adds an existing venv's or packages folder's packages to sys.path; True when there was one to mount."""
    mounted = False
    if _is_frozen() and os.path.isdir(packages_dir()):
        _mount_folder(packages_dir())
        mounted = True
    _, venv_dir, python_exe = _venv_paths()
    if not os.path.exists(python_exe):
        return mounted
    try:
        if _venv_version(venv_dir, python_exe) != _bundled_version():
            return mounted
        _mount(venv_dir, python_exe)
    except Exception as e:
        print(f"[Riemann Warning] Failed to mount local virtual environment: {e}")
        return mounted
    return True


def installed_names() -> set:
    """Normalised names of the optional packages installed in the venv or packages folder."""
    from importlib import metadata

    paths = []
    if _is_frozen() and os.path.isdir(packages_dir()):
        paths.append(packages_dir())
    _, venv_dir, python_exe = _venv_paths()
    if os.path.exists(python_exe):
        try:
            paths += [p for p in _venv_site_paths(venv_dir, python_exe) if os.path.isdir(p)]
        except Exception:
            log.debug("installed_names failed", exc_info=True)
    if not paths:
        return set()
    return {_normalise(d.metadata["Name"] or "") for d in metadata.distributions(path=paths)}


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", re.split(r"[\[<>=! ]", name, maxsplit=1)[0]).lower()


def _remove_from_folder(folder: str, pip_name: str) -> bool:
    """Deletes an installed package's files (its RECORD) from a --target folder; False when it is not there."""
    from importlib import metadata

    want = _normalise(pip_name)
    for dist in metadata.distributions(path=[folder]):
        if _normalise(dist.metadata["Name"] or "") != want:
            continue
        root = os.path.realpath(folder)
        touched = set()
        for f in dist.files or []:
            path = os.path.realpath(os.path.join(folder, str(f)))
            if os.path.commonpath([root, path]) != root:
                continue
            try:
                os.remove(path)
            except OSError:
                continue
            touched.add(os.path.dirname(path))
        for d in sorted(touched, key=len, reverse=True):
            while d != root and os.path.commonpath([root, d]) == root:
                try:
                    os.rmdir(d)
                except OSError:
                    break
                d = os.path.dirname(d)
        importlib.invalidate_caches()
        return True
    return False


def setup_and_get_venv() -> str:
    """
    Creates (or, after a Python upgrade, recreates) the optional-packages venv in the
    app data folder, adds its site-packages to sys.path, and returns its Python.
    A packaged build makes packages_dir instead and returns the Python that runs pip.
    """
    if _is_frozen():
        sys_py = _find_base_python()
        os.makedirs(packages_dir(), exist_ok=True)
        _mount_folder(packages_dir())
        return sys_py

    data_dir, venv_dir, python_exe = _venv_paths()

    if os.path.exists(python_exe):
        try:
            ver = _venv_version(venv_dir, python_exe)
            curr_ver = _bundled_version()
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
    name = re.split(r"[\[<>=! ]", pip_name, maxsplit=1)[0].lower().replace("_", "-")
    return asset_path("requirements", f"{name}.txt")


LOCK_FILTER = r"""
import sys
from pip._vendor.packaging.markers import Marker
src, out, full = sys.argv[1:4]
env = {"python_full_version": full, "python_version": ".".join(full.split(".")[:2])}
lines, entry = [], ""
for raw in open(src, encoding="utf-8").read().splitlines():
    line = raw.strip()
    if line.endswith("\\"):
        entry += line[:-1].strip() + " "
        continue
    entry += line
    req, sep, rest = entry.partition(" ; ")
    marker = rest.split(" --hash", 1)[0].strip()
    if not sep or not marker or Marker(marker).evaluate(env):
        lines.append(entry)
    entry = ""
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
"""


def target_lock(python: str, pip_name: str) -> str:
    """A copy of pip_name's lock with only the lines whose markers hold for the bundled Python."""
    folder = os.path.join(os.path.dirname(packages_dir()), "locks")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, os.path.basename(lock_file(pip_name)))
    subprocess.check_call(
        [python, "-c", LOCK_FILTER, lock_file(pip_name), out, _bundled_version(full=True)], **_NO_WINDOW
    )
    return out


def pip_install_command(python: str, pip_name: str) -> list:
    """
    Installs exactly what the lock lists, refusing any file whose hash differs.
    A packaged build installs into packages_dir, wheels for the bundled Python.
    """
    if not _is_frozen():
        return [python, "-m", "pip", "install", "--require-hashes", "--no-deps", "-r", lock_file(pip_name)]
    return [
        python, "-m", "pip", "install", "--require-hashes", "--no-deps",
        "--prefer-binary", "--upgrade", "--no-warn-script-location",
        "--target", packages_dir(), "--python-version", _bundled_version(),
        "-r", target_lock(python, pip_name),
    ]


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
            if self.action != "install" and _is_frozen():
                if _remove_from_folder(packages_dir(), self.pip_name):
                    self.finished_task.emit(self.pip_name, self.action, True)
                else:
                    self.error_occurred.emit(self.pip_name, self.action, "It is not installed.")
                return
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
        installed = installed_names()
        for display_name, pip_name in self.DEPENDENCIES.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(display_name))

            is_installed = _normalise(pip_name) in installed
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
