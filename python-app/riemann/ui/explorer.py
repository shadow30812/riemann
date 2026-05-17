import os
import sys

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QSettings,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


def _get_icon_path(filename):
    """Helper to dynamically resolve icon paths for the dashboard."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "riemann", "assets", "icons", filename)
    else:
        base = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(base, "assets", "icons", filename)


class FileIconProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None, dark_mode=False):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self._cache = {}

    def set_dark_mode(self, dark_mode):
        self.dark_mode = dark_mode
        self.invalidate()

    def _get_icon(self, name):
        suffix = "-white.svg" if self.dark_mode else ".svg"
        key = name + suffix
        if key in self._cache:
            return self._cache[key]

        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(base_path, "assets", "icons", f"{name}{suffix}")

        if not os.path.exists(icon_path):
            icon_path = os.path.join(base_path, "assets", "icons", f"{name}.svg")

        icon = QIcon(icon_path)
        self._cache[key] = icon
        return icon

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            source_index = self.mapToSource(index)
            model = self.sourceModel()

            if model.isDir(source_index):
                return super().data(index, role)

            file_info = model.fileInfo(source_index)
            ext = file_info.suffix().lower()

            if ext in ["pdf", "md"]:
                return self._get_icon("pdf")
            elif ext in ["html", "css", "js"]:
                return self._get_icon("browser")
            else:
                return self._get_icon("circle-question-mark")

        return super().data(index, role)


class FileExplorerPanel(QWidget):
    file_single_clicked = Signal(str)
    file_double_clicked = Signal(str)

    def __init__(self, parent=None, dark_mode=False):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.model = QFileSystemModel()
        self.model.setRootPath("")
        self.model.setFilter(QDir.Filter.NoDotAndDotDot | QDir.Filter.AllEntries)

        self.proxy = FileIconProxyModel(self, dark_mode)
        self.proxy.setSourceModel(self.model)

        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setHeaderHidden(True)
        for i in range(1, 4):
            self.tree.setColumnHidden(i, True)

        self.tree.clicked.connect(self._on_clicked)
        self.tree.doubleClicked.connect(self._on_double_clicked)

        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

        self.layout.addWidget(self.tree)
        self.current_path = ""
        self.update_theme(dark_mode)

    def set_path(self, path: str):
        self.current_path = path
        idx = self.model.index(path)
        proxy_idx = self.proxy.mapFromSource(idx)
        self.tree.setRootIndex(proxy_idx)

    def update_theme(self, dark_mode: bool):
        self.dark_mode = dark_mode
        self.proxy.set_dark_mode(dark_mode)
        bg = "#1e1e1e" if dark_mode else "#ffffff"
        fg = "#d4d4d4" if dark_mode else "#111111"
        self.tree.setStyleSheet(
            f"QTreeView {{ background-color: {bg}; color: {fg}; border: none; outline: none; }} "
            f"QTreeView::item:hover {{ background-color: rgba(60, 140, 255, 0.2); }}"
        )

    def _on_clicked(self, index: QModelIndex):
        source_idx = self.proxy.mapToSource(index)
        if not self.model.isDir(source_idx):
            self.file_single_clicked.emit(self.model.filePath(source_idx))

    def _on_double_clicked(self, index: QModelIndex):
        source_idx = self.proxy.mapToSource(index)
        if not self.model.isDir(source_idx):
            self.file_double_clicked.emit(self.model.filePath(source_idx))

    def _on_context_menu(self, pos):
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return

        source_idx = self.proxy.mapToSource(index)
        if self.model.isDir(source_idx):
            path = self.model.filePath(source_idx)
            menu = QMenu(self)
            add_fav = menu.addAction("Add to Favorites")
            action = menu.exec(self.tree.viewport().mapToGlobal(pos))

            if action == add_fav:
                settings = QSettings("Riemann", "Favorites")
                favs = settings.value("folders", [], type=list)
                if path not in favs:
                    favs.append(path)
                    settings.setValue("folders", favs)
                    QMessageBox.information(
                        self, "Favorites", "Folder added to favorites."
                    )


class FolderHomeTab(QWidget):
    """A distinct, modern dashboard for managing Workspaces."""

    def __init__(self, parent=None, dark_mode=True):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.setup_ui()

    def setup_ui(self):
        bg = "#0B0C10" if self.dark_mode else "#F3F4F6"
        fg = "#FFFFFF" if self.dark_mode else "#111827"
        card_bg = "#1F2937" if self.dark_mode else "#FFFFFF"
        card_border = "#374151" if self.dark_mode else "#E5E7EB"
        hover_bg = "#374151" if self.dark_mode else "#F9FAFB"
        accent = "#3B82F6"
        text_mut = "#9CA3AF" if self.dark_mode else "#6B7280"

        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg}; color: {fg}; font-family: 'Segoe UI', system-ui, sans-serif; }}
            QScrollArea {{ border: none; background: transparent; }}
            #HeaderTitle {{ font-size: 36px; font-weight: 800; margin-bottom: 4px; }}
            #HeaderSub {{ font-size: 15px; color: {text_mut}; margin-bottom: 24px; }}
            #SectionTitle {{ font-size: 20px; font-weight: 600; color: {fg}; }}
            
            QPushButton#PrimaryAction {{
                background-color: {accent}; color: #FFFFFF; border: none; border-radius: 8px;
                padding: 14px 28px; font-size: 16px; font-weight: bold;
            }}
            QPushButton#PrimaryAction:hover {{ background-color: #2563EB; }}
            
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget#FavList::item {{
                background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 12px; margin: 8px;
                padding: 20px; color: {fg};
            }}
            QListWidget#FavList::item:hover {{ border-color: {accent}; background-color: {hover_bg}; }}
            
            QListWidget#RecentList::item {{
                background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 8px; margin-bottom: 8px;
                padding: 12px 16px; color: {fg};
            }}
            QListWidget#RecentList::item:hover {{ border-color: {accent}; background-color: {hover_bg}; }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(60, 50, 60, 50)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("Workspace Dashboard")
        header.setObjectName("HeaderTitle")
        sub = QLabel(
            "Select a directory to mount as your active coding or research environment."
        )
        sub.setObjectName("HeaderSub")

        btn_layout = QHBoxLayout()
        btn_open = QPushButton("Open Directory...")
        btn_open.setObjectName("PrimaryAction")
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setFixedWidth(220)
        btn_open.clicked.connect(lambda: self.window().open_folder())
        btn_layout.addWidget(btn_open)
        btn_layout.addStretch()

        fav_header = QHBoxLayout()
        fav_icon = QLabel()
        fav_icon.setPixmap(QIcon(_get_icon_path("star.png")).pixmap(24, 24))
        fav_lbl = QLabel("Pinned Workspaces")
        fav_lbl.setObjectName("SectionTitle")
        fav_header.addWidget(fav_icon)
        fav_header.addWidget(fav_lbl)
        fav_header.addStretch()

        self.list_favs = QListWidget()
        self.list_favs.setObjectName("FavList")
        self.list_favs.setViewMode(QListView.ViewMode.IconMode)
        self.list_favs.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_favs.setSpacing(10)
        self.list_favs.setIconSize(QSize(48, 48))
        self.list_favs.setMinimumHeight(180)
        self.list_favs.setMaximumHeight(220)
        self.list_favs.itemClicked.connect(self._on_item_clicked)

        rec_header = QHBoxLayout()
        rec_icon = QLabel()
        rec_icon.setPixmap(QIcon(_get_icon_path("history.png")).pixmap(24, 24))
        rec_lbl = QLabel("Recent Directories")
        rec_lbl.setObjectName("SectionTitle")
        rec_header.addWidget(rec_icon)
        rec_header.addWidget(rec_lbl)
        rec_header.addStretch()

        self.list_recents = QListWidget()
        self.list_recents.setObjectName("RecentList")
        self.list_recents.itemClicked.connect(self._on_item_clicked)

        content_layout.addWidget(header)
        content_layout.addWidget(sub)
        content_layout.addLayout(btn_layout)
        content_layout.addSpacing(40)
        content_layout.addLayout(fav_header)
        content_layout.addSpacing(10)
        content_layout.addWidget(self.list_favs)
        content_layout.addSpacing(20)
        content_layout.addLayout(rec_header)
        content_layout.addSpacing(10)
        content_layout.addWidget(self.list_recents)

        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def showEvent(self, event):
        super().showEvent(event)
        self._populate_lists()

    def _populate_lists(self):
        self.list_favs.clear()
        self.list_recents.clear()

        folder_icon = QIcon(
            _get_icon_path("folder-white.svg" if self.dark_mode else "folder.svg")
        )

        favs = QSettings("Riemann", "Favorites").value("folders", [], type=list)
        for f in favs:
            if os.path.exists(f):
                item = QListWidgetItem(folder_icon, f"\n{os.path.basename(f)}")
                item.setToolTip(f)
                item.setData(Qt.ItemDataRole.UserRole, f)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.list_favs.addItem(item)

        if hasattr(self.window(), "history_manager"):
            recents = self.window().history_manager.get_list("folder")
            for r in recents[:15]:
                if os.path.exists(r) and r not in favs:
                    item = QListWidgetItem(f"   {os.path.basename(r)}")
                    item.setToolTip(r)
                    item.setData(Qt.ItemDataRole.UserRole, r)
                    self.list_recents.addItem(item)

    def _on_item_clicked(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if os.path.exists(path):
            self.window().open_folder(path)
