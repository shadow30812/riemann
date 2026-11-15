"""
Where the reader's toolbar buttons go.

The reader creates its buttons once (ToolbarMixin._setup_toolbar_buttons) and
they stay the only source of truth: shortcuts, settings and other mixins keep
using btn_save, btn_notes and the rest whatever the layout. ToolbarLayout only
decides where each one is shown:

    bar     the row at the top of the reader
    rail    a vertical strip at the right edge (left of the Notes panel)
    ribbon  a second row that shows one group (View, Tools, Pages, File) at a time
    pill    a floating strip at the bottom centre of the page
    menu    a menu: the labelled group buttons (layout A), fly-outs at the foot
            of the rail (C), or the ⋯ button
    hidden  nowhere; shortcuts and the command palette still reach it

Layouts (setting LAYOUT_KEY): "A" groups buttons into labelled menus on one
bar; "B" is a bar plus a ribbon row; "C" is a short bar plus the side rail;
"D" is a short bar plus the floating pill. "default" is C plus D's floating
page controls in pure full screen; "custom" is a placement per command stored as JSON
under CUSTOM_KEY, seeded from the layout that was active when it was chosen.

Rows never scroll. When the top bar, ribbon or rail is too small for what is
placed on it, buttons fold into a ⋯ menu in a fixed order (FOLD) and come
back when there is room again. The page number and the sidebar button never
fold. Menu entries mirror the buttons when the menu opens (icon, checked,
enabled) and click the real button when chosen.

Layout changes from Settings reach every open reader through notifier().
The rail can be hidden from its context menu (RAIL_HIDDEN_KEY); its buttons
then go to the menus.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.settings import app_settings

LAYOUT_KEY = "reader/toolbar_layout"
CUSTOM_KEY = "reader/toolbar_custom"
RAIL_HIDDEN_KEY = "reader/toolbar_rail_hidden"
RIBBON_GROUP_KEY = "reader/toolbar_ribbon_group"

BAR, RAIL, RIBBON, PILL, MENU, HIDDEN = "bar", "rail", "ribbon", "pill", "menu", "hidden"
PLACE_LABELS = {BAR: "Top bar", RAIL: "Side rail", RIBBON: "Ribbon", PILL: "Floating", MENU: "Menu", HIDDEN: "Hidden"}
GROUPS = ["View", "Tools", "Pages", "File"]
GROUP_ICONS = {"View": "book-open.svg", "Tools": "pen-line.svg", "Pages": "file.svg", "File": "folder.svg"}
LAYOUTS = {
    "default": "Default: side rail, floating page controls in full screen",
    "A": "A: main bar with menus",
    "B": "B: two-row ribbon",
    "C": "C: side rail",
    "D": "D: floating page controls",
    "custom": "Custom",
}


@dataclass(frozen=True)
class Command:
    key: str
    widgets: tuple
    label: str
    short: str
    shortcut: str
    group: str
    fold: int


COMMANDS: List[Command] = [
    Command("sidebar", ("btn_toggle_sidebar",), "Sidebar", "Sidebar", "Ctrl+Alt+S", "View", 90),
    Command("back", ("btn_nav_back",), "Back", "Back", "Alt+Left", "Tools", 30),
    Command("forward", ("btn_nav_forward",), "Forward", "Forward", "Alt+Right", "Tools", 30),
    Command("page", ("btn_prev", "txt_page", "lbl_total", "btn_next"), "Page", "Page", "", "View", 100),
    Command("zoom", ("btn_zoom_out", "combo_zoom", "btn_zoom_in"), "Zoom", "Zoom", "", "View", 50),
    Command("search", ("btn_search",), "Find", "Find", "Ctrl+F", "Tools", 40),
    Command("ai_search", ("btn_ai_search",), "AI search", "AI search", "Ctrl+I", "Tools", 20),
    Command("annotate", ("btn_annotate",), "Annotate", "Annotate", "Ctrl+Shift+A", "Tools", 14),
    Command("notes", ("btn_notes",), "Notes", "Notes", "Ctrl+Alt+N", "Tools", 12),
    Command("read_aloud", ("btn_read_aloud",), "Read aloud", "Read aloud", "Ctrl+Shift+U", "Tools", 8),
    Command("snip", ("btn_snip",), "Snip maths to LaTeX", "Snip LaTeX", "", "Tools", 6),
    Command("cite", ("btn_cite",), "Copy citation", "Cite", "", "Tools", 6),
    Command("ocr", ("btn_ocr",), "Recognise text (OCR)", "OCR", "", "Tools", 6),
    Command("present", ("btn_present",), "Present", "Present", "Shift+F5", "Pages", 10),
    Command("pages", ("btn_pages",), "Edit pages…", "Edit pages", "", "Pages", 5),
    Command("rotate", ("btn_rotate",), "Rotate right", "Rotate right", "Ctrl+R", "Pages", 4),
    Command("rotate_ccw", ("btn_rotate_ccw",), "Rotate left", "Rotate left", "Ctrl+Shift+R", "Pages", 4),
    Command("reflow", ("btn_reflow",), "Reflow text", "Reflow", "", "View", 4),
    Command("facing", ("btn_facing",), "Facing pages", "Facing", "", "View", 4),
    Command("scroll_mode", ("btn_scroll_mode",), "Continuous scroll", "Continuous", "", "View", 4),
    Command("autoscroll_up", ("btn_autoscroll_up",), "Auto-scroll up", "Drift up", "", "View", 3),
    Command("autoscroll_down", ("btn_autoscroll_down",), "Auto-scroll down", "Drift down", "", "View", 3),
    Command("theme", ("btn_theme",), "Page colours", "Colours", "", "View", 4),
    Command("fullscreen", ("btn_fullscreen",), "Full screen", "Full screen", "F11", "View", 16),
    Command("save", ("btn_save",), "Save a copy", "Save", "Ctrl+S", "File", 4),
    Command("print", ("btn_print",), "Print…", "Print", "Ctrl+P", "File", 3),
    Command("open_with", ("btn_open_external",), "Open with…", "Open with", "", "File", 3),
    Command("export", ("btn_export",), "Export annotations…", "Export", "", "File", 3),
    Command("rename", ("btn_rename",), "Rename from metadata", "Rename", "", "File", 2),
    Command("sign", ("btn_sign",), "Sign…", "Sign", "", "File", 2),
    Command("secure_export", ("btn_secure_export",), "Password-protected copy…", "Protect", "Ctrl+Shift+S", "File", 2),
]
BY_KEY: Dict[str, Command] = {c.key: c for c in COMMANDS}
CLUSTERS = ("page", "zoom")
LEFT = ("sidebar", "back", "forward", "page", "zoom")
FIXED_ON_BAR = ("sidebar", "page")


def _places(bar=(), rail=(), ribbon=(), pill=(), rest=MENU) -> Dict[str, str]:
    out = {c.key: rest for c in COMMANDS}
    for place, keys in ((BAR, bar), (RAIL, rail), (RIBBON, ribbon), (PILL, pill)):
        for key in keys:
            out[key] = place
    return out


CORE = ("sidebar", "back", "forward", "page", "zoom", "search", "ai_search")
PRESETS: Dict[str, dict] = {
    "A": {"places": _places(bar=CORE + ("annotate", "notes", "present")), "menus": "groups", "fullscreen_pill": False},
    "B": {"places": _places(bar=CORE, rest=RIBBON), "menus": "more", "fullscreen_pill": False},
    "C": {
        "places": _places(bar=CORE, rail=("annotate", "notes", "read_aloud", "snip", "cite", "ocr", "present", "fullscreen")),
        "menus": "rail",
        "fullscreen_pill": False,
    },
    "D": {
        "places": _places(bar=("sidebar", "back", "forward", "search", "ai_search", "annotate", "notes", "present"), pill=("page", "zoom", "fullscreen")),
        "menus": "more",
        "fullscreen_pill": False,
    },
}
PRESETS["default"] = dict(PRESETS["C"], fullscreen_pill=True)


def allowed_places(key: str) -> List[str]:
    if key in FIXED_ON_BAR:
        return [BAR, PILL] if key == "page" else [BAR]
    if key in CLUSTERS:
        return [BAR, PILL, MENU, HIDDEN]
    return [BAR, RAIL, RIBBON, PILL, MENU, HIDDEN]


def current_layout_name() -> str:
    name = str(app_settings().value(LAYOUT_KEY, "default") or "default")
    return name if name in LAYOUTS else "default"


def custom_layout() -> dict:
    raw = app_settings().value(CUSTOM_KEY, "")
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    base = PRESETS["default"]
    places = dict(base["places"])
    for key, place in (data.get("places") or {}).items():
        if key in BY_KEY and place in allowed_places(key):
            places[key] = place
    return {"places": places, "menus": "auto", "fullscreen_pill": bool(data.get("fullscreen_pill", True))}


def save_custom_layout(places: Dict[str, str], fullscreen_pill: bool) -> None:
    clean = {k: v for k, v in places.items() if k in BY_KEY and v in allowed_places(k)}
    app_settings().setValue(CUSTOM_KEY, json.dumps({"places": clean, "fullscreen_pill": bool(fullscreen_pill)}))


def resolve(name: Optional[str] = None) -> dict:
    name = name or current_layout_name()
    if name == "custom":
        spec = custom_layout()
    else:
        spec = dict(PRESETS.get(name, PRESETS["default"]))
        spec["places"] = dict(spec["places"])
    if spec["menus"] == "auto":
        spec["menus"] = "rail" if RAIL in spec["places"].values() else "more"
    return spec


class _Notifier(QObject):
    changed = Signal()


_notifier: Optional[_Notifier] = None


def notifier() -> _Notifier:
    global _notifier
    if _notifier is None or not shiboken6.isValid(_notifier):
        _notifier = _Notifier()
    return _notifier


def set_layout(name: str) -> None:
    app_settings().setValue(LAYOUT_KEY, name if name in LAYOUTS else "default")
    notifier().changed.emit()


class OverflowBox(QWidget):
    """
    A row or column of items that folds the ones that do not fit into a ⋯
    menu, lowest fold value first. Items are (widget, commands, fold).
    """

    def __init__(self, layout_owner, vertical: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.owner = layout_owner
        self.vertical = vertical
        self.items: List[tuple] = []
        self.extra_menu: List[str] = []
        self.extra_actions = []
        box = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        box.setContentsMargins(*((5, 8, 5, 8) if vertical else (8, 0, 8, 0)))
        box.setSpacing(4 if vertical else 2)
        self.box: QBoxLayout = box
        self.more = QToolButton()
        self.more.setObjectName("moreButton")
        self.more.setText("⋯")
        self.more.setToolTip("More")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more)
        self.more_menu.aboutToShow.connect(self._fill_more)
        self.more.setMenu(self.more_menu)
        self.folded: List[tuple] = []
        self._pending = False

    def add(self, widget: QWidget, commands=(), fold: int = 0) -> None:
        self.box.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter if self.vertical else Qt.AlignmentFlag.AlignVCenter)
        self.items.append((widget, tuple(commands), fold))

    def add_stretch(self) -> None:
        self.box.addStretch(1)

    def finish(self) -> None:
        self.box.addWidget(self.more, 0, Qt.AlignmentFlag.AlignHCenter if self.vertical else Qt.AlignmentFlag.AlignVCenter)
        self.refit()

    def _length(self, widget: QWidget) -> int:
        hint = widget.sizeHint()
        return hint.height() if self.vertical else hint.width()

    def refit(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._refit_now)

    def _refit_now(self) -> None:
        self._pending = False
        if not shiboken6.isValid(self):
            return
        margins = self.box.contentsMargins()
        space = self.box.spacing()
        avail = (self.height() - margins.top() - margins.bottom()) if self.vertical else (
            self.width() - margins.left() - margins.right()
        )
        if avail <= 0:
            return
        needs_more = bool(self.extra_menu or self.extra_actions)
        more_len = self._length(self.more) + space
        total = sum(self._length(w) + space for w, _, _ in self.items)
        hidden = set()
        order = sorted(range(len(self.items)), key=lambda i: (self.items[i][2], -i))
        budget = avail - (more_len if needs_more else 0)
        for i in order:
            if total <= budget:
                break
            widget, commands, fold = self.items[i]
            if fold >= 100:
                continue
            if not needs_more:
                needs_more = True
                budget -= more_len
                if total <= budget:
                    break
            hidden.add(i)
            total -= self._length(widget) + space
        self.folded = [self.items[i] for i in sorted(hidden)]
        for i, (widget, _, _) in enumerate(self.items):
            widget.setVisible(i not in hidden)
        self.more.setVisible(needs_more)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refit()

    def _fill_more(self) -> None:
        menu = self.more_menu
        menu.clear()
        for widget, commands, _ in self.folded:
            target = menu
            if widget.objectName() in ("groupMenu", "railFlyout"):
                target = menu.addMenu(widget.text() or widget.toolTip())
            for key in commands:
                self.owner.add_command_action(target, key)
        if self.folded and (self.extra_menu or self.extra_actions):
            menu.addSeparator()
        groups: Dict[str, List[str]] = {}
        for key in self.extra_menu:
            groups.setdefault(BY_KEY[key].group, []).append(key)
        for group in GROUPS:
            if group in groups:
                sub = menu.addMenu(group)
                for key in groups[group]:
                    self.owner.add_command_action(sub, key)
        for text, slot in self.extra_actions:
            menu.addAction(text).triggered.connect(slot)


class _Watcher(QObject):
    def __init__(self, layout: "ToolbarLayout") -> None:
        super().__init__(layout.tab)
        self.layout = layout
        notifier().changed.connect(self.on_layout_changed)

    def on_layout_changed(self) -> None:
        if shiboken6.isValid(self.layout.tab):
            self.layout.apply()

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.Show, QEvent.Type.Hide) and obj is self.layout.tab.toolbar:
            self.layout.sync_visibility()
        elif kind == QEvent.Type.Resize:
            self.layout.place_pill()
        elif kind == QEvent.Type.MouseMove:
            self.layout.wake_pill()
        return False


class ToolbarLayout:
    PILL_IDLE_MS = 2500

    def __init__(self, tab) -> None:
        self.tab = tab
        self.parking = QWidget(tab)
        self.parking.hide()
        self.bar: Optional[OverflowBox] = None
        self.ribbon: Optional[QWidget] = None
        self.ribbon_box: Optional[OverflowBox] = None
        self.rail: Optional[QFrame] = None
        self.rail_box: Optional[OverflowBox] = None
        self.pill: Optional[QFrame] = None
        self.spec: dict = {}
        self.fullscreen = False
        self._pill_effect = None
        self._pill_anim = None
        self._pill_timer = QTimer(tab)
        self._pill_timer.setSingleShot(True)
        self._pill_timer.timeout.connect(self._fade_pill)
        self.watcher = _Watcher(self)
        tab.toolbar.installEventFilter(self.watcher)
        tab.stack.installEventFilter(self.watcher)
        tab.scroll.viewport().installEventFilter(self.watcher)
        for key in BY_KEY:
            for widget in self.widgets(key):
                widget.setProperty("riemannCommand", key)

    def widgets(self, key: str) -> List[QWidget]:
        return [getattr(self.tab, name) for name in BY_KEY[key].widgets if hasattr(self.tab, name)]

    def button(self, key: str):
        found = self.widgets(key)
        return found[0] if found else None

    def set_fullscreen(self, on: bool) -> None:
        if on != self.fullscreen:
            self.fullscreen = on
            if self.spec.get("fullscreen_pill"):
                self.apply()
            else:
                self.sync_visibility()

    def effective_places(self) -> Dict[str, str]:
        places = dict(self.spec["places"])
        if app_settings().value(RAIL_HIDDEN_KEY, False, type=bool):
            places = {k: (MENU if v == RAIL else v) for k, v in places.items()}
        if self.fullscreen and self.spec.get("fullscreen_pill"):
            for key in ("page", "zoom", "fullscreen"):
                places[key] = PILL
        return places

    def _park(self) -> None:
        for key in BY_KEY:
            for widget in self.widgets(key):
                widget.setParent(self.parking)
                widget.show()
                if isinstance(widget, QPushButton) and widget.property("riemannLabelled"):
                    widget.setText("")
                    widget.setProperty("riemannLabelled", False)
        for name in ("bar", "ribbon", "rail", "pill"):
            old = getattr(self, name)
            if old is not None and shiboken6.isValid(old):
                old.hide()
                old.setParent(None)
                old.deleteLater()
            setattr(self, name, None)
        self.ribbon_box = self.rail_box = None
        self._pill_effect = self._pill_anim = None

    def apply(self, name: Optional[str] = None) -> None:
        self.spec = resolve(name)
        self._park()
        places = self.effective_places()
        by_place: Dict[str, List[str]] = {}
        for c in COMMANDS:
            by_place.setdefault(places[c.key], []).append(c.key)
        menus = self.spec["menus"]
        if menus == "rail" and not by_place.get(RAIL):
            menus = "more"
        self._build_bar(by_place.get(BAR, []), by_place.get(MENU, []), menus)
        self._build_ribbon(by_place.get(RIBBON, []))
        self._build_rail(by_place.get(RAIL, []), by_place.get(MENU, []) if menus == "rail" else [])
        self._build_pill(by_place.get(PILL, []))
        self.tab.toolbar.setFixedHeight(50 + (44 if self.ribbon is not None else 0))
        self.restyle()
        self.sync_visibility()

    def _cluster(self, key: str) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for widget in self.widgets(key):
            row.addWidget(widget)
        return holder

    def _item(self, key: str) -> QWidget:
        if key in CLUSTERS:
            return self._cluster(key)
        widget = self.button(key)
        widget.show()
        return widget

    def _separator(self, vertical_line: bool = True) -> QFrame:
        line = QFrame()
        line.setObjectName("toolbarSeparator")
        line.setFixedSize(1, 22) if vertical_line else line.setFixedSize(24, 1)
        return line

    def _with_separator(self, widget: QWidget) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(widget)
        row.addWidget(self._separator())
        return holder

    def _build_bar(self, keys: List[str], menu_keys: List[str], menus: str) -> None:
        bar = OverflowBox(self)
        left = [k for k in LEFT if k in keys]
        right = [k for k in keys if k not in LEFT]
        for i, key in enumerate(left):
            widget = self._item(key)
            nxt = left[i + 1] if i + 1 < len(left) else None
            if nxt is not None and not (key == "back" and nxt == "forward"):
                widget = self._with_separator(widget)
            bar.add(widget, [key], BY_KEY[key].fold)
        bar.add_stretch()
        for key in right:
            bar.add(self._item(key), [key], BY_KEY[key].fold)
        if menus == "groups":
            grouped: Dict[str, List[str]] = {}
            for key in menu_keys:
                grouped.setdefault(BY_KEY[key].group, []).append(key)
            if grouped:
                bar.add(self._separator(), [], 25)
            for group in GROUPS:
                if group in grouped:
                    bar.add(self._group_button(group, grouped[group]), grouped[group], 25)
        elif menus == "more":
            bar.extra_menu = list(menu_keys)
        if self.spec.get("places") and app_settings().value(RAIL_HIDDEN_KEY, False, type=bool) and RAIL in self.spec["places"].values():
            bar.extra_actions = [("Show side rail", self.show_rail)]
        self.tab.toolbar.layout().addWidget(bar)
        bar.finish()
        self.bar = bar

    def _group_button(self, group: str, keys: List[str]) -> QToolButton:
        button = QToolButton()
        button.setObjectName("groupMenu")
        button.setText(group)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        menu.aboutToShow.connect(lambda m=menu, ks=list(keys): self._fill_menu(m, ks))
        button.setMenu(menu)
        return button

    def _fill_menu(self, menu: QMenu, keys: List[str]) -> None:
        menu.clear()
        for key in keys:
            self.add_command_action(menu, key)

    def add_command_action(self, menu: QMenu, key: str) -> Optional[QAction]:
        c = BY_KEY[key]
        if key == "zoom":
            for label, name in (("Zoom in", "btn_zoom_in"), ("Zoom out", "btn_zoom_out")):
                widget = getattr(self.tab, name, None)
                if widget is not None:
                    action = menu.addAction(widget.icon(), label)
                    action.triggered.connect(lambda _=False, w=widget: QTimer.singleShot(0, w.click))
            return None
        if key == "page":
            return None
        button = self.button(key)
        if button is None:
            return None
        text = f"{c.label}\t{c.shortcut}" if c.shortcut else c.label
        action = menu.addAction(button.icon(), text)
        action.setCheckable(button.isCheckable())
        action.setChecked(button.isChecked())
        action.setEnabled(button.isEnabled())
        action.triggered.connect(lambda _=False, b=button: QTimer.singleShot(0, b.click))
        return action

    def _build_ribbon(self, keys: List[str]) -> None:
        if not keys:
            return
        groups = [g for g in GROUPS if any(BY_KEY[k].group == g for k in keys)]
        chosen = str(app_settings().value(RIBBON_GROUP_KEY, groups[0]) or groups[0])
        if chosen not in groups:
            chosen = groups[0]
        ribbon = QWidget()
        ribbon.setObjectName("toolbarRibbon")
        row = QHBoxLayout(ribbon)
        row.setContentsMargins(10, 0, 0, 4)
        row.setSpacing(8)
        picker = QComboBox()
        picker.setObjectName("ribbonPicker")
        picker.addItems(groups)
        picker.setCurrentText(chosen)
        picker.setToolTip("Which group of tools the second row shows")
        picker.currentTextChanged.connect(self._choose_ribbon_group)
        row.addWidget(picker)
        box = OverflowBox(self)
        box.box.setContentsMargins(0, 0, 8, 0)
        for key in keys:
            if BY_KEY[key].group != chosen:
                continue
            widget = self._item(key)
            if isinstance(widget, QPushButton):
                widget.setText(" " + BY_KEY[key].short)
                widget.setProperty("riemannLabelled", True)
            box.add(widget, [key], BY_KEY[key].fold)
        box.add_stretch()
        row.addWidget(box, 1)
        self.tab.toolbar.layout().addWidget(ribbon)
        box.finish()
        self.ribbon, self.ribbon_box = ribbon, box

    def _choose_ribbon_group(self, group: str) -> None:
        app_settings().setValue(RIBBON_GROUP_KEY, group)
        QTimer.singleShot(0, self.apply)

    def _build_rail(self, keys: List[str], flyout_keys: List[str]) -> None:
        if not keys:
            return
        rail = QFrame()
        rail.setObjectName("toolbarRail")
        rail.setFixedWidth(48)
        rail.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        rail.customContextMenuRequested.connect(self._rail_menu)
        outer = QVBoxLayout(rail)
        outer.setContentsMargins(0, 0, 0, 0)
        box = OverflowBox(self, vertical=True)
        main = [k for k in keys if k != "fullscreen"]
        last_group = None
        for key in main:
            group = BY_KEY[key].group
            if last_group is not None and group != last_group:
                box.add(self._separator(False), [], 0)
            last_group = group
            box.add(self._item(key), [key], BY_KEY[key].fold)
        box.add_stretch()
        grouped: Dict[str, List[str]] = {}
        for key in flyout_keys:
            grouped.setdefault(BY_KEY[key].group, []).append(key)
        for group in GROUPS:
            if group in grouped:
                box.add(self._flyout(group, grouped[group]), grouped[group], 25)
        if "fullscreen" in keys:
            box.add(self._item("fullscreen"), ["fullscreen"], BY_KEY["fullscreen"].fold)
        outer.addWidget(box)
        content = self.tab.content_container.layout()
        notes = getattr(self.tab, "notes_panel", None)
        index = content.indexOf(notes) if notes is not None else -1
        content.insertWidget(index if index >= 0 else content.count(), rail)
        box.finish()
        self.rail, self.rail_box = rail, box

    def _flyout(self, group: str, keys: List[str]) -> QToolButton:
        button = QToolButton()
        button.setObjectName("railFlyout")
        button.setIcon(self.tab._get_icon(GROUP_ICONS[group]))
        button.setIconSize(QSize(20, 20))
        button.setToolTip(group)
        button.setProperty("riemannGroup", group)
        menu = QMenu(button)

        def pop() -> None:
            self._fill_menu(menu, keys)
            size = menu.sizeHint()
            menu.exec(button.mapToGlobal(QPoint(-size.width() - 6, 0)))

        button.clicked.connect(pop)
        return button

    def _rail_menu(self, pos) -> None:
        menu = QMenu(self.rail)
        menu.addAction("Hide side rail").triggered.connect(self.hide_rail)
        menu.exec(QCursor.pos())

    def hide_rail(self) -> None:
        app_settings().setValue(RAIL_HIDDEN_KEY, True)
        notifier().changed.emit()

    def show_rail(self) -> None:
        app_settings().setValue(RAIL_HIDDEN_KEY, False)
        notifier().changed.emit()

    def _build_pill(self, keys: List[str]) -> None:
        if not keys:
            return
        pill = QFrame(self.tab)
        pill.setObjectName("toolbarPill")
        row = QHBoxLayout(pill)
        row.setContentsMargins(12, 4, 12, 4)
        row.setSpacing(4)
        for i, key in enumerate(keys):
            if i:
                row.addWidget(self._separator())
            row.addWidget(self._item(key))
        self._pill_effect = QGraphicsOpacityEffect(pill)
        self._pill_effect.setOpacity(1.0)
        pill.setGraphicsEffect(self._pill_effect)
        self._pill_anim = QPropertyAnimation(self._pill_effect, b"opacity", pill)
        self._pill_anim.setDuration(400)
        self.pill = pill
        pill.adjustSize()

    def pill_wanted(self) -> bool:
        tab = self.tab
        if self.pill is None or getattr(tab, "is_preview", False) or not getattr(tab, "current_doc", None):
            return False
        return not tab.toolbar.isHidden() or self.fullscreen

    def sync_visibility(self) -> None:
        shown = not self.tab.toolbar.isHidden()
        if self.rail is not None:
            self.rail.setVisible(shown)
        if self.pill is not None:
            want = self.pill_wanted()
            self.pill.setVisible(want)
            if want:
                self.place_pill()
                self.wake_pill()

    def place_pill(self) -> None:
        pill = self.pill
        if pill is None or not shiboken6.isValid(pill):
            return
        pill.adjustSize()
        area = self.tab.stack.geometry()
        top_left = self.tab.stack.mapTo(self.tab, QPoint(0, 0))
        x = top_left.x() + (area.width() - pill.width()) // 2
        y = top_left.y() + area.height() - pill.height() - 24
        pill.move(max(0, x), max(0, y))
        pill.raise_()

    def wake_pill(self) -> None:
        if self.pill is None or not self.pill.isVisible() or self._pill_effect is None:
            return
        self._pill_anim.stop()
        self._pill_effect.setOpacity(1.0)
        self._pill_timer.start(self.PILL_IDLE_MS)

    def _fade_pill(self) -> None:
        pill = self.pill
        if pill is None or not shiboken6.isValid(pill) or self._pill_anim is None:
            return
        if pill.underMouse() or any(w.hasFocus() for w in pill.findChildren(QWidget)):
            self._pill_timer.start(self.PILL_IDLE_MS)
            return
        self._pill_anim.stop()
        self._pill_anim.setStartValue(self._pill_effect.opacity())
        self._pill_anim.setEndValue(0.25)
        self._pill_anim.start()

    def restyle(self) -> None:
        sheet = self.tab.toolbar.styleSheet()
        for frame in (self.rail, self.pill):
            if frame is not None:
                frame.setStyleSheet(sheet)
