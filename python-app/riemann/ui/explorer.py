"""
Notes:

__init__: Set by the per-tab theme toggle (N); None follows the interface
theme.
"""

import os

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .palette import FONT_STACK, GridCanvas, path_row, short_folder, tinted_icon, tinted_pixmap, tokens
from ..core.settings import FAVOURITE_FOLDERS_KEY, favourites_settings
from ..core.assets import icon_path
from ..core.doctypes import EBOOK_SUFFIXES, HTML_SUFFIXES, has_image_name, has_table_name, is_reader_name



class FileIconProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None, dark_mode=False):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self._cache = {}

    def set_dark_mode(self, dark_mode):
        self.dark_mode = dark_mode
        self.invalidate()

    def _get_icon(self, name):
        key = (name, self.dark_mode)
        if key not in self._cache:
            self._cache[key] = QIcon(icon_path(f"{name}.svg", self.dark_mode))
        return self._cache[key]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            source_index = self.mapToSource(index)
            model = self.sourceModel()

            if model.isDir(source_index):
                return super().data(index, role)

            name = model.fileInfo(source_index).fileName()

            if has_image_name(name):
                return self._get_icon("image")
            if has_table_name(name):
                return self._get_icon("file-text")
            if name.lower().endswith(EBOOK_SUFFIXES):
                return self._get_icon("book-open")
            if is_reader_name(name):
                return self._get_icon("pdf")
            elif name.lower().endswith(HTML_SUFFIXES):
                return self._get_icon("browser")
            else:
                return self._get_icon("circle-question-mark")

        return super().data(index, role)


class FileExplorerPanel(QWidget):
    file_single_clicked = Signal(str)
    file_double_clicked = Signal(str)
    collapse_requested = Signal()

    def __init__(self, parent=None, dark_mode=False):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.header = QWidget(self)
        self.header.setObjectName("ExplorerHeader")
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(10, 6, 8, 6)
        self.header_layout.setSpacing(6)

        self.lbl_folder_icon = QLabel(self.header)
        self.lbl_folder_icon.setFixedSize(16, 16)

        self.lbl_folder_title = QLabel("Explorer", self.header)

        self.btn_collapse = QToolButton(self.header)
        self.btn_collapse.setAutoRaise(True)
        self.btn_collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_collapse.setToolTip("Collapse the folder view (Ctrl+Shift+F)")
        self.btn_collapse.clicked.connect(self.collapse_requested.emit)

        self.header_layout.addWidget(self.lbl_folder_icon)
        self.header_layout.addWidget(self.lbl_folder_title, 1)
        self.header_layout.addWidget(self.btn_collapse)
        self.layout.addWidget(self.header)

        self.model = QFileSystemModel()
        self.model.setRootPath("")
        self.model.setFilter(QDir.Filter.NoDotAndDotDot | QDir.Filter.AllEntries)

        self.proxy = FileIconProxyModel(self, dark_mode)
        self.proxy.setSourceModel(self.model)

        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        for i in range(1, 4):
            self.tree.setColumnHidden(i, True)

        self.tree.clicked.connect(self._on_clicked)
        self.tree.doubleClicked.connect(self._on_double_clicked)

        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

        self.layout.addWidget(self.tree, 1)
        self.current_path = ""
        self.update_theme(dark_mode)

    def set_path(self, path: str):
        self.current_path = path
        self.model.setRootPath(path)
        idx = self.model.index(path)
        proxy_idx = self.proxy.mapFromSource(idx)
        self.tree.setRootIndex(proxy_idx)
        folder_name = os.path.basename(path) or path
        self.lbl_folder_title.setText(folder_name)
        self.lbl_folder_title.setToolTip(path)

    def update_theme(self, dark_mode: bool):
        self.dark_mode = dark_mode
        self.proxy.set_dark_mode(dark_mode)
        t = tokens(dark_mode)
        self.header.setStyleSheet(
            f"#ExplorerHeader {{ background-color: {t['surface']}; border-bottom: 1px solid {t['border']}; }}"
            f"#ExplorerHeader QLabel {{ color: {t['text']}; font-weight: 600; font-size: 12px; background: transparent; }}"
            f"#ExplorerHeader QToolButton {{ border: none; border-radius: 6px; padding: 2px; }}"
            f"#ExplorerHeader QToolButton:hover {{ background: {t['raised']}; }}"
        )
        self.lbl_folder_icon.setPixmap(tinted_pixmap("folder", t["accent"], 16))
        self.btn_collapse.setIcon(tinted_icon("chevron-left", t["muted"], 16))
        self.tree.setStyleSheet(
            f"QTreeView {{ background-color: {t['canvas']}; color: {t['text']}; border: none; outline: none; padding: 4px; }} "
            f"QTreeView::item {{ padding: 3px 2px; border-radius: 6px; }} "
            f"QTreeView::item:hover {{ background-color: {t['raised']}; }} "
            f"QTreeView::item:selected {{ background-color: {t['accent_soft']}; color: {t['text']}; }}"
        )

    def _on_clicked(self, index: QModelIndex):
        source_idx = self.proxy.mapToSource(index)
        if not self.model.isDir(source_idx):
            path = self.model.filePath(source_idx)
            if path:
                self.file_single_clicked.emit(path)

    def _on_double_clicked(self, index: QModelIndex):
        source_idx = self.proxy.mapToSource(index)
        if not self.model.isDir(source_idx):
            path = self.model.filePath(source_idx)
            if path:
                self.file_double_clicked.emit(path)

    def _on_context_menu(self, pos):
        from .components import build_open_with_menu

        index = self.tree.indexAt(pos)
        if index.isValid():
            source_idx = self.proxy.mapToSource(index)
            path = self.model.filePath(source_idx)
            is_dir = self.model.isDir(source_idx)
        else:
            path, is_dir = self.current_path, True
        if not path:
            return

        menu = QMenu(self)
        act_full = act_preview = add_fav = None
        if is_dir:
            add_fav = menu.addAction("Add to Favorites")
        else:
            act_full = menu.addAction("Open in Full Mode")
            act_preview = menu.addAction("Open in Preview Mode")
        menu.addSeparator()
        menu.addMenu(build_open_with_menu(menu, path, "Open in External Application ↗"))
        action = menu.exec(self.tree.viewport().mapToGlobal(pos))

        if action is None:
            return
        if action == act_full:
            self.file_double_clicked.emit(path)
        elif action == act_preview:
            self.file_single_clicked.emit(path)
        elif action == add_fav:
            settings = favourites_settings()
            favs = settings.value(FAVOURITE_FOLDERS_KEY, [], type=list)
            if path not in favs:
                favs.append(path)
                settings.setValue(FAVOURITE_FOLDERS_KEY, favs)
                QMessageBox.information(self, "Favorites", "Folder added to favorites.")


