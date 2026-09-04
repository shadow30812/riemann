import os

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


def get_dialog_directory(settings: QSettings) -> str:
    default_dir = settings.value("app/default_dir", "", type=str)
    if default_dir and os.path.exists(default_dir):
        return default_dir
    last_dir = settings.value("app/last_dir", "", type=str)
    if last_dir and os.path.exists(last_dir):
        return last_dir
    return os.path.expanduser("~")


class ManageFavoritesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Favorites")
        self.resize(500, 400)
        self.settings = QSettings("Riemann", "Favorites")

        layout = QVBoxLayout(self)

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
        favs = self.settings.value("folders", [], type=list)
        for f in favs:
            if os.path.exists(f):
                self.list_widget.addItem(f)

    def _add_folder(self):
        app_settings = QSettings("Riemann", "PDFReader")
        start_dir = get_dialog_directory(app_settings)
        path = QFileDialog.getExistingDirectory(
            self, "Select Folder to Favorite", start_dir
        )
        if path:
            favs = self.settings.value("folders", [], type=list)
            if path not in favs:
                favs.append(path)
                self.settings.setValue("folders", favs)
                self._load_favorites()

    def _remove_folder(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        path = item.text()
        favs = self.settings.value("folders", [], type=list)
        if path in favs:
            favs.remove(path)
            self.settings.setValue("folders", favs)
            self._load_favorites()
