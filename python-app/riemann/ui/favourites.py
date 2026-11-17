import os

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)
from ..core.settings import (
    FAVOURITE_FOLDERS_KEY,
    app_settings,
    favourites_settings,
    get_dialog_directory,
)
from .palette import dialog_icon


class ManageFavoritesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Favorites")
        self.resize(500, 400)
        self.settings = favourites_settings()

        layout = QVBoxLayout(self)
        dialog_icon(self, "folder-cog")

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Add Folder")
        self.btn_remove = QPushButton("Remove Selected")
        self.btn_close = QPushButton("Close")

        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_remove)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)

        self.btn_add.clicked.connect(self._add_folder)
        self.btn_remove.clicked.connect(self._remove_folder)
        self.btn_close.clicked.connect(self.accept)

        self._load_favorites()

    def _load_favorites(self):
        self.list_widget.clear()
        favs = self.settings.value(FAVOURITE_FOLDERS_KEY, [], type=list)
        for f in favs:
            if os.path.exists(f):
                self.list_widget.addItem(f)

    def _add_folder(self):
        store = app_settings()
        start_dir = get_dialog_directory(store)
        path = QFileDialog.getExistingDirectory(
            self, "Select Folder to Favorite", start_dir
        )
        if path:
            favs = self.settings.value(FAVOURITE_FOLDERS_KEY, [], type=list)
            if path not in favs:
                favs.append(path)
                self.settings.setValue(FAVOURITE_FOLDERS_KEY, favs)
                self._load_favorites()

    def _remove_folder(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        path = item.text()
        favs = self.settings.value(FAVOURITE_FOLDERS_KEY, [], type=list)
        if path in favs:
            favs.remove(path)
            self.settings.setValue(FAVOURITE_FOLDERS_KEY, favs)
            self._load_favorites()
