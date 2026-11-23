"""
The table view: CSV and TSV files (and, through TableSheet, any other grid of
cells) shown as a sheet with a frozen header row, sorting, Find and copying.

A TableSheet holds one sheet's rows. Rows come from an iterator and are
loaded CHUNK_ROWS at a time (a CSV file stays open until its last row is read
or the tab closes): TableModel answers canFetchMore and fetchMore,
so QTableView asks for more as the person scrolls and a large export never
loads all at once. Each loaded row keeps its number in the file, which the row
headers show, so sorting doesn't lose where a row came from.

Sorting needs every row. A sheet that isn't fully loaded is read to the end
first, LOAD_STEP_ROWS per timer tick so the window stays responsive, and then
sorted; one longer than SORT_LIMIT_ROWS is never sorted and says so. Clicking a
sorted column again reverses it, and a third click restores the file's order.
Values sort by core.tables.sort_key: numbers by value, text without case,
empty cells last.

Find (Ctrl+F) searches the loaded rows from the current cell onwards and,
when they run out, loads more in the same timer steps until it finds a match
or reaches the end; Return finds the next match and Shift+Return the one
before. Ctrl+C copies the selected cells as tab-separated text, which pastes
into a spreadsheet as cells.

A sheet whose rows stop with a read error keeps the error (TableSheet.error)
and is never saved, since the rows after it would be lost. Bytes the file's
encoding couldn't decode show as U+FFFD but stay in the rows as they were read
(core.tables), so they reach the file again unless their cell is changed.

The status line shows the selected cell's name and content, or its formula
when the sheet has one for it (TableSheet.formulas, filled by spreadsheets).
Several sheets get tabs along the bottom.

"Open as text" (CSV and TSV only) asks the window to show the file in a reader
tab as plain text, as Riemann did before this view existed.

Editing (CSV and TSV only, not in previews, files up to EDIT_LIMIT_BYTES):
double-click a cell or press F2 to change it, Delete empties the selected
cells, Ctrl+Z undoes the last change. The header row stays as it is. Changes
live in the loaded rows until Ctrl+S (or Save in the toolbar), which asks
before it replaces the file: Save, Save As or Cancel. Saving reads the rows not
loaded yet, puts every row back in the file's order (a sorted view saves
unsorted) and writes them with core.tables.write_delimited. Save As moves the
tab to the new file (renamed tells the window). Closing the tab or the window,
or Open as text, with changes not saved asks Save, Don't Save or Cancel
(resolve_edits). Delete and Ctrl+Z are read by the view itself, not as
window-wide shortcuts, so the cell editor and the find box keep those keys;
Ctrl+C in either of them copies their text.
"""

import dataclasses
import os
import re
from collections.abc import Callable, Iterator

import shiboken6
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTabBar,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.assets import icon_path
from ...core.settings import app_settings, get_dialog_directory, save_last_directory
from ...core.tables import column_name, number_value, open_delimited, sort_key, write_delimited
from ..components import populate_open_with_menu
from ..palette import scrollbar_style, toast_style, tokens
from ..shortcuts import share_keys
from ..toast import Toast

CHUNK_ROWS = 1000
LOAD_STEP_ROWS = 5000
SORT_LIMIT_ROWS = 500_000
MAX_CELL_CHARS = 2000
EDIT_LIMIT_BYTES = 32 * 1024 * 1024
ESCAPED_BYTE = re.compile("[\udc80-\udcff]")


def shown(text: str) -> str:
    return ESCAPED_BYTE.sub("\ufffd", text)


class TableSheet:
    def __init__(self, name: str, rows: Iterator[list], header: list | None = None, first_row: int = 1):
        self.name = name
        self.header = header
        self.rows: list = []
        self.columns = len(header) if header else 0
        self.exhausted = False
        self.formulas: dict = {}
        self.spans: list = []
        self.delimited = None
        self.error = ""
        self._source = rows
        self._next_number = first_row

    def fetch(self, count: int) -> list:
        """Reads up to count more rows; returns the (number, cells) pairs added."""
        added = []
        for _ in range(count):
            try:
                cells = next(self._source)
            except StopIteration:
                self.exhausted = True
                break
            except Exception as error:
                self.error = str(error) or type(error).__name__
                self.exhausted = True
                break
            added.append((self._next_number, ["" if c is None else str(c) for c in cells]))
            self._next_number += 1
        self.rows.extend(added)
        if added:
            self.columns = max(self.columns, max(len(cells) for _, cells in added))
        if self.exhausted:
            self.close()
        return added

    def close(self) -> None:
        """Lets go of the source, which may hold the file open."""
        close = getattr(self._source, "close", None)
        if close is not None:
            close()


