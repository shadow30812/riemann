"""
Asking to install LibreOffice, and installing it.

offer_office(parent, path, basic) is what the window calls when an Office
file is opened and LibreOffice isn't found. OfficePrompt explains what
LibreOffice is for and offers Install LibreOffice or Cancel, plus "Use the
basic view" when Riemann can read the file itself (basic is True), with
"Don't ask again" (OFFICE_ASK_KEY; Settings turns it back on). Install opens
InstallDialog. It returns "office" once LibreOffice is installed, "basic"
for the basic view, and "" otherwise.

offer_basic_packages asks to install the basic view's packages when they are
missing (a source install without them). They come from the hashed lock
OFFICE_VIEWERS_LOCK through the same DependencyWorker as the optional
packages in Settings, with a progress dialog, and the call waits for it.

InstallDialog shows the first plan from core/office_install.py: what it does
and the exact commands, with Copy for people who would rather use a
terminal. Nothing runs until Install is clicked. The commands run one after
another in a QProcess (asynchronous, so the window stays responsive), their
output shown as it comes. When they succeed, LibreOffice is looked for again
(core.office.refresh); if it is found the dialog accepts. When a plan fails,
the next one is offered (Flatpak after the distribution's packages), and the
download page is always one click away. Closing the dialog while a command
runs kills it.
"""

import importlib
import os
from typing import List, Optional

from PySide6.QtCore import QEventLoop, QProcess, QProcessEnvironment, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from ..core import office
from ..core.endpoints import LIBREOFFICE_DOWNLOAD
from ..core.office_install import InstallPlan, install_env, install_plans
from ..core.settings import OFFICE_ASK_KEY, OFFICE_PATH_KEY, app_settings
from .threads import track
from .palette import dialog_icon


class OfficePrompt(QDialog):
    def __init__(self, parent, name: str, basic: bool = False):
        super().__init__(parent)
        self.setWindowTitle("LibreOffice")
        self.choice = "cancel"
        layout = QVBoxLayout(self)
        dialog_icon(self, "file-cog")
        message = (
            f"Riemann shows {name} with LibreOffice, a free office suite, and it isn't installed.\n\n"
            "Once it is, Word, PowerPoint, Excel and OpenDocument files open here as pages, and "
            "\"Edit in LibreOffice\" lets you change them."
        )
        if basic:
            message += (
                "\n\nWithout it, Riemann can show a basic view: the text, tables and pictures, "
                "without the page layout."
            )
        text = QLabel(message)
        text.setWordWrap(True)
        text.setMinimumWidth(420)
        layout.addWidget(text)
        self.cb_dont_ask = QCheckBox("Don't ask again")
        layout.addWidget(self.cb_dont_ask)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.btn_basic = None
        if basic:
            self.btn_basic = QPushButton("Use the basic view")
            self.btn_basic.clicked.connect(self._basic)
            buttons.addWidget(self.btn_basic)
        install = QPushButton("Install LibreOffice…")
        install.setDefault(True)
        install.clicked.connect(self._install)
        buttons.addWidget(install)
        layout.addLayout(buttons)

    def _install(self) -> None:
        self.choice = "install"
        self.accept()

    def _basic(self) -> None:
        self.choice = "basic"
        self.accept()

    def done(self, result: int) -> None:
        if self.cb_dont_ask.isChecked():
            app_settings().setValue(OFFICE_ASK_KEY, False)
        super().done(result)


