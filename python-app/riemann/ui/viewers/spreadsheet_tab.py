"""
Spreadsheets (Excel, OpenDocument) in the table view.

SpreadsheetTab is a TableTab that opens at once, says it is opening, and
fills in its sheets when a SpreadsheetWorker is done:

1. When LibreOffice is installed it saves the workbook again as .xlsx
   (core/office.py, cached like a PDF). That recalculates the formulas, since
   files written by other programs often carry no saved values, and turns
   .xls and .ods into one format.
2. The "spreadsheet" converter (core/office_basic.py) reads that file, or the
   original when LibreOffice isn't there, in a child process with a time
   limit, and writes each visible sheet's rows as JSON lines into a folder
   of the tab's own.
3. The tab reads those rows lazily, CHUNK_ROWS at a time like a CSV file,
   with the merged cells and formulas the converter found. The folder is
   deleted when the tab closes, after the files are closed.

With LibreOffice, "Show as pages" asks the window for a reader tab that shows
the workbook as LibreOffice prints it, charts and pictures included.
Closing the tab while it opens cancels both steps. In an incognito window
(incognito=True) LibreOffice's copy goes to a temporary folder of the tab's
own, deleted when it closes, so nothing stays in the cache.
"""

import json
import os
import shutil
import tempfile
import threading
from typing import Iterator, Optional

import shiboken6
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtWidgets import QToolButton

from ...core.convert_process import Conversion, ConvertError
from ...core.office import OfficeConversion, OfficeError, find_office
from ...core.office_basic import basic_format, packages_ready
from ...core.settings import OFFICE_PATH_KEY, app_settings
from ..threads import release, track
from .table_tab import TableSheet, TableTab


class SpreadsheetWorker(QThread):
    loaded = Signal(str, object, bool)
    failed = Signal(str, str)

    def __init__(self, path: str, override: str = "", folder: Optional[str] = None):
        super().__init__()
        self.path = path
        self.override = override
        self.folder = folder
        self._lock = threading.Lock()
        self._cancelled = False
        self._steps: list = []

    def _step(self, step):
        with self._lock:
            if self._cancelled:
                raise OfficeError("Cancelled.")
            self._steps.append(step)
        return step

    def run(self):
        source = self.path
        office = find_office(self.override)
        name = os.path.basename(self.path)
        try:
            if office is not None:
                try:
                    source = self._step(OfficeConversion(self.path, "xlsx", office=office, folder=self.folder)).run()
                except OfficeError:
                    if self._cancelled:
                        return
                    source = self.path
            fmt = basic_format(source)
            if fmt is None:
                self.failed.emit(self.path, f"Riemann needs LibreOffice to show {name}, and it isn't installed.")
                return
            if not packages_ready(fmt):
                self.failed.emit(self.path, f"The package that reads {name} isn't installed.")
                return
            result = self._step(Conversion("spreadsheet", source)).run()
        except (OfficeError, ConvertError) as error:
            if not self._cancelled:
                self.failed.emit(self.path, str(error))
            return
        if self._cancelled:
            shutil.rmtree(result.get("folder", ""), ignore_errors=True)
            return
        self.loaded.emit(self.path, result, office is not None)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            steps = list(self._steps)
        for step in steps:
            step.cancel()


def _jsonl_rows(file: str) -> Iterator[list]:
    with open(file, encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


class SpreadsheetTab(TableTab):
    def __init__(self, path: str, dark_mode: bool = False, preview: bool = False, parent=None, incognito: bool = False):
        super().__init__(path, dark_mode, preview, sheets=[], parent=parent)
        self.folder: Optional[str] = None
        self.office_folder = tempfile.mkdtemp(prefix="riemann-office-") if incognito else None
        self.btn_pages = QToolButton()
        self.btn_pages.setProperty("icon_name", "file-text.svg")
        self.btn_pages.setToolTip("Show the workbook as LibreOffice prints it, charts and pictures included")
        self.btn_pages.setText("Show as pages")
        self.btn_pages.setAutoRaise(True)
        self.btn_pages.setIconSize(QSize(18, 18))
        self.btn_pages.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_pages.clicked.connect(self.show_as_pages)
        self.btn_pages.setVisible(False)
        bar = self.toolbar.layout()
        bar.insertWidget(bar.indexOf(self.btn_open_with), self.btn_pages)
        self.apply_ui_theme(dark_mode)
        self.status.setText(f"Opening {os.path.basename(self.current_path)}…")
        self.worker: Optional[SpreadsheetWorker] = SpreadsheetWorker(
            self.current_path, app_settings().value(OFFICE_PATH_KEY, "", type=str), self.office_folder
        )
        self.worker.loaded.connect(self._on_loaded)
        self.worker.failed.connect(self._on_failed)
        track(self.worker).start()

    def _on_loaded(self, path: str, result: dict, with_office: bool) -> None:
        self.worker = None
        self.folder = result.get("folder")
        sheets = []
        for entry in result.get("sheets", []):
            sheet = TableSheet(entry["name"], _jsonl_rows(entry["file"]))
            sheet.spans = [tuple(span) for span in entry.get("spans", [])]
            for key, formula in entry.get("formulas", {}).items():
                row, column = key.split(",")
                sheet.formulas[(int(row), int(column))] = formula
            sheets.append(sheet)
        self.btn_pages.setVisible(with_office and not self.is_preview)
        self.set_sheets(sheets)

    def _on_failed(self, path: str, message: str) -> None:
        self.worker = None
        self.error = message
        self.set_sheets([])

    def show_as_pages(self) -> None:
        window = self.window()
        if hasattr(window, "show_as_pages"):
            window.show_as_pages(self)

    def cleanup(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None and shiboken6.isValid(worker):
            worker.cancel()
            release(worker, worker.loaded, worker.failed)
        super().cleanup()
        for folder in (self.folder, self.office_folder):
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
        self.folder = self.office_folder = None
