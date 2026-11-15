"""
The per-site cookie dialog (opened from Settings, Privacy and data) and the
library search dialog.
"""

from PySide6.QtCore import (
    Qt,
    QUrl,
)
from PySide6.QtWidgets import (
    QMainWindow,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from ..core.log import log


class ManageCookiesDialog(QDialog):
    """
    A dialog for clearing cookies associated with specific individual websites.
    """

    def __init__(self, parent: "QMainWindow") -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Cookies for Individual Sites")
        self.resize(450, 500)
        self.parent_win = parent
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select a website to clear its cookies:"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        history = self.parent_win.history_manager.get_list("web")
        domains = set()
        for url in history:
            try:
                host = QUrl(url).host()
                if host:
                    domains.add(host)
            except Exception:
                log.debug("__init__ failed", exc_info=True)

        self.list_widget.addItems(sorted(list(domains)))

        btn_layout = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Cookies for Selected")
        self.btn_clear.clicked.connect(self.clear_selected)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)

        btn_layout.addWidget(self.btn_clear)
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

        self.store = self.parent_win.web_profile.cookieStore()
        self.cookies_to_delete = set()
        self.store.cookieAdded.connect(self._handle_cookie_added)

    def clear_selected(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Please select a website first.")
            return
        domain = item.text()
        self.cookies_to_delete.add(domain)
        self.store.loadAllCookies()
        QMessageBox.information(self, "Success", f"Cookies cleared for {domain}.")

    def _handle_cookie_added(self, cookie) -> None:
        domain = cookie.domain()
        for d in self.cookies_to_delete:
            if d in domain:
                self.store.deleteCookie(cookie)


class LibrarySearchDialog(QDialog):
    """
    A modal dialog for searching the local PDF library using keywords or tags.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Search Library")
        self.resize(800, 500)

        layout = QVBoxLayout(self)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Search by keyword, or use tags like author:Smith year:2024"
        )
        self.search_input.returnPressed.connect(self.execute_search)
        layout.addWidget(self.search_input)

        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(
            ["Title", "Authors", "Year", "Path"]
        )
        self.results_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.results_table.itemDoubleClicked.connect(self.open_selected_pdf)
        layout.addWidget(self.results_table)

    def execute_search(self):
        """
        Executes a query against the library manager and populates the results table.
        """
        query = self.search_input.text().strip()
        results = self.parent().library_manager.search_library(query)

        self.results_table.setRowCount(0)
        for row_idx, row_data in enumerate(results):
            self.results_table.insertRow(row_idx)
            self.results_table.setItem(
                row_idx, 0, QTableWidgetItem(row_data.get("title", ""))
            )
            self.results_table.setItem(
                row_idx, 1, QTableWidgetItem(row_data.get("authors", ""))
            )
            self.results_table.setItem(
                row_idx, 2, QTableWidgetItem(row_data.get("year", ""))
            )

            path_item = QTableWidgetItem(row_data.get("file_path", ""))
            path_item.setFlags(path_item.flags() ^ Qt.ItemFlag.ItemIsEditable)
            self.results_table.setItem(row_idx, 3, path_item)

    def open_selected_pdf(self, item):
        row = item.row()
        file_path = self.results_table.item(row, 3).text()

        if file_path:
            self.accept()
            self.parent().new_pdf_tab(file_path)
