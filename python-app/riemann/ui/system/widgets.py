"""
Controls used on System pages: an editable list of strings, the site
permissions editor (defaults plus per-site exceptions, backed by
riemann.core.permissions), the read-aloud voice picker, and the reader
toolbar layout picker.

ToolbarLayoutEditor applies a choice at once to every open reader (through
riemann.ui.reader.toolbar_layout). The floating-controls switch and the table
of commands are always shown, filled in with what the chosen layout does
(they were hidden until Custom was chosen, so nobody knew they were there).
Changing one while a preset is chosen saves that preset, with the change, as
Custom and switches to it. Choosing Custom shows the saved custom layout, or
the active one the first time; every change is saved and applied straight
away.

VoicePicker lists the speech engine's voices only when it is first opened:
starting the engine can take a moment (speech-dispatcher on Linux), and most
visits to Settings never need it.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import permissions
from .base import card, setting_row

CHOICE_LABELS = {permissions.ASK: "Ask", permissions.ALLOW: "Allow", permissions.BLOCK: "Block"}
DEFAULT_LABEL = "Default"


class StringListEditor(QWidget):
    """A list of strings the user can add to, edit in place and remove from."""

    changed = Signal(list)

    def __init__(self, values: list[str], placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.list = QListWidget()
        self.list.setMinimumHeight(160)
        self.list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        for v in values:
            self._append(v)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(placeholder)
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove")
        row.addWidget(self.entry, 1)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_remove)
        layout.addLayout(row)
        self.btn_add.clicked.connect(self._add)
        self.entry.returnPressed.connect(self._add)
        self.btn_remove.clicked.connect(self._remove)
        self.list.itemChanged.connect(lambda _: self._emit())

    def _append(self, value: str) -> None:
        item = QListWidgetItem(value)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.addItem(item)

    def values(self) -> list[str]:
        return [self.list.item(i).text().strip() for i in range(self.list.count()) if self.list.item(i).text().strip()]

    def _add(self) -> None:
        text = self.entry.text().strip()
        if text and text not in self.values():
            self._append(text)
            self.entry.clear()
            self._emit()

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self._emit()

    def _emit(self) -> None:
        self.changed.emit(self.values())


class PermissionsEditor(QWidget):
    """
    Default answers for every permission and content setting, and the
    per-site exceptions. Edits apply at once; prompts answered in a browser
    tab show up here as they happen. The table is rebuilt on a zero-delay
    timer, never inside the signal of the combo box that changed.
    """

    def __init__(self, store: permissions.SitePermissions, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        asked = card(
            layout,
            "When a site asks",
            "What happens the first time a site asks to use these. Copying text with a click always works.",
        )
        self.default_boxes: dict[str, QComboBox] = {}
        for kind in permissions.KINDS:
            if kind.key in permissions.PROMPTED:
                self._default_row(asked, kind)
        content = card(layout, "Content", "Applied to every page before it loads.")
        for kind in permissions.KINDS:
            if kind.key in permissions.CONTENT:
                self._default_row(content, kind)

        sites = card(
            layout,
            "Site exceptions",
            "Choices remembered for individual sites. Set one back to Default to forget it.",
        )
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter sites")
        self.filter.textChanged.connect(self._apply_filter)
        sites.addWidget(self.filter)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Site", "Permission", "Choice"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(220)
        sites.addWidget(self.table)
        self.empty = QLabel("No site exceptions yet.")
        self.empty.setObjectName("hint")
        sites.addWidget(self.empty)

        add_row = QHBoxLayout()
        self.new_site = QLineEdit()
        self.new_site.setPlaceholderText("https://example.com")
        self.new_kind = QComboBox()
        for kind in permissions.KINDS:
            self.new_kind.addItem(kind.label, kind.key)
        self.new_value = QComboBox()
        self.new_kind.currentIndexChanged.connect(self._fill_new_value)
        self._fill_new_value()
        self.btn_add = QPushButton("Add exception")
        self.btn_add.clicked.connect(self._add_exception)
        self.btn_forget = QPushButton("Forget selected sites")
        self.btn_forget.clicked.connect(self._forget_selected)
        add_row.addWidget(self.new_site, 1)
        add_row.addWidget(self.new_kind)
        add_row.addWidget(self.new_value)
        add_row.addWidget(self.btn_add)
        sites.addLayout(add_row)
        sites.addWidget(self.btn_forget, 0, Qt.AlignmentFlag.AlignLeft)

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self.reload)
        self.reload()
        store.changed.connect(self._reload_timer.start)

    def _default_row(self, layout: QVBoxLayout, kind: permissions.Kind) -> None:
        box = QComboBox()
        for choice in kind.choices:
            box.addItem(CHOICE_LABELS[choice], choice)
        box.currentIndexChanged.connect(lambda _=0, k=kind.key, b=box: self._set_default(k, b))
        self.default_boxes[kind.key] = box
        setting_row(layout, kind.label, box, f"Sites that want to {kind.request}.")

    def _set_default(self, key: str, box: QComboBox) -> None:
        if not self._updating:
            self._updating = True
            try:
                self.store.set_default(key, box.currentData())
            finally:
                self._updating = False

    def _fill_new_value(self) -> None:
        kind = permissions.KIND_BY_KEY[self.new_kind.currentData()]
        self.new_value.clear()
        for choice in kind.choices:
            self.new_value.addItem(CHOICE_LABELS[choice], choice)

    def _add_exception(self) -> None:
        site = self.new_site.text().strip()
        if site and "://" not in site:
            site = "https://" + site
        if not permissions.site_of(site):
            self.new_site.setFocus()
            return
        self.store.set(site, self.new_kind.currentData(), self.new_value.currentData())
        self.new_site.clear()

    def _forget_selected(self) -> None:
        sites = {self.table.item(r.row(), 0).text() for r in self.table.selectionModel().selectedRows()}
        for site in sites:
            self.store.clear_site(site)

    def reload(self) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            for key, box in self.default_boxes.items():
                box.setCurrentIndex(max(0, box.findData(self.store.default(key))))
            self.table.setRowCount(0)
            for site, entries in self.store.sites().items():
                for key, value in sorted(entries.items()):
                    kind = permissions.KIND_BY_KEY[key]
                    row = self.table.rowCount()
                    self.table.insertRow(row)
                    self.table.setItem(row, 0, QTableWidgetItem(site))
                    self.table.setItem(row, 1, QTableWidgetItem(kind.label))
                    box = QComboBox()
                    box.addItem(DEFAULT_LABEL, None)
                    for choice in kind.choices:
                        box.addItem(CHOICE_LABELS[choice], choice)
                    box.setCurrentIndex(max(0, box.findData(value)))
                    box.currentIndexChanged.connect(
                        lambda _=0, s=site, k=key, b=box: self.store.set(s, k, b.currentData())
                    )
                    self.table.setCellWidget(row, 2, box)
            self.empty.setVisible(self.table.rowCount() == 0)
            self._apply_filter(self.filter.text())
        finally:
            self._updating = False

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            site = self.table.item(row, 0).text().lower()
            self.table.setRowHidden(row, bool(needle) and needle not in site)


class VoicePicker(QComboBox):
    SYSTEM_DEFAULT = "System default"

    def __init__(self, current: str, loader, parent=None) -> None:
        super().__init__(parent)
        self._loader = loader
        self._loaded = False
        self.addItem(self.SYSTEM_DEFAULT, "")
        if current:
            self.addItem(current, current)
            self.setCurrentIndex(1)

    def voice(self) -> str:
        return str(self.currentData() or "")

    def showPopup(self) -> None:
        if not self._loaded:
            self._loaded = True
            chosen = self.voice()
            self.blockSignals(True)
            self.clear()
            self.addItem(self.SYSTEM_DEFAULT, "")
            for name in self._loader():
                self.addItem(name, name)
            index = self.findData(chosen)
            if index < 0 and chosen:
                self.addItem(chosen, chosen)
                index = self.count() - 1
            self.setCurrentIndex(max(0, index))
            self.blockSignals(False)
        super().showPopup()


class ToolbarLayoutEditor(QWidget):
    def __init__(self, parent=None) -> None:
        from PySide6.QtWidgets import QCheckBox

        from ...core.settings import app_settings
        from ..reader import toolbar_layout as tl

        super().__init__(parent)
        self.tl = tl
        self.settings = app_settings()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.combo = QComboBox()
        for key, label in tl.LAYOUTS.items():
            self.combo.addItem(label, key)
        self.combo.setCurrentIndex(max(0, self.combo.findData(tl.current_layout_name())))
        self.combo.currentIndexChanged.connect(self._layout_chosen)
        setting_row(layout, "Layout", self.combo, "Default keeps the page and zoom at the top, tools on a rail at the right, and floats the page controls in full screen.")
        self.cb_rail = QCheckBox()
        self.cb_rail.setChecked(not self.settings.value(tl.RAIL_HIDDEN_KEY, False, type=bool))
        self.cb_rail.toggled.connect(self._rail_toggled)
        setting_row(layout, "Show the side rail", self.cb_rail, "Right-click the rail to hide it; its buttons move into the menus.")
        self.custom = QWidget()
        self.custom.setObjectName("settingRow")
        custom_layout = QVBoxLayout(self.custom)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.cb_fullscreen_pill = QCheckBox()
        self.cb_fullscreen_pill.toggled.connect(self._custom_changed)
        setting_row(
            custom_layout,
            "Floating page controls in full screen",
            self.cb_fullscreen_pill,
            "Changing this or a row below makes your own layout (Custom).",
        )
        self.table = QTableWidget(len(tl.COMMANDS), 2)
        self.table.setHorizontalHeaderLabels(["Command", "Shown in"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.place_combos = {}
        for row, command in enumerate(tl.COMMANDS):
            self.table.setItem(row, 0, QTableWidgetItem(command.label.rstrip("…")))
            combo = QComboBox()
            for place in tl.allowed_places(command.key):
                combo.addItem(tl.PLACE_LABELS[place], place)
            combo.currentIndexChanged.connect(self._custom_changed)
            self.table.setCellWidget(row, 1, combo)
            self.place_combos[command.key] = combo
        self.table.setMinimumHeight(320)
        custom_layout.addWidget(self.table)
        layout.addWidget(self.custom)
        self._show_layout(self.layout_name())

    def layout_name(self) -> str:
        return str(self.combo.currentData() or "default")

    def _show_layout(self, name: str) -> None:
        spec = self.tl.custom_layout() if name == "custom" else self.tl.resolve(name)
        self._loading = True
        for key, combo in self.place_combos.items():
            combo.setCurrentIndex(max(0, combo.findData(spec["places"].get(key))))
        self.cb_fullscreen_pill.setChecked(bool(spec.get("fullscreen_pill", False)))
        self._loading = False

    def _layout_chosen(self) -> None:
        name = self.layout_name()
        if name == "custom" and not self.settings.value(self.tl.CUSTOM_KEY, ""):
            previous = self.tl.resolve(self.tl.current_layout_name())
            self.tl.save_custom_layout(previous["places"], previous.get("fullscreen_pill", False))
        self._show_layout(name)
        self.tl.set_layout(name)

    def places(self) -> dict:
        return {key: combo.currentData() for key, combo in self.place_combos.items()}

    def _custom_changed(self, *_) -> None:
        if getattr(self, "_loading", False):
            return
        self.tl.save_custom_layout(self.places(), self.cb_fullscreen_pill.isChecked())
        if self.layout_name() != "custom":
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(self.combo.findData("custom"))
            self.combo.blockSignals(False)
        self.tl.set_layout("custom")

    def _rail_toggled(self, on: bool) -> None:
        self.settings.setValue(self.tl.RAIL_HIDDEN_KEY, not on)
        self.tl.notifier().changed.emit()