def csv_sheets(path: str) -> list:
    """The one sheet of a CSV or TSV file, its header taken out when the file has one."""
    source = open_delimited(path)
    rows = source.rows()
    header = None
    if source.has_header:
        header = next(rows, None)
    sheet = TableSheet(os.path.basename(path), rows, header, first_row=2 if header else 1)
    sheet.delimited = source
    return [sheet]


class TableModel(QAbstractTableModel):
    edited = Signal()

    def __init__(self, sheet: TableSheet, parent=None):
        super().__init__(parent)
        self.sheet = sheet
        self.sort_state: tuple | None = None
        self.editable = False
        self.modified = False
        self.undo_stack: list = []
        self.saved_depth = 0

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.sheet.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self.sheet.columns

    def cell(self, row: int, column: int) -> str:
        cells = self.sheet.rows[row][1]
        return cells[column] if column < len(cells) else ""

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            text = shown(self.cell(index.row(), index.column()))
            return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "…"
        if role == Qt.ItemDataRole.EditRole:
            return shown(self.cell(index.row(), index.column()))
        if role == Qt.ItemDataRole.ToolTipRole:
            text = shown(self.cell(index.row(), index.column()))
            return text[:MAX_CELL_CHARS] if len(text) > 40 or "\n" in text else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if number_value(self.cell(index.row(), index.column())) is not None:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: QModelIndex):
        flags = super().flags(index)
        if self.editable and index.isValid():
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not self.editable:
            return False
        text = "" if value is None else str(value)
        if text == shown(self.cell(index.row(), index.column())):
            return True
        change = self._write(index.row(), index.column(), text)
        if change is not None:
            self._push([change])
        return True

    def _push(self, changes: list) -> None:
        if len(self.undo_stack) < self.saved_depth:
            self.saved_depth = -1
        self.undo_stack.append(changes)
        self.modified = True
        self.edited.emit()

    def mark_saved(self) -> None:
        self.saved_depth = len(self.undo_stack)
        self.modified = False
        self.edited.emit()

    def _write(self, row: int, column: int, text: str) -> tuple | None:
        """Sets one cell; returns (row entry, column, old text) for undo, or None when nothing changed."""
        entry = self.sheet.rows[row]
        cells = entry[1]
        old = cells[column] if column < len(cells) else ""
        if old == text:
            return None
        if column >= len(cells):
            cells.extend([""] * (column + 1 - len(cells)))
        cells[column] = text
        index = self.index(row, column)
        self.dataChanged.emit(index, index)
        return entry, column, old

    def clear_cells(self, indexes: list) -> int:
        changes = [c for c in (self._write(i.row(), i.column(), "") for i in indexes if i.isValid()) if c]
        if changes:
            self._push(changes)
        return len(changes)

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        positions = {id(entry): row for row, entry in enumerate(self.sheet.rows)}
        for entry, column, old in reversed(self.undo_stack.pop()):
            row = positions.get(id(entry))
            if row is not None:
                self._write(row, column, old)
        self.modified = len(self.undo_stack) != self.saved_depth
        self.edited.emit()
        return True

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            header = self.sheet.header
            if header and section < len(header) and str(header[section]).strip():
                return str(header[section])
            return column_name(section)
        if section < len(self.sheet.rows):
            return str(self.sheet.rows[section][0])
        return None

    def canFetchMore(self, parent=QModelIndex()) -> bool:
        return not parent.isValid() and not self.sheet.exhausted

    def fetchMore(self, parent=QModelIndex(), count: int = CHUNK_ROWS) -> int:
        if parent.isValid() or self.sheet.exhausted:
            return 0
        start, columns = len(self.sheet.rows), self.sheet.columns
        if start == 0:
            self.beginResetModel()
            added = self.sheet.fetch(count)
            self.endResetModel()
            return len(added)
        added = self.sheet.fetch(count)
        if self.sheet.columns > columns:
            self.beginInsertColumns(QModelIndex(), columns, self.sheet.columns - 1)
            self.endInsertColumns()
        if added:
            self.beginInsertRows(QModelIndex(), start, start + len(added) - 1)
            self.endInsertRows()
        return len(added)

    def sort_rows(self, column: int, descending: bool | None) -> None:
        self.layoutAboutToBeChanged.emit()
        if descending is None:
            self.sheet.rows.sort(key=lambda row: row[0])
            self.sort_state = None
        else:
            keys = {id(row): sort_key(row[1][column] if column < len(row[1]) else "") for row in self.sheet.rows}
            self.sheet.rows.sort(key=lambda row: keys[id(row)])
            if descending:
                filled = sum(1 for row in self.sheet.rows if keys[id(row)][0] != 2)
                self.sheet.rows[:] = self.sheet.rows[:filled][::-1] + self.sheet.rows[filled:]
            self.sort_state = (column, descending)
        self.layoutChanged.emit()


