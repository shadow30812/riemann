"""
Filling in PDF forms in the reader.

The rendering mixin places editable controls over a page's form widgets and
records entries in form_values_cache, keyed by (page index, annotation
index). This mixin writes them into the PDF 1.5 s after the last change, on a
worker thread, through riemann.core.pdf_forms, when "Save annotations inside
PDF files" is on; pending entries are written when the tab closes, and
saving a copy includes them. A signed or encrypted form keeps its entries in
the reader only, and says so once.
"""

import threading

from PySide6.QtCore import QObject, QTimer, Signal

from ....core import pdf_annotations, pdf_forms
from .annotations import store_in_pdf_enabled
from .host import ReaderHostBase


class _FormBridge(QObject):
    written = Signal(str, str)


class FormsMixin(ReaderHostBase):
    _form_bridge_obj = None
    _form_timer = None
    _forms_blocked = False

    def _form_changed(self) -> None:
        if not store_in_pdf_enabled():
            return
        timer = self._form_timer
        if timer is None:
            timer = self._form_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(1500)
            timer.timeout.connect(self._save_forms)
        timer.start()

    def _form_bridge(self) -> _FormBridge:
        bridge = self._form_bridge_obj
        if bridge is None:
            bridge = self._form_bridge_obj = _FormBridge(self)
            bridge.written.connect(self._on_forms_written)
        return bridge

    def _save_forms(self, blocking: bool = False) -> None:
        path = self.current_path or ""
        values = dict(self.form_values_cache or {})
        if not path.lower().endswith(".pdf") or not values or self._forms_blocked:
            return
        bridge = self._form_bridge()

        def work():
            try:
                result = pdf_forms.write_form_values(path, values)
            except Exception as e:
                result = f"error: {e}"
            if blocking:
                self._on_forms_written(path, result)
            else:
                try:
                    bridge.written.emit(path, result)
                except RuntimeError:
                    pass

        if blocking:
            work()
        else:
            threading.Thread(target=work, name="riemann-forms").start()

    def _on_forms_written(self, path: str, result: str) -> None:
        if path != self.current_path:
            return
        if result == "ok":
            sig = pdf_annotations.file_signature(path)
            if self._anno_file_sig is not None:
                self._anno_file_sig = sig
                self._write_annotation_json()
            try:
                import os

                self._loaded_mtime = os.path.getmtime(path)
            except OSError:
                pass
        elif result == "blocked":
            self._forms_blocked = True
            self.show_toast("This PDF is signed or encrypted: form entries stay in Riemann. Save a copy to keep them.")
        elif result == "changed":
            self._form_changed()

    def flush_forms_to_pdf(self) -> None:
        timer = self._form_timer
        if timer is not None and timer.isActive():
            timer.stop()
            self._save_forms(blocking=True)