class FolderHomeTab(QWidget):
    """The Workspaces dashboard: favourite and recent folders."""

    def __init__(self, parent=None, dark_mode=True):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self._dark_override = None
        self.setup_ui()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.canvas = GridCanvas(self.dark_mode)
        root.addWidget(self.canvas)
        canvas_layout = QVBoxLayout(self.canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("dashContent")
        row = QHBoxLayout(content)
        row.setContentsMargins(48, 44, 48, 44)
        column = QWidget()
        column.setMaximumWidth(1080)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        row.addWidget(column, 30)
        row.addStretch(1)

        header = QLabel("Workspace Dashboard")
        header.setObjectName("HeaderTitle")
        sub = QLabel(
            "Select a directory to mount as your active coding or research environment."
        )
        sub.setObjectName("HeaderSub")
        sub.setWordWrap(True)

        self.btn_open = QPushButton("  Open Directory...")
        self.btn_open.setObjectName("PrimaryAction")
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.clicked.connect(lambda: self.window().open_folder())
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.btn_open)
        btn_layout.addStretch()

        fav_lbl = QLabel("Favourite Workspaces")
        fav_lbl.setObjectName("SectionTitle")

        self.list_favs = QListWidget()
        self.list_favs.setObjectName("FavList")
        self.list_favs.setViewMode(QListView.ViewMode.IconMode)
        self.list_favs.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_favs.setMovement(QListView.Movement.Static)
        self.list_favs.setGridSize(QSize(184, 130))
        self.list_favs.setIconSize(QSize(34, 34))
        self.list_favs.setUniformItemSizes(True)
        self.list_favs.setWordWrap(False)
        self.list_favs.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list_favs.setFixedHeight(150)
        self.list_favs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_favs.itemClicked.connect(self._on_item_clicked)
        self.lbl_favs_empty = QLabel(
            "No favourites yet. Right-click a folder in the file tree and choose Add to Favorites."
        )
        self.lbl_favs_empty.setObjectName("EmptyNote")
        self.lbl_favs_empty.setWordWrap(True)

        rec_lbl = QLabel("Recent Directories")
        rec_lbl.setObjectName("SectionTitle")
        recent_card = QFrame()
        recent_card.setObjectName("DashCard")
        card_layout = QVBoxLayout(recent_card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        self.list_recents = QListWidget()
        self.list_recents.setObjectName("RecentList")
        self.list_recents.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_recents.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_recents.itemClicked.connect(self._on_item_clicked)
        self.lbl_recents_empty = QLabel("Folders you open will show up here.")
        self.lbl_recents_empty.setObjectName("EmptyNote")
        self.lbl_recents_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.list_recents)
        card_layout.addWidget(self.lbl_recents_empty)

        layout.addWidget(header)
        layout.addSpacing(6)
        layout.addWidget(sub)
        layout.addSpacing(24)
        layout.addLayout(btn_layout)
        layout.addSpacing(40)
        layout.addWidget(fav_lbl)
        layout.addSpacing(12)
        layout.addWidget(self.list_favs)
        layout.addWidget(self.lbl_favs_empty)
        layout.addSpacing(32)
        layout.addWidget(rec_lbl)
        layout.addSpacing(12)
        layout.addWidget(recent_card)
        layout.addStretch(1)

        scroll.setWidget(content)
        canvas_layout.addWidget(scroll)
        self._apply_style()

    def _apply_style(self):
        t = tokens(self.dark_mode)
        self.canvas.set_dark(self.dark_mode)
        self.setStyleSheet(f"""
            QWidget {{ background: transparent; color: {t["text"]}; font-family: {FONT_STACK}; }}
            QScrollArea, #dashContent {{ border: none; background: transparent; }}
            #HeaderTitle {{ font-size: 32px; font-weight: 600; color: {t["text"]}; }}
            #HeaderSub {{ font-size: 15px; color: {t["muted"]}; }}
            #SectionTitle {{ font-size: 15px; font-weight: 600; color: {t["text"]}; }}
            #EmptyNote {{ font-size: 14px; color: {t["faint"]}; padding: 18px 4px; }}
            #DashCard {{ background: {t["surface"]}; border: 1px solid {t["border"]}; border-radius: 16px; }}

            QPushButton#PrimaryAction {{
                background-color: {t["accent"]}; color: {t["accent_text"]}; border: none; border-radius: 10px;
                padding: 11px 22px; font-size: 14px; font-weight: 600;
            }}
            QPushButton#PrimaryAction:hover {{ background-color: {t["accent_hover"]}; }}

            QListWidget {{ background: transparent; border: none; outline: none; padding: 0; }}
            QListWidget#FavList::item {{
                background-color: {t["surface"]}; border: 1px solid {t["border"]}; border-radius: 14px;
                margin: 0 12px 12px 0; padding: 18px 10px 12px 10px; color: {t["text"]};
            }}
            QListWidget#FavList::item:hover {{ border-color: {t["accent"]}; background-color: {t["raised"]}; }}
            QListWidget#FavList::item:selected {{ border-color: {t["accent"]}; background-color: {t["accent_soft"]}; color: {t["text"]}; }}

            QListWidget#RecentList::item {{ border: none; border-radius: 10px; margin: 1px 0; padding: 0; }}
            QListWidget#RecentList::item:hover {{ background-color: {t["raised"]}; }}
            QListWidget#RecentList::item:selected {{ background-color: {t["accent_soft"]}; }}
            #recentName {{ font-size: 14px; color: {t["text"]}; }}
            #recentDir {{ font-size: 12px; color: {t["faint"]}; }}
            QScrollBar:vertical {{ background: transparent; width: 14px; }}
            QScrollBar::handle:vertical {{ background: {t["border_strong"]}; border-radius: 5px; margin: 2px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self.btn_open.setIcon(tinted_icon("folder", t["accent_text"], 18))

    def apply_ui_theme(self, dark: bool):
        """Called by the main window when the interface theme changes."""
        self._dark_override = None
        if dark != self.dark_mode:
            self.dark_mode = dark
            self._apply_style()
            self._populate_lists()

    def toggle_theme(self):
        """The per-tab theme toggle (N): switches this page between light and dark."""
        self._dark_override = not self.dark_mode
        self.dark_mode = self._dark_override
        self._apply_style()
        self._populate_lists()

    def showEvent(self, event):
        super().showEvent(event)
        dark = self._dark_override
        if dark is None:
            dark = getattr(self.window(), "dark_mode", self.dark_mode)
        if dark != self.dark_mode:
            self.dark_mode = dark
            self._apply_style()
        self._populate_lists()

    def _populate_lists(self):
        self.list_favs.clear()
        self.list_recents.clear()
        t = tokens(self.dark_mode)
        fav_icon = tinted_icon("folder", t["accent"], 34)
        row_icon = tinted_pixmap("folder", t["muted"], 18)
        favs = favourites_settings().value(FAVOURITE_FOLDERS_KEY, [], type=list)
        for f in favs:
            if os.path.exists(f):
                item = QListWidgetItem(fav_icon, os.path.basename(f.rstrip(os.sep)) or f)
                item.setToolTip(f)
                item.setData(Qt.ItemDataRole.UserRole, f)
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
                item.setSizeHint(QSize(172, 118))
                self.list_favs.addItem(item)

        if hasattr(self.window(), "history_manager"):
            recents = self.window().history_manager.get_list("folder")
            for r in recents[:15]:
                if os.path.exists(r) and r not in favs:
                    item = QListWidgetItem()
                    item.setToolTip(r)
                    item.setData(Qt.ItemDataRole.UserRole, r)
                    item.setSizeHint(QSize(0, 54))
                    self.list_recents.addItem(item)
                    self.list_recents.setItemWidget(
                        item,
                        path_row(row_icon, os.path.basename(r.rstrip(os.sep)) or r, short_folder(os.path.dirname(r))),
                    )

        has_favs = self.list_favs.count() > 0
        self.list_favs.setVisible(has_favs)
        self.lbl_favs_empty.setVisible(not has_favs)
        n = self.list_recents.count()
        self.list_recents.setVisible(n > 0)
        self.list_recents.setFixedHeight(n * 56 + 2)
        self.lbl_recents_empty.setVisible(n == 0)

    def _on_item_clicked(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if os.path.exists(path):
            self.window().open_folder(path)

