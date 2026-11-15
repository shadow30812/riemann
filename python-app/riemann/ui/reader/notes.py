"""
Notes per paper: a Markdown file kept next to the PDF ("paper.notes.md"), or
in the app data folder when the PDF's folder is read-only, shown in a panel
at the right of the reader.

The panel has an editor and a preview. "Link this page" inserts
"[p. 7](#page=7)" at the cursor; in the preview such links jump to the page
(through the reader's navigate_to, so Back returns), and web links open in a
browser tab. Notes are saved 0.8 s after the last keystroke, atomically, and
the file is only created once something has been written.
"""

import hashlib
import os

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core.storage import app_data_dir, write_text_atomic
from ..palette import tokens, ui_font
from .utils import render_markdown_safely


def notes_path_for(pdf_path: str) -> str:
    """Where a document's notes live: beside it when that folder is writable."""
    folder = os.path.dirname(os.path.abspath(pdf_path))
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    beside = os.path.join(folder, f"{stem}.notes.md")
    if os.path.exists(beside) or os.access(folder, os.W_OK):
        return beside
    key = hashlib.sha256(os.path.abspath(pdf_path).encode("utf-8")).hexdigest()[:24]
    return os.path.join(app_data_dir(), "notes", f"{stem}-{key}.md")


class NotesPanel(QWidget):
    page_link_clicked = Signal(int)
    web_link_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("notesPanel")
        self.setMinimumWidth(280)
        self.path = ""
        self.current_page = 0
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel("Notes")
        self.title.setFont(ui_font(14))
        self.btn_link = QPushButton("Link this page")
        self.btn_link.setToolTip("Insert a link to the current page at the cursor")
        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setCheckable(True)
        head.addWidget(self.title, 1)
        head.addWidget(self.btn_link)
        head.addWidget(self.btn_preview)
        layout.addLayout(head)
        self.stack = QStackedWidget()
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Notes on this paper, in Markdown. They are saved next to the PDF.")
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.preview.anchorClicked.connect(self._on_link)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.preview)
        layout.addWidget(self.stack, 1)
        self.where = QLabel("")
        self.where.setWordWrap(True)
        self.where.setFont(ui_font(11))
        layout.addWidget(self.where)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save)
        self.editor.textChanged.connect(self._on_changed)
        self.btn_link.clicked.connect(self.insert_page_link)
        self.btn_preview.toggled.connect(self.set_preview)

    def load(self, pdf_path: str) -> None:
        self.save()
        self.path = notes_path_for(pdf_path) if pdf_path else ""
        text = ""
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = ""
        self._loading = True
        self.editor.setPlainText(text)
        self._loading = False
        self.where.setText(self.path)
        if self.btn_preview.isChecked():
            self.set_preview(True)

    def _on_changed(self) -> None:
        if not self._loading:
            self._save_timer.start()

    def save(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        if not self.path:
            return
        text = self.editor.toPlainText()
        if not text.strip() and not os.path.exists(self.path):
            return
        try:
            write_text_atomic(self.path, text)
        except OSError as e:
            self.where.setText(f"Could not save: {e}")

    def insert_page_link(self) -> None:
        provider = getattr(self, "page_provider", None)
        n = (provider() if provider else self.current_page) + 1
        self.editor.insertPlainText(f"[p. {n}](#page={n})")
        self.editor.setFocus()

    def set_preview(self, on: bool) -> None:
        if on:
            self.preview.setHtml(render_markdown_safely(self.editor.toPlainText()))
        self.stack.setCurrentIndex(1 if on else 0)
        self.btn_link.setEnabled(not on)

    def _on_link(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("#page="):
            try:
                self.page_link_clicked.emit(int(text.split("=", 1)[1]) - 1)
            except ValueError:
                pass
        elif url.scheme() in ("http", "https"):
            self.web_link_clicked.emit(text)

    def apply_theme(self, dark: bool) -> None:
        t = tokens(dark)
        self.setStyleSheet(
            f"""
            QWidget#notesPanel {{ background: {t["surface"]}; border-left: 1px solid {t["border"]}; }}
            QPlainTextEdit, QTextBrowser {{ background: {t["canvas"]}; color: {t["text"]};
                border: 1px solid {t["border"]}; border-radius: 8px; padding: 6px; }}
            QLabel {{ color: {t["muted"]}; }}
            """
        )
        self.title.setStyleSheet(f"color: {t['text']}; font-weight: 600;")