class InstallDialog(QDialog):
    def __init__(self, parent=None, plans: Optional[List[InstallPlan]] = None):
        super().__init__(parent)
        self.setWindowTitle("Install LibreOffice")
        self.resize(620, 440)
        self.plans = install_plans() if plans is None else plans
        self.plan_index = 0
        self.steps: List[List[str]] = []
        self.process: Optional[QProcess] = None
        self.found = False

        layout = QVBoxLayout(self)
        dialog_icon(self, "hard-drive-download")
        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("dialogTitle")
        self.lbl_note = QLabel()
        self.lbl_note.setWordWrap(True)
        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_note)
        self.txt_commands = QPlainTextEdit()
        self.txt_commands.setReadOnly(True)
        self.txt_commands.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.txt_commands.setMaximumHeight(70)
        layout.addWidget(self.txt_commands)
        self.txt_output = QPlainTextEdit()
        self.txt_output.setReadOnly(True)
        self.txt_output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.txt_output.setPlaceholderText("The installer's messages appear here.")
        layout.addWidget(self.txt_output, 1)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        buttons = QHBoxLayout()
        self.btn_download = QPushButton("Open the download page")
        self.btn_download.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(LIBREOFFICE_DOWNLOAD)))
        self.btn_copy = QPushButton("Copy commands")
        self.btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.txt_commands.toPlainText()))
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        self.btn_install = QPushButton("Install")
        self.btn_install.setDefault(True)
        self.btn_install.clicked.connect(self.start)
        buttons.addWidget(self.btn_download)
        buttons.addStretch()
        buttons.addWidget(self.btn_copy)
        buttons.addWidget(self.btn_close)
        buttons.addWidget(self.btn_install)
        layout.addLayout(buttons)
        self._show_plan()

    def plan(self) -> Optional[InstallPlan]:
        return self.plans[self.plan_index] if self.plan_index < len(self.plans) else None

    def _show_plan(self) -> None:
        plan = self.plan()
        if plan is None:
            self.lbl_title.setText("Install LibreOffice from its website")
            self.lbl_note.setText(
                "Riemann can't install LibreOffice on this computer by itself. Download it from "
                "libreoffice.org, install it, then open the file again."
            )
            self.txt_commands.setPlainText("")
            for widget in (self.txt_commands, self.btn_copy, self.btn_install):
                widget.setVisible(False)
            self.btn_download.setDefault(True)
            return
        self.lbl_title.setText(f"Install with {plan.name}")
        self.lbl_note.setText(plan.note + " These commands run when you click Install:")
        self.txt_commands.setPlainText(plan.shown())
        self.btn_install.setText("Install")
        self.btn_install.setEnabled(True)

    def start(self) -> None:
        plan = self.plan()
        if plan is None:
            return
        self.steps = [list(step) for step in plan.steps]
        self.btn_install.setEnabled(False)
        self.lbl_status.setText("Installing…")
        self._next_step()

    def _next_step(self) -> None:
        if not self.steps:
            self._finished_plan()
            return
        step = self.steps.pop(0)
        self.txt_output.appendPlainText("$ " + " ".join(step))
        process = QProcess(self)
        environment = QProcessEnvironment()
        for key, value in install_env().items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._step_finished)
        process.errorOccurred.connect(self._step_error)
        self.process = process
        process.start(step[0], step[1:])

    def _read_output(self) -> None:
        if self.process is not None:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self.txt_output.insertPlainText(text)
            self.txt_output.ensureCursorVisible()

    def _step_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._failed(f"{self.process.program()} couldn't be started.")

    def _step_finished(self, code: int, status) -> None:
        self._read_output()
        if status != QProcess.ExitStatus.NormalExit or code != 0:
            self._failed(f"The installer stopped with code {code}.")
            return
        self._next_step()

    def _finished_plan(self) -> None:
        self.process = None
        office.refresh()
        if office.find_office(app_settings().value(OFFICE_PATH_KEY, "", type=str)) is not None:
            self.found = True
            self.lbl_status.setText("LibreOffice is installed.")
            self.accept()
            return
        self._failed("The installer finished, but Riemann still can't find LibreOffice.")

    def _failed(self, message: str) -> None:
        self.process = None
        self.steps = []
        self.plan_index += 1
        following = self.plan()
        if following is not None:
            self.lbl_status.setText(f"{message} You can try {following.name} instead.")
        else:
            self.lbl_status.setText(f"{message} You can download LibreOffice from its website instead.")
        self._show_plan()

    def done(self, result: int) -> None:
        process = self.process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(3000)
        super().done(result)


def offer_office(parent, path: str, basic: bool = False) -> str:
    prompt = OfficePrompt(parent, os.path.basename(path), basic)
    if prompt.exec() != QDialog.DialogCode.Accepted:
        return ""
    if prompt.choice == "basic":
        return "basic"
    if prompt.choice != "install":
        return ""
    dialog = InstallDialog(parent)
    dialog.exec()
    return "office" if dialog.found else ""


def offer_basic_packages(parent) -> bool:
    """Asks to install the basic view's packages, and installs them; True when that worked."""
    from ..core.dependencies import OFFICE_VIEWERS_LOCK, OFFICE_VIEWERS_REQUIREMENTS, DependencyWorker

    answer = QMessageBox.question(
        parent,
        "Basic view",
        "The basic view needs a few Python packages that aren't installed: "
        + ", ".join(OFFICE_VIEWERS_REQUIREMENTS)
        + ". Riemann installs the versions it was tested with, checked by their hashes. Install them now?",
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    progress = QProgressDialog("Installing the basic-view packages…", None, 0, 0, parent)
    progress.setWindowTitle("Basic view")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    worker = DependencyWorker("install", OFFICE_VIEWERS_LOCK)
    loop = QEventLoop()
    outcome = {"ok": False, "error": ""}

    def finished(_name: str, _action: str, ok: bool) -> None:
        outcome["ok"] = ok
        loop.quit()

    def failed(_name: str, _action: str, error: str) -> None:
        outcome["error"] = error
        loop.quit()

    worker.progress_update.connect(lambda text: progress.setLabelText(f"Installing the basic-view packages…\n{text[:70]}"))
    worker.finished_task.connect(finished)
    worker.error_occurred.connect(failed)
    progress.show()
    track(worker).start()
    loop.exec()
    worker.wait()
    progress.close()
    importlib.invalidate_caches()
    if not outcome["ok"]:
        QMessageBox.warning(parent, "Basic view", outcome["error"] or "The packages couldn't be installed.")
    return outcome["ok"]
