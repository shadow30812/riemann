"""
The hex view: any file Riemann has no viewer for, shown as its bytes.

Each row is an offset, sixteen bytes in hex and the same bytes as text
(core/binary.py explains why showing bytes is safe for any file). HexModel
reads the file a block at a time as rows scroll into view and keeps the
last CACHED_BLOCKS blocks, so a file of any size opens at once; the table
has fixed row heights, so Qt never measures rows it doesn't draw.

A banner above the bytes says what Riemann knows: a program (core.binary
.program_kind) gets the shield icon in the warning colour and the promise
that Riemann never runs it, and has no Open with button; a file Riemann
can't read gets the same icon in the danger colour and the system's reason;
any other file gets a plain note. The file is never written.

Like the other viewers it has current_path, is_preview, cleanup(),
toggle_theme() (N switches the bytes between dark and light) and a toolbar
the window hides in full screen. The table takes letters for type-to-search,
so a filter on it (not on the application) passes F (full screen) and Esc
(leave it) to the tab, as the picture viewer has them.
"""

import os
from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QSize, Qt
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.assets import icon_path
from ...core.binary import BLOCK_BYTES, ROW_BYTES, hex_row, program_kind, read_block, text_row
from ..components import populate_open_with_menu
from ..palette import banner_style, tinted_pixmap, toast_style, tokens
from ..toast import Toast

CACHED_BLOCKS = 32


