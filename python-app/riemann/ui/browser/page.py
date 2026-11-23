"""
The web page of a browser tab: pop-ups, site permissions and leaving a page.

Clipboard access goes through the site permissions in riemann.core.permissions
(PermissionBar) instead of JavascriptCanAccessClipboard, which granted it to
every page.

Leaving a page with unsaved changes: a site that wants to warn first registers
a beforeunload handler, and Chromium then asks through javaScriptConfirm with
Qt's fixed LEAVE_PROMPT. WebPage shows that question as Leave or Stay, on the
tab it belongs to (brought to the front). Closing a tab, a window or the app
calls BrowserTab.confirm_leave first: it sends the page RequestClose, and
Chromium answers with windowCloseRequested (no handler, or Leave) or, through
WebPage, leave_refused (Stay). A page whose renderer never answers is let go
after LEAVE_WAIT_MS. Chromium asks only on pages the person has interacted
with, as in other browsers.

owning_tab imports BrowserTab when called: tab.py imports this module.
"""

import urllib.parse
from html import escape as html_escape
from typing import Optional

from PySide6.QtCore import QCoreApplication, QObject, Qt, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QWidget,
)

from ...core import permissions
from ...core.doctypes import document_kind
from ...core.settings import HOMEPAGE_LINKS_KEY, app_settings
from .interceptor import is_local_homepage, sanitize_homepage_links


DOWNLOAD_CLAIMED = "riemannDownloadClaimed"


CAPTIONS_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld


LEAVE_PROMPT = "Are you sure you want to leave this page? Changes that you made may not be saved."


LEAVE_WAIT_MS = 3000


def owning_tab(page: Optional[QObject]) -> Optional["BrowserTab"]:
    """The BrowserTab whose view shows page, or None for pop-ups and pages outside a tab."""
    from .tab import BrowserTab

    obj = page.parent() if page is not None else None
    while obj is not None and not isinstance(obj, BrowserTab):
        obj = obj.parent()
    return obj


def opens_after_download(path: str, mime_type: str = "") -> bool:
    """PDFs, e-books and Office files open in a tab once downloaded; code, text and pictures don't."""
    if mime_type == "application/pdf" or path.lower().endswith(".pdf"):
        return True
    return document_kind(path) in ("pdf", "ebook", "office")


def show_tab(tab: Optional[QWidget]) -> None:
    """Makes tab the current page of the tab widget holding it."""
    stack = tab.parentWidget() if tab is not None else None
    if isinstance(stack, QStackedWidget) and isinstance(stack.parentWidget(), QTabWidget):
        stack.parentWidget().setCurrentWidget(tab)


def ask_to_leave(parent: Optional[QWidget], site: str) -> bool:
    """The Leave or Stay question for a page with unsaved changes; True for Leave."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Leave page?")
    box.setText(f"Changes you made on {site} may not be saved." if site else "Changes you made on this page may not be saved.")
    box.setInformativeText("Leave the page anyway?")
    leave = box.addButton("Leave", QMessageBox.ButtonRole.AcceptRole)
    stay = box.addButton("Stay", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(stay)
    box.setEscapeButton(stay)
    box.exec()
    return box.clickedButton() is leave


class WebPage(QWebEnginePage):
    """
    Custom QWebEnginePage subclass implementing specific behaviors for
    window creation, JavaScript console logging, and custom scheme handling.
    """

    leave_refused = Signal()
    asking_to_leave = False

    def __init__(
        self, profile: QWebEngineProfile, parent: Optional[QObject] = None
    ) -> None:
        super().__init__(profile, parent)
        self._popups = []
        self.store = app_settings()
        self.site_permissions: Optional[permissions.SitePermissions] = None

    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEnginePage:
        """
        Handles background tabs and pop-ups (like Google sign-in) with a temporary view
        that shares this page's profile.
        """
        view = self.parent()
        main_win = view.window() if view else None

        if _type == QWebEnginePage.WebWindowType.WebBrowserBackgroundTab:
            if hasattr(main_win, "new_browser_tab"):
                new_tab = main_win.new_browser_tab(url="", background=True)
                return new_tab.web.page()

        elif _type == QWebEnginePage.WebWindowType.WebBrowserTab:
            if hasattr(main_win, "new_browser_tab"):
                new_tab = main_win.new_browser_tab(url="", background=False)
                return new_tab.web.page()

        popup_view = QWebEngineView()
        popup_view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup_view.resize(800, 600)

        self._popups.append(popup_view)
        popup_view.destroyed.connect(lambda: self._cleanup_popup(popup_view))

        page = WebPage(self.profile(), popup_view)
        page.windowCloseRequested.connect(popup_view.close)

        popup_view.setPage(page)
        popup_view.show()
        return page

    def _cleanup_popup(self, popup: QWebEngineView) -> None:
        if popup in self._popups:
            self._popups.remove(popup)

    def javaScriptConfirm(self, origin: QUrl, msg: str) -> bool:
        if msg != QCoreApplication.translate("QWebEnginePage", LEAVE_PROMPT):
            return super().javaScriptConfirm(origin, msg)
        tab = owning_tab(self)
        show_tab(tab)
        self.asking_to_leave = True
        try:
            leave = ask_to_leave(tab or QWebEngineView.forPage(self), origin.host())
        finally:
            self.asking_to_leave = False
        if not leave:
            self.leave_refused.emit()
        return leave

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        """Sends the page's console messages to standard output."""
        pass

    def acceptNavigationRequest(
        self, url: QUrl, _type: QWebEnginePage.NavigationType, isMainFrame: bool
    ) -> bool:
        """
        Handles Riemann's own URL schemes, such as data sent from internal pages.
        Returns False when Riemann handled the URL itself.
        """
        if url.host() == "riemann-save.local" or url.scheme() == "riemann-save":
            if url.scheme() == "riemann-save":
                payload = urllib.parse.unquote(url.toString().replace("riemann-save://", "", 1))
            else:
                query = url.query(QUrl.ComponentFormattingOption.FullyEncoded)
                payload = urllib.parse.unquote(query[5:]) if query.startswith("data=") else ""
            links = sanitize_homepage_links(payload) if is_local_homepage(self.url()) else None
            if links is not None:
                self.store.setValue(HOMEPAGE_LINKS_KEY, links)
            return False

        if isMainFrame and self.site_permissions is not None:
            apply_content_settings(self.settings(), self.site_permissions, url)
        return super().acceptNavigationRequest(url, _type, isMainFrame)


