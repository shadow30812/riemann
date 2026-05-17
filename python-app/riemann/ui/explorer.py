import os

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileSystemModel,
    QMenu,
    QMessageBox,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


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
