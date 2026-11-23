"""
The command palette (Ctrl+Shift+P): type a few letters of any command and
press Enter.

Commands are gathered each time the palette opens, so nothing has to be
registered by hand:

- every action in the window's menus (submenus filled on demand, such as
  Open Recent, are left out),
- every enabled button with a tooltip in the current tab's toolbar, named by
  the tooltip's first line ("Rotate pages 90°", "OCR Current Page"...),
  including reader buttons the toolbar layout has folded into a menu or
  hidden (they carry the "riemannCommand" property),
- the System pages and the sections of Settings.

Matching is a fuzzy subsequence search: the typed letters must appear in
order, and matches at the start of words or in a run score higher. Shortcuts
are taken from the "(Ctrl+X)" suffix the app's labels use, or from the
action, and shown as keycaps.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from .palette import tokens, ui_font
from .tooltip import COLOURS, KeyCap, parse_tip

SHORTCUT_SUFFIX = re.compile(r"\s*\(([^()]*(?:Ctrl|Alt|Shift|F\d)[^()]*)\)\s*$")


@dataclass
class Command:
    title: str
    group: str
    run: Callable[[], None]
    keys: tuple = ()


def _clean(text: str) -> tuple[str, tuple]:
    text = text.replace("&&", "\0").replace("&", "").replace("\0", "&").strip()
    title, keys, _ = parse_tip(text)
    if not keys:
        m = SHORTCUT_SUFFIX.search(title)
        if m:
            keys = tuple(re.split(r"\+(?=.)", m.group(1).split(",")[0].strip()))
            title = title[: m.start()].rstrip()
    return title.rstrip(".… "), tuple(keys)


def _child_menus(widget) -> list[QMenu]:
    return widget.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly)


def menu_commands(menubar) -> list[Command]:
    """
    Every enabled action in the menu bar's menus. Menus are found as children
    rather than through QAction.menu(): in PySide 6.10 the wrapper that call
    returns can take the menu's actions with it when it is garbage-collected.
    """
    out: list[Command] = []

    def walk(menu: QMenu, group: str) -> None:
        subs = {sub.menuAction(): sub for sub in _child_menus(menu)}
        for action in menu.actions():
            if action.isSeparator() or not action.isVisible():
                continue
            sub = subs.get(action)
            if sub is not None:
                if sub.actions():
                    walk(sub, f"{group} › {_clean(sub.title())[0]}")
                continue
            if not action.isEnabled() or not action.text().strip():
                continue
            title, keys = _clean(action.text())
            if not keys and not action.shortcut().isEmpty():
                keys = tuple(action.shortcut().toString().split("+"))
            out.append(Command(title, group, action.trigger, keys))

    order = {a: i for i, a in enumerate(menubar.actions())}
    menus = sorted(_child_menus(menubar), key=lambda m: order.get(m.menuAction(), 1_000))
    for menu in menus:
        if menu.menuAction() in order:
            walk(menu, _clean(menu.title())[0])
    return out


def toolbar_commands(tab: QWidget, group: str) -> list[Command]:
    out: list[Command] = []
    seen = set()
    for button in tab.findChildren(QAbstractButton):
        tip = button.toolTip().strip()
        placed = button.property("riemannCommand") is not None
        if not tip or not button.isEnabled() or not (placed or button.isVisibleTo(tab)):
            continue
        title, keys = _clean(tip.split("\n")[0])
        if title and title not in seen:
            seen.add(title)
            out.append(Command(title, group, button.click, keys))
    return out


def system_commands(window) -> list[Command]:
    from .system import PAGES

    out = []
    for page, cls in PAGES.items():
        out.append(Command(f"Open {cls.title}", "Riemann", lambda p=page: window.open_system_page(p)))
    sections = [
        ("appearance", "Appearance"),
        ("documents", "Documents"),
        ("browser", "Browser"),
        ("permissions", "Site permissions"),
        ("ai", "AI and OCR"),
        ("privacy", "Privacy and data"),
        ("about", "About"),
    ]
    for key, label in sections:
        out.append(Command(f"Settings: {label}", "Riemann", lambda k=key: window.open_system_page("settings", k)))
    return out


def gather(window) -> list[Command]:
    commands = menu_commands(window.menuBar())
    tab = window.tabs_main.currentWidget() if hasattr(window, "tabs_main") else None
    if tab is not None:
        group = "Document" if hasattr(tab, "current_doc") else "Page" if hasattr(tab, "web") else "Tab"
        commands += toolbar_commands(tab, group)
    commands += system_commands(window)
    return commands


def score(query: str, text: str) -> float:
    """How well query matches text as a subsequence; 0 when it does not."""
    q = query.lower().replace(" ", "")
    t = text.lower()
    if not q:
        return 1.0
    total, pos, run = 0.0, -1, 0
    for ch in q:
        nxt = t.find(ch, pos + 1)
        if nxt < 0:
            return 0.0
        run = run + 1 if nxt == pos + 1 else 0
        at_word = nxt == 0 or not t[nxt - 1].isalnum()
        total += 1.0 + run * 1.5 + (3.0 if at_word else 0.0) - min(nxt - pos - 1, 10) * 0.1
        pos = nxt
    return total + (5.0 if t.startswith(query.lower()) else 0.0)


class CommandRow(QWidget):
    def __init__(self, command: Command, dark: bool, parent=None) -> None:
        super().__init__(parent)
        t = tokens(dark)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(8)
        title = QLabel(command.title)
        title.setFont(ui_font(13))
        title.setStyleSheet(f"color: {t['text']}; background: transparent;")
        group = QLabel(command.group)
        group.setFont(ui_font(11))
        group.setStyleSheet(f"color: {t['faint']}; background: transparent;")
        row.addWidget(title, 1)
        row.addWidget(group)
        for key in command.keys:
            row.addWidget(KeyCap(key, COLOURS[dark]))


class CommandPalette(QFrame):
    """A floating search box over the window, listing matching commands."""

    MAX_ROWS = 12

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window_ref = window
        self.setObjectName("commandPalette")
        self.commands: list[Command] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command")
        self.input.setFont(ui_font(15))
        self.list = QListWidget()
        self.list.setObjectName("commandList")
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemActivated.connect(lambda _: self.run_current())
        self.list.itemClicked.connect(lambda _: self.run_current())
        layout.addWidget(self.input)
        layout.addWidget(self.list)
        self.input.textChanged.connect(self.refresh)
        self.input.installEventFilter(self)
        self.hide()

    def open(self) -> None:
        dark = bool(getattr(self.window_ref, "dark_mode", True))
        t = tokens(dark)
        self.setStyleSheet(
            f"""
            QFrame#commandPalette {{
                background: {t["surface"]};
                border: 1px solid {t["border_strong"]};
                border-radius: 12px;
            }}
            QListWidget#commandList {{ background: transparent; border: none; outline: none; }}
            QListWidget#commandList::item {{ border-radius: 7px; }}
            QListWidget#commandList::item:selected {{ background: {t["accent_soft"]}; }}
            """
        )
        self._dark = dark
        self.commands = gather(self.window_ref)
        self.input.clear()
        self.refresh("")
        width = min(640, self.window_ref.width() - 40)
        self.setFixedWidth(width)
        self.move((self.window_ref.width() - width) // 2, 70)
        self.show()
        self.raise_()
        self.input.setFocus()

    def matches(self, text: str) -> list[Command]:
        if not text.strip():
            return list(self.commands)
        scored = []
        for command in self.commands:
            s = max(score(text, command.title), score(text, f"{command.group} {command.title}") * 0.8)
            if s > 0:
                scored.append((s, command))
        scored.sort(key=lambda pair: -pair[0])
        return [c for _, c in scored]

    def refresh(self, text: str) -> None:
        self.list.clear()
        for command in self.matches(text)[:200]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, command)
            row = CommandRow(command, getattr(self, "_dark", True))
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if self.list.count():
            self.list.setCurrentRow(0)
        rows = min(self.MAX_ROWS, max(1, self.list.count()))
        height = self.list.sizeHintForRow(0) if self.list.count() else 30
        self.list.setFixedHeight(rows * max(30, height) + 4)
        self.adjustSize()

    def run_current(self) -> None:
        item = self.list.currentItem()
        self.hide()
        if item is not None:
            item.data(Qt.ItemDataRole.UserRole).run()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if key == Qt.Key.Key_Down else -1
                row = max(0, min(self.list.count() - 1, self.list.currentRow() + step))
                self.list.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run_current()
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        if obj is self.input and event.type() == QEvent.Type.FocusOut and not self.list.underMouse():
            self.hide()
        return False
