"""
The card that appears when the mouse rests on a citation link.

It shows the reference the link points to (read from the reference list by
riemann.core.citations). "Look it up" asks OpenAlex, on a worker thread, for
the work's title, authors, year and citation count; "Open beside" then opens
its arXiv page, an open-access copy or its DOI in the split pane, so the
cited paper sits next to the one being read. "Go to reference" follows the
link as a click would. Nothing is sent to OpenAlex unless the user asks.
"""


from PySide6.QtCore import QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ...core import citations
from ..palette import tokens, ui_font
from ..threads import track


class _Lookup(QThread):
    found = Signal(str, object)

    def __init__(self, reference: str) -> None:
        super().__init__()
        self.reference = reference

    def run(self) -> None:
        self.found.emit(self.reference, citations.lookup(self.reference))


class CitationCard(QFrame):
    go_to = Signal(int, object)
    open_beside = Signal(str)

    WIDTH = 440

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setObjectName("citationCard")
        self.setFixedWidth(self.WIDTH)
        self.target: tuple | None = None
        self.reference = ""
        self.info = None
        self._lookups: dict[str, object] = {}
        self._worker: _Lookup | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setFont(ui_font(12))
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.found = QLabel()
        self.found.setWordWrap(True)
        self.found.setFont(ui_font(13, QFont.Weight.Medium))
        self.found.hide()
        self.meta = QLabel()
        self.meta.setWordWrap(True)
        self.meta.setFont(ui_font(11))
        self.meta.hide()
        layout.addWidget(self.text)
        layout.addWidget(self.found)
        layout.addWidget(self.meta)
        row = QHBoxLayout()
        self.btn_lookup = QPushButton("Look it up")
        self.btn_lookup.setToolTip("Find this work on OpenAlex (sends the reference to openalex.org)")
        self.btn_open = QPushButton("Open beside")
        self.btn_open.hide()
        self.btn_copy = QPushButton("Copy")
        self.btn_go = QPushButton("Go to reference")
        for b in (self.btn_lookup, self.btn_open, self.btn_copy, self.btn_go):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)
        self.btn_lookup.clicked.connect(self.look_up)
        self.btn_open.clicked.connect(self._open)
        self.btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.reference))
        self.btn_go.clicked.connect(self._go)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(450)
        self._hide_timer.timeout.connect(self._maybe_hide)
        self.hide()

    def apply_theme(self, dark: bool) -> None:
        t = tokens(dark)
        self.setStyleSheet(
            f"""
            QFrame#citationCard {{ background: {t["surface"]}; border: 1px solid {t["border_strong"]};
                border-radius: 10px; }}
            QLabel {{ color: {t["text"]}; background: transparent; }}
            """
        )
        self.meta.setStyleSheet(f"color: {t['muted']};")
        self.text.setStyleSheet(f"color: {t['muted']};")

    def show_for(self, reference: str, target: tuple, pos: QPoint) -> None:
        if reference != self.reference:
            self.reference = reference
            self.info = self._lookups.get(reference)
            self.text.setText(reference)
            self._show_info()
        self.target = target
        self.adjustSize()
        parent = self.parentWidget()
        x = min(max(8, pos.x() - 40), parent.width() - self.width() - 8)
        y = pos.y() + 18
        if y + self.height() > parent.height() - 8:
            y = max(8, pos.y() - self.height() - 12)
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide_timer.stop()

    def leave_soon(self) -> None:
        if self.isVisible() and not self._hide_timer.isActive():
            self._hide_timer.start()

    def _maybe_hide(self) -> None:
        if not self.underMouse():
            self.hide()

    def leaveEvent(self, event) -> None:
        self.leave_soon()
        super().leaveEvent(event)

    def enterEvent(self, event) -> None:
        self._hide_timer.stop()
        super().enterEvent(event)

    def look_up(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_lookup.setEnabled(False)
        self.btn_lookup.setText("Looking…")
        self._worker = _Lookup(self.reference)
        self._worker.found.connect(self._on_found)
        track(self._worker).start()

    def _on_found(self, reference: str, info) -> None:
        self._lookups[reference] = info if info else False
        self.btn_lookup.setText("Look it up")
        self.btn_lookup.setEnabled(True)
        if reference == self.reference:
            self.info = self._lookups[reference]
            self._show_info()
            self.adjustSize()

    def _show_info(self) -> None:
        info = self.info
        if info is None:
            self.found.hide()
            self.meta.hide()
            self.btn_open.hide()
            self.btn_lookup.show()
            return
        self.btn_lookup.hide()
        if info is False:
            self.found.setText("Not found on OpenAlex.")
            self.found.show()
            self.meta.hide()
            self.btn_open.hide()
            return
        authors = info.get("authors") or []
        who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        bits = [b for b in (who, str(info.get("year") or ""), f"cited {info.get('cited_by', 0):,} times") if b]
        self.found.setText(info.get("title") or "")
        self.meta.setText(" · ".join(bits))
        self.found.show()
        self.meta.show()
        self.btn_open.setVisible(bool(citations.best_link(info)))

    def _open(self) -> None:
        if self.info:
            self.open_beside.emit(citations.best_link(self.info))
            self.hide()

    def _go(self) -> None:
        if self.target is not None:
            self.go_to.emit(self.target[0], self.target[1])
        self.hide()