class TableTab(QWidget):
    renamed = Signal()

    is_preview = False

    def __init__(
        self,
        path: str,
        dark_mode: bool = False,
        preview: bool = False,
        sheets: list | None = None,
        parent=None,
        loader: Callable[[str], list] | None = None,
    ):
        super().__init__(parent)
        self.current_path = os.path.abspath(path)
        self.is_preview = preview
        self.dark_mode = dark_mode
        self.lbl_toast: Toast | None = None
        self.models: list = []
        self.error = ""
        self.editable = False
        self._pending: Callable[[], bool] | None = None
        self._step_timer = QTimer(self)
        self._step_timer.setInterval(0)
        self._step_timer.timeout.connect(self._load_step)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QWidget()
        self.toolbar.setObjectName("tableToolbar")
        self.toolbar.setFixedHeight(44)
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(8, 4, 8, 4)
        bar.setSpacing(4)
        self.lbl_size = QLabel()
        bar.addWidget(self.lbl_size)
        bar.addStretch(1)
        self.btn_save = self._button(bar, "save.svg", "Save the changes to the file (Ctrl+S)", self.save)
        self.btn_save.setText("Save")
        self.btn_save.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_save.setVisible(False)
        self.btn_find = self._button(bar, "search.svg", "Find (Ctrl+F)", self.show_find)
        self.btn_text = self._button(bar, "file-text.svg", "Open as text", self.open_as_text)
        self.btn_text.setText("Open as text")
        self.btn_text.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_open_with = QToolButton()
        self.btn_open_with.setText("Open with")
        self.btn_open_with.setToolTip("Open with another application")
        self.btn_open_with.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        open_menu = QMenu(self.btn_open_with)
        open_menu.aboutToShow.connect(lambda: populate_open_with_menu(open_menu, self.current_path, self))
        self.btn_open_with.setMenu(open_menu)
        bar.addWidget(self.btn_open_with)
        layout.addWidget(self.toolbar)

        self.find_bar = QWidget()
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(8, 4, 8, 4)
        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("Find in the table")
        self.txt_find.returnPressed.connect(lambda: self.find_next(backwards=False))
        self.txt_find.installEventFilter(self)
        self.lbl_find = QLabel()
        find_layout.addWidget(self.txt_find, 1)
        find_layout.addWidget(self.lbl_find)
        self.find_bar.hide()
        layout.addWidget(self.find_bar)

        self.view = QTableView()
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setWordWrap(False)
        self.view.setSortingEnabled(False)
        self.view.horizontalHeader().setSectionsClickable(True)
        self.view.horizontalHeader().setSortIndicatorShown(False)
        self.view.horizontalHeader().sectionClicked.connect(self.sort_by)
        self.view.horizontalHeader().setDefaultSectionSize(120)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.view.verticalHeader().setDefaultSectionSize(24)
        self.view.installEventFilter(self)
        layout.addWidget(self.view, 1)

        self.sheet_tabs = QTabBar()
        self.sheet_tabs.setShape(QTabBar.Shape.RoundedSouth)
        self.sheet_tabs.setDocumentMode(True)
        self.sheet_tabs.currentChanged.connect(self.show_sheet)
        layout.addWidget(self.sheet_tabs)
        self.status = QLabel()
        self.status.setObjectName("tableStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

        for seq, slot in (("Ctrl+F", self.show_find), ("Ctrl+C", self.copy_cells), ("Ctrl+S", self.save)):
            QShortcut(QKeySequence(seq), self).activated.connect(slot)
        share_keys(self)

        self.apply_ui_theme(dark_mode)
        delimited = sheets is None and loader is None
        try:
            size = os.path.getsize(self.current_path)
        except OSError:
            size = EDIT_LIMIT_BYTES + 1
        self.editable = delimited and not preview and size <= EDIT_LIMIT_BYTES
        if self.editable:
            self.view.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
            )
        try:
            sheets = sheets if sheets is not None else (loader or csv_sheets)(self.current_path)
        except Exception as error:
            sheets = []
            self.error = f"{os.path.basename(self.current_path)} can't be read: {error}"
        self.btn_text.setVisible(delimited)
        self.set_sheets(sheets)

    def _button(self, bar: QHBoxLayout, icon: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setProperty("icon_name", icon)
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setIconSize(QSize(18, 18))
        button.clicked.connect(slot)
        bar.addWidget(button)
        return button

    def set_sheets(self, sheets: list) -> None:
        self.models = [TableModel(sheet, self) for sheet in sheets]
        for model in self.models:
            model.editable = self.editable and model.sheet.delimited is not None
            model.edited.connect(self._update_save)
            model.rowsInserted.connect(self._update_size)
            model.columnsInserted.connect(self._update_size)
            if model.canFetchMore():
                model.fetchMore()
        while self.sheet_tabs.count():
            self.sheet_tabs.removeTab(0)
        for sheet in sheets:
            self.sheet_tabs.addTab(sheet.name)
        self.sheet_tabs.setVisible(len(sheets) > 1)
        if self.models:
            self.show_sheet(0)
        else:
            self.status.setText(self.error or "This file has no rows.")

    @property
    def model(self) -> TableModel | None:
        index = self.sheet_tabs.currentIndex() if self.sheet_tabs.count() else 0
        return self.models[index] if 0 <= index < len(self.models) else None

    def show_sheet(self, index: int) -> None:
        if not 0 <= index < len(self.models):
            return
        self._stop_loading()
        model = self.models[index]
        self.view.setModel(model)
        self.view.clearSpans()
        for row, column, rows, columns in model.sheet.spans:
            if row < model.rowCount():
                self.view.setSpan(row, column, rows, columns)
        self.view.selectionModel().currentChanged.connect(self._show_cell)
        self._update_size()
        self._show_cell(self.view.currentIndex())

    def _update_size(self, *_) -> None:
        model = self.model
        if model is None:
            return
        rows = model.rowCount()
        more = "" if model.sheet.exhausted else "+"
        self.lbl_size.setText(f"{rows:,}{more} rows  ·  {model.columnCount():,} columns")

    def _show_cell(self, index: QModelIndex, *_) -> None:
        model = self.model
        if model is None or not index.isValid():
            self.status.setText("")
            return
        number = model.sheet.rows[index.row()][0]
        name = f"{column_name(index.column())}{number}"
        formula = model.sheet.formulas.get((number, index.column()))
        text = formula or shown(model.cell(index.row(), index.column()))
        self.status.setText(f"{name}   {text[:MAX_CELL_CHARS]}".replace("\n", " ⏎ "))

    def sort_by(self, column: int) -> None:
        model = self.model
        if model is None:
            return
        state = model.sort_state
        if state is None or state[0] != column:
            order: bool | None = False
        elif state[1] is False:
            order = True
        else:
            order = None

        def finish() -> bool:
            if len(model.sheet.rows) > SORT_LIMIT_ROWS:
                self.show_toast(f"This sheet has more than {SORT_LIMIT_ROWS:,} rows, too many to sort here.")
                return True
            if not model.sheet.exhausted:
                return False
            model.sort_rows(column, order)
            header = self.view.horizontalHeader()
            header.setSortIndicatorShown(order is not None)
            if order is not None:
                header.setSortIndicator(column, Qt.SortOrder.DescendingOrder if order else Qt.SortOrder.AscendingOrder)
            return True

        self._run_loading(finish, "Reading every row to sort…")

    def _run_loading(self, done: Callable[[], bool], message: str) -> None:
        if done():
            return
        self._pending = done
        self.status.setText(message)
        self._step_timer.start()

    def _load_step(self) -> None:
        model = self.model
        if model is None or self._pending is None:
            self._stop_loading()
            return
        if not model.sheet.exhausted and len(model.sheet.rows) <= SORT_LIMIT_ROWS:
            model.fetchMore(count=LOAD_STEP_ROWS)
        if self._pending():
            self._stop_loading()
            self._show_cell(self.view.currentIndex())

    def _stop_loading(self) -> None:
        self._step_timer.stop()
        self._pending = None

    def show_find(self) -> None:
        self.find_bar.show()
        self.txt_find.setFocus()
        self.txt_find.selectAll()

    def hide_find(self) -> None:
        self._stop_loading()
        self.find_bar.hide()
        self.view.setFocus()

    def eventFilter(self, obj, event) -> bool:
        view = getattr(self, "view", None)
        if (
            view is not None
            and obj is view
            and event.type() == event.Type.KeyPress
            and self.model
            and self.model.editable
        ):
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not event.modifiers():
                count = self.model.clear_cells(self.view.selectionModel().selectedIndexes())
                if count > 1:
                    self.show_toast(f"Emptied {count:,} cells")
                return True
            if event.matches(QKeySequence.StandardKey.Undo):
                if not self.model.undo():
                    self.show_toast("Nothing to undo")
                return True
        if obj is self.txt_find and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.hide_find()
                return True
            if (
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self.find_next(backwards=True)
                return True
        return super().eventFilter(obj, event)

    def find_next(self, backwards: bool = False) -> None:
        model = self.model
        needle = self.txt_find.text().casefold()
        if model is None or not needle:
            return
        current = self.view.currentIndex()
        position = [current.row(), current.column()] if current.isValid() else [0, -1]
        if backwards and not current.isValid():
            position = [model.rowCount(), 0]

        def match(row: int, column: int) -> bool:
            return needle in model.cell(row, column).casefold()

        def search() -> bool:
            columns = model.columnCount()
            row, column = position
            if backwards:
                while row >= 0:
                    for c in range((column - 1) if row == position[0] else columns - 1, -1, -1):
                        if match(row, c):
                            self._select(row, c)
                            return True
                    row -= 1
                self.lbl_find.setText("No more matches above")
                return True
            while row < model.rowCount():
                for c in range(column + 1, columns):
                    if match(row, c):
                        self._select(row, c)
                        return True
                row += 1
                column = -1
            position[:] = [row, -1]
            if model.sheet.exhausted:
                self.lbl_find.setText("No more matches")
                return True
            return False

        self.lbl_find.setText("")
        self._run_loading(search, "Searching…")

    def _select(self, row: int, column: int) -> None:
        index = self.model.index(row, column)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.lbl_find.setText("")

    def copy_cells(self) -> None:
        focused = self.cell_editor() or (self.txt_find if self.txt_find.hasFocus() else None)
        if focused is not None:
            focused.copy()
            return
        model = self.model
        indexes = self.view.selectionModel().selectedIndexes() if model is not None else []
        if not indexes:
            return
        rows = sorted({i.row() for i in indexes})
        columns = sorted({i.column() for i in indexes})
        chosen = {(i.row(), i.column()) for i in indexes}
        lines = [
            "\t".join(
                shown(model.cell(r, c)).replace("\t", " ").replace("\n", " ") if (r, c) in chosen else ""
                for c in columns
            )
            for r in rows
        ]
        QApplication.clipboard().setText("\n".join(lines))
        self.show_toast(f"Copied {len(indexes):,} cells" if len(indexes) > 1 else "Copied the cell")

    @property
    def modified(self) -> bool:
        return any(model.modified for model in self.models)

    def _update_save(self) -> None:
        self.btn_save.setVisible(self.modified)

    def cell_editor(self) -> QLineEdit | None:
        """The editor of the cell being changed, if one is open."""
        if self.view.state() != QAbstractItemView.State.EditingState:
            return None
        editors = [e for e in self.view.viewport().findChildren(QLineEdit) if e.isVisible()]
        return editors[0] if editors else None

    def _commit_editor(self) -> None:
        editor = self.cell_editor()
        if editor is not None:
            self.view.commitData(editor)
            self.view.closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    def _ask(self, closing: bool) -> str:
        name = os.path.basename(self.current_path)
        box = QMessageBox(self)
        box.setWindowTitle("Save changes")
        box.setIcon(QMessageBox.Icon.Question)
        if closing:
            box.setText(f"{name} has changes that aren't saved. Save them? Saving replaces the file.")
        else:
            box.setText(f"Save your changes to {name}? This replaces the file.")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        save_as = box.addButton("Save As…", QMessageBox.ButtonRole.ActionRole)
        discard = box.addButton("Don't Save", QMessageBox.ButtonRole.DestructiveRole) if closing else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is save_as:
            return "save_as"
        if discard is not None and clicked is discard:
            return "discard"
        return ""

    def save(self) -> bool:
        """Asks, then writes the changes; True when they were saved."""
        if not self.editable:
            return False
        self._commit_editor()
        if not self.modified:
            self.show_toast("There are no changes to save")
            return False
        return self._save_as_chosen(self._ask(closing=False))

    def _save_as_chosen(self, choice: str) -> bool:
        if choice == "save":
            return self._write_file(self.current_path)
        if choice == "save_as":
            suffix = os.path.splitext(self.current_path)[1]
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save As",
                os.path.join(get_dialog_directory(app_settings()), os.path.basename(self.current_path)),
                f"Table (*{suffix})" if suffix else "",
            )
            if not path:
                return False
            save_last_directory(app_settings(), path)
            return self._write_file(path)
        return False

    def resolve_edits(self) -> bool:
        """Before the tab closes: asks about changes not saved; False when the person cancelled."""
        self._commit_editor()
        if not self.modified:
            return True
        choice = self._ask(closing=True)
        if choice == "discard":
            for model in self.models:
                model.modified = False
            return True
        return self._save_as_chosen(choice)

    def _write_file(self, path: str) -> bool:
        model = self.models[0]
        sheet = model.sheet
        self._stop_loading()
        while not sheet.exhausted:
            model.fetchMore(count=LOAD_STEP_ROWS)
        if sheet.error:
            QMessageBox.warning(
                self,
                "Save changes",
                f"{os.path.basename(self.current_path)} couldn't be read to the end ({sheet.error}), "
                "so saving it would lose the rows after that point.",
            )
            return False
        rows = [cells for _, cells in sorted(sheet.rows, key=lambda row: row[0])]
        if sheet.header is not None:
            rows.insert(0, ["" if c is None else str(c) for c in sheet.header])
        try:
            encoding = write_delimited(path, sheet.delimited, rows)
        except (OSError, UnicodeError) as error:
            QMessageBox.warning(self, "Save changes", f"{os.path.basename(path)} couldn't be saved: {error}")
            return False
        changed_encoding = encoding != sheet.delimited.encoding
        sheet.delimited = dataclasses.replace(sheet.delimited, path=os.path.abspath(path), encoding=encoding)
        model.mark_saved()
        if os.path.abspath(path) != self.current_path:
            self.current_path = os.path.abspath(path)
            sheet.name = os.path.basename(path)
            self.renamed.emit()
        if changed_encoding:
            self.show_toast("Saved as UTF-8, since the file's old encoding can't hold some of the text")
        else:
            self.show_toast(f"Saved {os.path.basename(path)}")
        return True

    def open_as_text(self) -> None:
        if not self.resolve_edits():
            return
        window = self.window()
        if hasattr(window, "open_as_text"):
            window.open_as_text(self)

    def _update_icons(self) -> None:
        for button in self.toolbar.findChildren(QToolButton):
            name = button.property("icon_name")
            if name:
                button.setIcon(QIcon(icon_path(name, self.dark_mode)))

    def apply_ui_theme(self, dark: bool) -> None:
        self.dark_mode = dark
        t = tokens(dark)
        self.setStyleSheet(
            f"QWidget#tableToolbar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}"
            f"QLabel {{ color: {t['muted']}; background: transparent; }}"
            f"QLabel#tableStatus {{ background: {t['surface']}; color: {t['text']}; padding: 4px 10px;"
            f" border-top: 1px solid {t['border']}; }}"
            f"QToolButton {{ color: {t['text']}; border: 1px solid transparent; border-radius: 4px; padding: 5px; "
            f"background: transparent; }}"
            f"QToolButton:hover {{ background: {t['raised']}; }}"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
            f"QTableView {{ background: {t['surface']}; color: {t['text']}; gridline-color: {t['border']};"
            f" selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; border: none; }}"
            f"QHeaderView::section {{ background: {t['raised']}; color: {t['muted']}; border: none;"
            f" border-right: 1px solid {t['border']}; border-bottom: 1px solid {t['border']}; padding: 3px 6px; }}"
            f"QTableCornerButton::section {{ background: {t['raised']}; border: none; }}"
            f"QLineEdit {{ background: {t['canvas']}; color: {t['text']}; border: 1px solid {t['border_strong']};"
            f" border-radius: 6px; padding: 4px; }}" + scrollbar_style(t)
        )
        self._update_icons()

    def toggle_theme(self) -> None:
        self.apply_ui_theme(not self.dark_mode)

    def show_toast(self, message: str) -> None:
        if self.lbl_toast is None:
            self.lbl_toast = Toast(self)
        self.lbl_toast.show_message(message, toast_style(tokens(self.dark_mode)), 3000)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.find_bar.isVisible():
            self.hide_find()
            event.accept()
            return
        super().keyPressEvent(event)

    def cleanup(self) -> None:
        self._stop_loading()
        if shiboken6.isValid(self.view):
            self.view.setModel(None)
        for model in self.models:
            model.sheet.close()
        self.models = []