def apply_content_settings(settings: QWebEngineSettings, store, url: QUrl) -> None:
    """Sets the page attributes behind the content settings for url's site."""
    A = QWebEngineSettings.WebAttribute
    allowed = lambda key: store.get(url, key) == permissions.ALLOW
    settings.setAttribute(A.JavascriptCanOpenWindows, allowed("popups"))
    settings.setAttribute(A.PlaybackRequiresUserGesture, not allowed("autoplay"))
    settings.setAttribute(A.JavascriptEnabled, allowed("javascript"))
    settings.setAttribute(A.AutoLoadImages, allowed("images"))
    settings.setAttribute(A.AllowRunningInsecureContent, allowed("insecure_content"))


class PermissionBar(QFrame):
    """
    The "example.com wants to use your camera" bar shown under the address bar.
    Requests queue and are shown one at a time; "Remember" stores the answer
    for the site.
    """

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("permissionBar")
        self._queue: list = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.remember = QCheckBox("Remember for this site")
        self.remember.setChecked(True)
        self.btn_block = QPushButton("Block")
        self.btn_allow = QPushButton("Allow")
        self.btn_allow.setDefault(True)
        self.btn_manage = QToolButton()
        self.btn_manage.setText("Manage")
        self.btn_manage.setAutoRaise(True)
        self.btn_manage.setToolTip("Site permissions in Settings")
        self.btn_manage.clicked.connect(self._open_settings)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.btn_manage)
        layout.addWidget(self.remember)
        layout.addWidget(self.btn_block)
        layout.addWidget(self.btn_allow)
        self.btn_allow.clicked.connect(lambda: self._answer(True))
        self.btn_block.clicked.connect(lambda: self._answer(False))
        self.hide()

    def ask(self, permission, keys: list[str]) -> None:
        self._queue.append((permission, keys))
        if len(self._queue) == 1:
            self._show_current()

    def _show_current(self) -> None:
        while self._queue and not self._queue[0][0].isValid():
            self._queue.pop(0)
        if not self._queue:
            self.hide()
            return
        permission, keys = self._queue[0]
        site = permissions.site_of(permission.origin()) or permission.origin().toString()
        wants = " and ".join(permissions.KIND_BY_KEY[k].request for k in keys)
        self.label.setText(f"<b>{html_escape(site)}</b> wants to {html_escape(wants)}.")
        self.remember.setVisible(bool(permissions.site_of(permission.origin())))
        self.show()

    def _answer(self, allow: bool) -> None:
        if not self._queue:
            self.hide()
            return
        permission, keys = self._queue.pop(0)
        if permission.isValid():
            permission.grant() if allow else permission.deny()
        if self.remember.isChecked():
            for key in keys:
                self.store.set(permission.origin(), key, permissions.ALLOW if allow else permissions.BLOCK)
        self._show_current()

    def _open_settings(self) -> None:
        show = getattr(self.window(), "show_site_permissions", None)
        if show:
            show()

    def clear(self) -> None:
        for permission, _ in self._queue:
            if permission.isValid():
                permission.deny()
        self._queue.clear()
        self.hide()
