"""
Filling in PDF forms in the reader.

The rendering mixin places editable controls over a page's form widgets and
records entries in form_values_cache, keyed by (page index, annotation
index). FormSaver writes them into the PDF 1.5 s after the last change, on a
worker thread, through riemann.core.pdf_forms, when "Save annotations inside
PDF files" is on; flush() writes pending entries at once (when the tab
closes), and saving a copy includes them. A signed or encrypted form keeps
its entries in the reader only, and says so once.

FormSaver is given path(), the file being written, and values(), the
entries. saved(path) says the file now holds them; toast carries messages.
"""

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ....core import pdf_forms
from .annotations import store_in_pdf_enabled

SAVE_DELAY_MS = 1500


class FormSaver(QObject):
    saved = Signal(str)
    toast = Signal(str)
    _written = Signal(str, str)

    def __init__(
        self,
        path: Callable[[], str],
        values: Callable[[], dict],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.values = values
        self.blocked = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self.save)
        self._written.connect(self._on_written)

    def changed(self) -> None:
        """An entry changed: writes the entries once they have stopped changing for a moment."""
        if store_in_pdf_enabled():
            self._timer.start()

    def flush(self) -> None:
        """Writes waiting entries now, before returning."""
        if self._timer.isActive():
            self._timer.stop()
            self.save(blocking=True)

    def save(self, blocking: bool = False) -> None:
        path = self.path()
        values = dict(self.values() or {})
        if not path.lower().endswith(".pdf") or not values or self.blocked:
            return

        def work() -> None:
            try:
                result = pdf_forms.write_form_values(path, values)
            except Exception as e:
                result = f"error: {e}"
            if blocking:
                self._on_written(path, result)
            else:
                try:
                    self._written.emit(path, result)
                except RuntimeError:
                    pass

        if blocking:
            work()
        else:
            threading.Thread(target=work, name="riemann-forms").start()

    def _on_written(self, path: str, result: str) -> None:
        if path != self.path():
            return
        if result == "ok":
            self.saved.emit(path)
        elif result == "blocked":
            self.blocked = True
            self.toast.emit("This PDF is signed or encrypted: form entries stay in Riemann. Save a copy to keep them.")
        elif result == "changed":
            self.changed()