def file_size_text(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


class HexModel(QAbstractTableModel):
    HEADERS = ("Offset", "Hex", "Text")

    def __init__(self, path: str, size: int, parent=None) -> None:
        super().__init__(parent)
        self.path = path
        self.size = size
        self._blocks: OrderedDict = OrderedDict()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else (self.size + ROW_BYTES - 1) // ROW_BYTES

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def row_bytes(self, row: int) -> bytes:
        start = row * ROW_BYTES
        block = start // BLOCK_BYTES
        data = self._blocks.get(block)
        if data is None:
            try:
                data = read_block(self.path, block * BLOCK_BYTES)
            except OSError:
                data = b""
            self._blocks[block] = data
            while len(self._blocks) > CACHED_BLOCKS:
                self._blocks.popitem(last=False)
        else:
            self._blocks.move_to_end(block)
        offset = start - block * BLOCK_BYTES
        return data[offset:offset + ROW_BYTES]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or not index.isValid():
            return None
        if index.column() == 0:
            return f"{index.row() * ROW_BYTES:08X}"
        data = self.row_bytes(index.row())
        return hex_row(data) if index.column() == 1 else text_row(data)


class HexTab(QWidget):
    is_preview = False

    def __init__(self, path: str, dark_mode: bool = False, preview: bool = False, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.current_path = os.path.abspath(path)
        self.is_preview = preview
        self.dark_mode = dark_mode
        self.bytes_dark = dark_mode
        self.lbl_toast: Toast | None = None
        self.program = program_kind(self.current_path)
        self.error = ""
        size = 0
        try:
            size = os.path.getsize(self.current_path)
            with open(self.current_path, "rb"):
                pass
        except OSError as error:
            self.error = error.strerror or str(error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QWidget()
        self.toolbar.setObjectName("hexToolbar")
        self.toolbar.setFixedHeight(44)
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(12, 4, 8, 4)
        bar.setSpacing(6)
        self.lbl_info = QLabel(self._info(size))
        bar.addWidget(self.lbl_info)
        bar.addStretch(1)
        self.btn_open_with = None
        if not self.program and not self.error:
            self.btn_open_with = QToolButton()
            self.btn_open_with.setText("Open with")
            self.btn_open_with.setToolTip("Open with another application")
            self.btn_open_with.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            open_menu = QMenu(self.btn_open_with)
            open_menu.aboutToShow.connect(lambda: populate_open_with_menu(open_menu, self.current_path, self))
            self.btn_open_with.setMenu(open_menu)
            bar.addWidget(self.btn_open_with)
        self.btn_fullscreen = QToolButton()
        self.btn_fullscreen.setProperty("icon_name", "maximize.svg")
        self.btn_fullscreen.setToolTip("Full screen (F)")
        self.btn_fullscreen.setAutoRaise(True)
        self.btn_fullscreen.setIconSize(QSize(18, 18))
        self.btn_fullscreen.clicked.connect(self.toggle_reader_fullscreen)
        bar.addWidget(self.btn_fullscreen)
        layout.addWidget(self.toolbar)

        self.banner = QFrame()
        self.banner.setObjectName("hexBanner")
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)
        self.lbl_banner_icon = QLabel()
        self.lbl_banner = QLabel(self.banner_text())
        self.lbl_banner.setWordWrap(True)
        row.addWidget(self.lbl_banner_icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.lbl_banner, 1)
        layout.addWidget(self.banner)

        self.view = QTableView()
        self.view.setObjectName("hexView")
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.view.verticalHeader().setDefaultSectionSize(self.view.fontMetrics().height() + 6)
        self.model = HexModel(self.current_path, 0 if self.error else size, self)
        self.view.setModel(self.model)
        header = self.view.horizontalHeader()
        width = self.view.fontMetrics().horizontalAdvance
        header.resizeSection(0, width("0" * 10) + 16)
        header.resizeSection(1, width("0" * (ROW_BYTES * 3 + 1)) + 24)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)
        self.view.installEventFilter(self)
        layout.addWidget(self.view, 1)
        self.view.setVisible(not self.error)
        if self.error:
            layout.addStretch(1)

        self.apply_ui_theme(dark_mode)

    def _info(self, size: int) -> str:
        name = os.path.basename(self.current_path)
        return name if self.error else f"{name}  ·  {file_size_text(size)}"

    def banner_text(self) -> str:
        name = os.path.basename(self.current_path)
        if self.error:
            return f"Riemann can't read {name}: {self.error}."
        if self.program:
            return (
                f"{name} is a program ({self.program}). Riemann shows its bytes and never runs it, "
                "and doesn't offer to open it in another application."
            )
        return f"Riemann has no viewer for this kind of file, so it shows the bytes. {name} isn't changed."

    def banner_tone(self) -> str:
        return "danger" if self.error else "warning" if self.program else "neutral"

    def _update_icons(self) -> None:
        name = self.btn_fullscreen.property("icon_name")
        self.btn_fullscreen.setIcon(QIcon(icon_path(name, self.dark_mode)))

    def apply_ui_theme(self, dark: bool) -> None:
        self.dark_mode = dark
        t = tokens(dark)
        self.toolbar.setStyleSheet(
            f"QWidget#hexToolbar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}"
            f"QLabel {{ color: {t['muted']}; background: transparent; }}"
            f"QToolButton {{ color: {t['text']}; border: 1px solid transparent; border-radius: 4px; padding: 5px; "
            f"background: transparent; }}"
            f"QToolButton:hover {{ background: {t['raised']}; }}"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        tone = self.banner_tone()
        self.banner.setStyleSheet(
            f"QFrame#hexBanner {{ {banner_style(t, tone)} }} QLabel {{ background: transparent; }}"
        )
        colour = t["text"] if tone == "neutral" else t["accent_text"]
        self.lbl_banner.setStyleSheet(f"color: {colour};")
        icon = "binary" if tone == "neutral" else "shield-cog-corner"
        self.lbl_banner_icon.setPixmap(tinted_pixmap(icon, colour, 20))
        self._style_bytes()
        self._update_icons()

    def _style_bytes(self) -> None:
        t = tokens(self.bytes_dark)
        self.view.setStyleSheet(
            f"QTableView#hexView {{ background: {t['canvas']}; color: {t['text']}; border: none;"
            f" selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}"
            f"QHeaderView::section {{ background: {t['surface']}; color: {t['muted']}; border: none;"
            f" border-bottom: 1px solid {t['border']}; padding: 4px 6px; }}"
        )

    def toggle_theme(self) -> None:
        self.bytes_dark = not self.bytes_dark
        self._style_bytes()
        self.show_toast("Dark bytes" if self.bytes_dark else "Light bytes")

    def toggle_reader_fullscreen(self) -> None:
        window = self.window()
        if hasattr(window, "toggle_reader_fullscreen"):
            window.toggle_reader_fullscreen()

    def _leave_fullscreen(self) -> None:
        window = self.window()
        if getattr(window, "_reader_fullscreen", False) and hasattr(window, "exit_fullscreen"):
            window.exit_fullscreen()

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.view
            and event.type() == QEvent.Type.KeyPress
            and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        ):
            keys = {Qt.Key.Key_F: self.toggle_reader_fullscreen, Qt.Key.Key_Escape: self._leave_fullscreen}
            action = keys.get(event.key())
            if action is not None:
                action()
                return True
        return super().eventFilter(obj, event)

    def update_fullscreen_icon(self, state: int) -> None:
        self.btn_fullscreen.setProperty("icon_name", "minimize.svg" if state == 2 else "maximize.svg")
        self._update_icons()

    def show_toast(self, message: str) -> None:
        if self.lbl_toast is None:
            self.lbl_toast = Toast(self)
        self.lbl_toast.show_message(message, toast_style(tokens(self.bytes_dark)), 2500)

    def cleanup(self) -> None:
        self.model._blocks.clear()
