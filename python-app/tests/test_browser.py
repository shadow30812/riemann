"""
Notes:

dispose: The tab owns its profile and creates it before its view, so Qt's child
order would delete the profile first. Detach it and release it after the tab
(and its page) are gone.
"""

import http.server
import json
import threading
import time
import urllib.parse
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QUrl, Signal
from PySide6.QtGui import QFocusEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestInfo,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

from riemann.core.settings import AUTO_OPEN_DOWNLOADS_KEY, app_settings
from riemann.ui import threads
from riemann.ui.browser import (
    BrowserTab,
    RequestInterceptor,
    WebPage,
    YtDlpSettingsDialog,
    YtDlpStreamWorker,
    YtDlpWorker,
    js_runtimes,
)
from riemann.ui.browser.downloads import Downloads
from riemann.ui.browser.media import MediaControls
from riemann.ui.browser.stream import BUILT_IN_PLAYER, StreamLauncher, VideoPlayer, format_time
from riemann.ui.threads import track


@pytest.fixture
def app(qtbot):
    return qtbot


def _flush_deferred_deletes():
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()


class _WebEngineTracker:
    """Holds QtWebEngine objects created by a test and deletes them in order.

    QtWebEngine aborts the process if a QWebEngineProfile is destroyed while
    a page using it is still alive, so pages, views and tabs must go first.
    Garbage collection or Qt child order (BrowserTab parents its profile
    before its view) does not guarantee that.
    """

    def __init__(self):
        self._objects = []
        self._profiles = []

    def profile(self):
        profile = QWebEngineProfile()
        self._profiles.append(profile)
        return profile

    def add(self, obj):
        self._objects.append(obj)
        return obj

    def tab(self, **kwargs):
        return self.add(BrowserTab(**kwargs))

    def dispose(self):
        for obj in reversed(self._objects):
            if not shiboken6.isValid(obj):
                continue
            if isinstance(obj, BrowserTab):
                devtools = getattr(obj, "_devtools_window", None)
                if devtools is not None:
                    devtools.close()
                    devtools.deleteLater()
                    _flush_deferred_deletes()
                if obj.profile.parent() is obj:
                    obj.profile.setParent(None)
                    self._profiles.append(obj.profile)
            if isinstance(obj, QWidget):
                obj.close()
            obj.deleteLater()
            _flush_deferred_deletes()
        self._objects.clear()
        for profile in reversed(self._profiles):
            profile.deleteLater()
        _flush_deferred_deletes()
        self._profiles.clear()


@pytest.fixture
def webengine(qapp):
    tracker = _WebEngineTracker()
    yield tracker
    tracker.dispose()


@patch("yt_dlp.YoutubeDL")
def test_ytdlpworker_success(mock_ydl, qtbot):
    mock_inst = MagicMock()
    mock_ydl.return_value.__enter__.return_value = mock_inst
    worker = YtDlpWorker("http://fake.url", "/fake/dir")
    with qtbot.waitSignal(worker.finished, timeout=1000) as blocker:
        worker.run()
    assert blocker.args == [True, "Download complete!"]


@patch("yt_dlp.YoutubeDL")
def test_ytdlpworker_failure(mock_ydl, qtbot):
    mock_inst = MagicMock()
    mock_inst.download.side_effect = Exception("Network error")
    mock_ydl.return_value.__enter__.return_value = mock_inst
    worker = YtDlpWorker("http://fake.url", "/fake/dir")
    with qtbot.waitSignal(worker.finished, timeout=1000) as blocker:
        worker.run()
    assert blocker.args == [False, "Network error"]


@patch("yt_dlp.YoutubeDL")
def test_ytdlpworker_cancelled(mock_ydl, qtbot):
    worker = YtDlpWorker("http://fake.url", "/fake/dir")
    worker.is_cancelled = True
    with qtbot.waitSignal(worker.finished, timeout=1000) as blocker:
        worker.run()
    assert blocker.args == [False, "Download cancelled."]



def test_ytdlpworker_stop():
    worker = YtDlpWorker("http://fake.url", "/fake/dir")
    worker.stop()
    assert worker.is_cancelled is True



def test_webpage_createwindow_background(qtbot, webengine):
    profile = webengine.profile()
    main_win = webengine.add(QWidget())
    qtbot.addWidget(main_win)
    parent_view = QWebEngineView(main_win)

    mock_tab = MagicMock()
    mock_tab.web.page.return_value = webengine.add(QWebEnginePage(profile))
    main_win.new_browser_tab = MagicMock(return_value=mock_tab)

    page = webengine.add(WebPage(profile, parent_view))
    new_page = page.createWindow(QWebEnginePage.WebWindowType.WebBrowserBackgroundTab)

    main_win.new_browser_tab.assert_called_with(url="", background=True)
    assert new_page is not None


def test_webpage_createwindow_tab(qtbot, webengine):
    profile = webengine.profile()
    main_win = webengine.add(QWidget())
    qtbot.addWidget(main_win)
    parent_view = QWebEngineView(main_win)

    mock_tab = MagicMock()
    mock_tab.web.page.return_value = webengine.add(QWebEnginePage(profile))
    main_win.new_browser_tab = MagicMock(return_value=mock_tab)

    page = webengine.add(WebPage(profile, parent_view))
    new_page = page.createWindow(QWebEnginePage.WebWindowType.WebBrowserTab)

    main_win.new_browser_tab.assert_called_with(url="", background=False)
    assert new_page is not None


def test_webpage_createwindow_popup(qtbot, webengine):
    profile = webengine.profile()
    page = webengine.add(WebPage(profile))
    page.createWindow(QWebEnginePage.WebWindowType.WebDialog)
    assert len(page._popups) == 1

    popup_view = webengine.add(page._popups[0])
    page._cleanup_popup(popup_view)
    assert len(page._popups) == 0


def test_webpage_js_console(webengine):
    profile = webengine.profile()
    page = webengine.add(WebPage(profile))
    page.javaScriptConsoleMessage(
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel,
        "Test Message",
        10,
        "test.js",
    )


@patch("PySide6.QtCore.QSettings.setValue")
def test_homepage_saves_valid_links(mock_set_value, webengine):
    from riemann.ui import browser

    profile = webengine.profile()
    page = webengine.add(WebPage(profile))
    links = '[{"name": "Docs", "url": "https://docs.example"}]'
    url = QUrl("https://riemann-save.local/?data=" + urllib.parse.quote(links))
    with patch.object(WebPage, "url", return_value=QUrl.fromLocalFile(browser.HOMEPAGE_PATH)):
        result = page.acceptNavigationRequest(
            url, QWebEnginePage.NavigationType.NavigationTypeOther, False
        )

    assert result is False
    mock_set_value.assert_called_once_with("homepage_links", json.dumps(json.loads(links)))


@pytest.mark.parametrize(
    "page_url",
    ["https://evil.example/", "https://evil.example/homepage.html", "file:///tmp/homepage.html"],
)
@patch("PySide6.QtCore.QSettings.setValue")
def test_other_pages_cannot_save_homepage_links(mock_set_value, page_url, webengine):
    profile = webengine.profile()
    page = webengine.add(WebPage(profile))
    with patch.object(WebPage, "url", return_value=QUrl(page_url)):
        result = page.acceptNavigationRequest(
            QUrl('riemann-save://[{"name":"x","url":"https://x"}]'),
            QWebEnginePage.NavigationType.NavigationTypeTyped,
            True,
        )
    assert result is False
    mock_set_value.assert_not_called()


def test_sanitize_homepage_links_rejects_script():
    from riemann.ui.browser import sanitize_homepage_links

    assert sanitize_homepage_links("[]);fetch('file:///etc/passwd');//") is None
    assert sanitize_homepage_links('{"name": "x"}') is None
    cleaned = json.loads(sanitize_homepage_links('[{"name": "ok", "url": "https://a"}, {"name": 1}, "x"]'))
    assert cleaned == [{"name": "ok", "url": "https://a"}]


def test_request_interceptor_block():
    interceptor = RequestInterceptor()
    info = MagicMock(spec=QWebEngineUrlRequestInfo)
    info.requestUrl.return_value = QUrl("https://doubleclick.net/ad")
    info.firstPartyUrl.return_value = QUrl("https://news.example/")

    interceptor.interceptRequest(info)
    info.block.assert_called_once_with(True)


def test_request_interceptor_whatsapp():
    interceptor = RequestInterceptor()
    info = MagicMock(spec=QWebEngineUrlRequestInfo)
    info.requestUrl.return_value = QUrl("https://web.whatsapp.com/")
    info.firstPartyUrl.return_value = QUrl("https://web.whatsapp.com/")

    interceptor.interceptRequest(info)
    info.setHttpHeader.assert_called_with(b"User-Agent", interceptor.spoofed_ua)


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_init(mock_injector, qtbot, webengine):
    tab = webengine.tab(start_url="https://example.com")
    qtbot.addWidget(tab)
    assert tab.txt_url.placeholderText() == "Enter URL or Search..."


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_incognito_init(mock_injector, qtbot, webengine):
    tab = webengine.tab(incognito=True)
    qtbot.addWidget(tab)
    assert tab.txt_url.placeholderText() == "Incognito Mode"
    assert tab.incognito is True


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_focus_in(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.show()
    QCoreApplication.processEvents()
    event = QFocusEvent(QEvent.Type.FocusIn)
    with patch.object(tab.web, "setFocus") as mock_set_focus, patch.object(tab, "hasFocus", return_value=True):
        tab.focusInEvent(event)
        mock_set_focus.assert_not_called()
        QCoreApplication.processEvents()
        mock_set_focus.assert_called()


@patch("riemann.ui.browser.tab.ScriptInjector")
@patch("os.getlogin", return_value="TestUser")
def test_browser_tab_homepage_load(mock_login, mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.web.page().runJavaScript = MagicMock()
    from riemann.ui import browser

    with patch.object(tab.web, "url", return_value=QUrl("https://evil.example/homepage.html")):
        tab._on_homepage_load_finished(True)
        tab.web.page().runJavaScript.assert_not_called()
    with patch.object(tab.web, "url", return_value=QUrl.fromLocalFile(browser.HOMEPAGE_PATH)):
        tab._on_homepage_load_finished(True)
        tab.web.page().runJavaScript.assert_called_once()


def _fake_permission(kind, origin="https://meet.example"):
    from PySide6.QtWebEngineCore import QWebEnginePermission

    perm = MagicMock()
    perm.permissionType.return_value = getattr(QWebEnginePermission.PermissionType, kind)
    perm.origin.return_value = QUrl(origin)
    perm.isValid.return_value = True
    return perm


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_permission_requests_follow_site_settings(mock_injector, qtbot, webengine, tmp_path):
    from riemann.core.permissions import ALLOW, BLOCK, SitePermissions

    tab = webengine.tab()
    qtbot.addWidget(tab)
    store = SitePermissions(str(tmp_path / "perms.json"))
    tab.site_permissions = store
    tab.permission_bar.store = store

    store.set("https://meet.example", "camera", ALLOW)
    store.set("https://meet.example", "microphone", ALLOW)
    allowed = _fake_permission("MediaAudioVideoCapture")
    tab._on_permission_requested(allowed)
    allowed.grant.assert_called_once()

    store.set("https://meet.example", "microphone", BLOCK)
    denied = _fake_permission("MediaAudioVideoCapture")
    tab._on_permission_requested(denied)
    denied.deny.assert_called_once()
    denied.grant.assert_not_called()

    unknown = _fake_permission("Unsupported")
    tab._on_permission_requested(unknown)
    unknown.deny.assert_called_once()


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_clipboard_read_asks_and_remembers(mock_injector, qtbot, webengine, tmp_path):
    from riemann.core.permissions import ALLOW, SitePermissions

    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.show()
    store = SitePermissions(str(tmp_path / "perms.json"))
    tab.site_permissions = store
    tab.permission_bar.store = store

    first = _fake_permission("ClipboardReadWrite", "https://docs.example")
    second = _fake_permission("Geolocation", "https://maps.example")
    tab._on_permission_requested(first)
    tab._on_permission_requested(second)
    assert tab.permission_bar.isVisible()
    assert "docs.example" in tab.permission_bar.label.text()
    first.grant.assert_not_called()

    tab.permission_bar.btn_allow.click()
    first.grant.assert_called_once()
    assert store.get("https://docs.example", "clipboard") == ALLOW
    assert "maps.example" in tab.permission_bar.label.text()

    tab.permission_bar.remember.setChecked(False)
    tab.permission_bar.btn_block.click()
    second.deny.assert_called_once()
    assert store.get("https://maps.example", "location") == "ask"
    assert not tab.permission_bar.isVisible()


def test_content_settings_follow_the_site(qapp, tmp_path):
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings

    from riemann.core.permissions import ALLOW, BLOCK, SitePermissions
    from riemann.ui.browser import apply_content_settings

    store = SitePermissions(str(tmp_path / "perms.json"))
    store.set("https://video.example", "autoplay", BLOCK)
    store.set("https://video.example", "popups", ALLOW)
    profile = QWebEngineProfile()
    page = QWebEnginePage(profile)
    A = QWebEngineSettings.WebAttribute
    apply_content_settings(page.settings(), store, QUrl("https://video.example/watch"))
    assert page.settings().testAttribute(A.PlaybackRequiresUserGesture)
    assert page.settings().testAttribute(A.JavascriptCanOpenWindows)
    apply_content_settings(page.settings(), store, QUrl("https://news.example/"))
    assert not page.settings().testAttribute(A.PlaybackRequiresUserGesture)
    assert not page.settings().testAttribute(A.JavascriptCanOpenWindows)
    assert not page.settings().testAttribute(A.AllowRunningInsecureContent)
    del page
    del profile


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_devtools(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.open_devtools()
    assert hasattr(tab, "_devtools_window")
    assert tab._devtools_window.isVisible()


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_zoom(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.web.setZoomFactor = MagicMock()
    tab.web.zoomFactor = MagicMock(return_value=1.0)

    tab.modify_zoom(0.5)
    tab.web.setZoomFactor.assert_called_with(1.5)
    assert tab.btn_zoom.text() == "150%"

    tab.reset_zoom()
    tab.web.setZoomFactor.assert_called_with(1.0)
    assert tab.btn_zoom.text() == "100%"


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_search_toggle(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    assert tab.search_bar.isHidden() is True
    tab.toggle_search()
    assert tab.search_bar.isHidden() is False


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_navigate(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.web.load = MagicMock()

    tab.txt_url.setText("https://example.com")
    tab.navigate_to_url()
    tab.web.load.assert_called_with(QUrl("https://example.com"))


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_toast(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.show_toast("Hello World")
    assert tab.lbl_toast.isHidden() is False
    assert tab.lbl_toast.text() == "Hello World"


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_bookmark_toggle(mock_injector, qtbot, webengine):
    main_win = MagicMock()
    main_win.bookmarks_manager.is_bookmarked.return_value = False

    tab = webengine.tab()
    qtbot.addWidget(tab)
    with patch.object(tab, "window", return_value=main_win):
        tab.toggle_bookmark()
        main_win.bookmarks_manager.add.assert_called_once()


@patch("riemann.ui.browser.tab.ScriptInjector")
@patch("builtins.open", new_callable=mock_open, read_data="console.log('audio');")
@patch("os.path.exists", return_value=True)
def test_browser_tab_music_mode(mock_exists, mock_file, mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.web.page().runJavaScript = MagicMock()

    tab.btn_music.setChecked(True)
    tab.toggle_music_mode()
    tab.web.page().runJavaScript.assert_called_once()
    assert (
        "window.RiemannAudio.enable();" in tab.web.page().runJavaScript.call_args[0][0]
    )


def _bare_view(webengine):
    """A web view on its own profile, with no BrowserTab around it."""
    view = webengine.add(QWebEngineView())
    view.setPage(WebPage(webengine.profile(), view))
    return view


def test_media_controls_work_without_a_browser_tab(qtbot, webengine):
    view = _bare_view(webengine)
    media = MediaControls(view, parent=view)
    toasts, playing, scripts = [], [], []
    media.toast.connect(toasts.append)
    media.playing_changed.connect(playing.append)
    view.page().runJavaScript = lambda script, *args: scripts.append(script)

    assert media.set_music_mode(True) is True
    assert "window.RiemannAudio.enable();" in scripts[-1]
    assert toasts[-1] == "Music Mode ON"
    view.loadFinished.emit(True)
    assert _wait_until(lambda: len(scripts) == 2, 3000)
    assert media.set_music_mode(False) is False
    assert "window.RiemannAudio.disable();" in scripts[-1]
    view.loadFinished.emit(True)
    assert not _wait_until(lambda: len(scripts) > 3, 1500)

    view.page().recentlyAudibleChanged.emit(True)
    assert playing == ["playing"]
    view.page().recentlyAudibleChanged.emit(False)
    assert _wait_until(lambda: playing[-1:] == [None], 3000)


def test_downloads_work_without_a_browser_tab(qtbot, webengine, tmp_path):
    view = _bare_view(webengine)
    owner = QWidget()
    qtbot.addWidget(owner)
    downloads = Downloads(view, owner)
    toasts, saved = [], []
    downloads.toast.connect(toasts.append)
    downloads.pdf_saved.connect(saved.append)
    downloads.download_video()
    assert toasts == ["Invalid URL for download."]
    page = view.page()
    target = tmp_path / "page.pdf"
    with patch("riemann.ui.browser.downloads.QFileDialog.getSaveFileName", return_value=(str(target), "")), patch(
        "riemann.ui.browser.downloads.save_last_directory"
    ), patch.object(page, "printToPdf", lambda path: page.pdfPrintingFinished.emit(path, True)):
        downloads.print_to_pdf()
    assert toasts[-2:] == ["Rendering PDF...", "PDF saved successfully!"]
    assert saved == [str(target)]


def test_a_missing_engine_script_leaves_the_mode_off(qtbot, webengine, monkeypatch, tmp_path):
    view = _bare_view(webengine)
    media = MediaControls(view, parent=view)
    toasts = []
    media.toast.connect(toasts.append)
    monkeypatch.setattr("riemann.ui.browser.media.asset_path", lambda name: str(tmp_path / name))
    assert media.set_video_mode(True) is False
    assert not media.video_on
    assert toasts == ["Error: Missing video_engine.js"]


def _wait_until(predicate, timeout_ms=15000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def download_server():
    files = {
        "/report.pdf": (b"%PDF-1.4\n%%EOF\n", "application/pdf"),
        "/bundle.zip": (b"PK\x05\x06" + bytes(18), "application/zip"),
        "/book.epub": (b"PK\x03\x04" + bytes(26) + b"mimetypeapplication/epub+zip", "application/epub+zip"),
        "/script.py": (b"print('hi')\n", "text/x-python"),
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, mime = files[self.path]
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f"attachment; filename={self.path[1:]}")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _download(webengine, qtbot, tmp_path, url, tabs=1):
    """
    Downloads url from the first of tabs browser tabs sharing one profile, through the real
    QWebEngineDownloadRequest.
    """
    profile = webengine.profile()
    profile.setDownloadPath(str(tmp_path))
    opened, toasts, dialogs = [], [], []
    made = []
    for _ in range(tabs):
        tab = webengine.tab(profile=profile)
        qtbot.addWidget(tab)
        tab.downloads.toast.connect(toasts.append)
        made.append(tab)
    made[0].downloads.document_downloaded.connect(opened.append)
    target = tmp_path / url.rsplit("/", 1)[1]

    def save_dialog(*args, **kwargs):
        dialogs.append(args)
        return str(target), ""

    with patch("riemann.ui.browser.downloads.QFileDialog.getSaveFileName", side_effect=save_dialog), patch(
        "riemann.ui.browser.downloads.save_last_directory"
    ):
        made[0].web.setUrl(QUrl(url))
        assert _wait_until(lambda: "Download complete!" in toasts or "Download failed." in toasts)
    return target, opened, toasts, dialogs


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_download_completes_and_opens_the_pdf(mock_injector, qtbot, webengine, tmp_path, download_server):
    target, opened, toasts, _dialogs = _download(webengine, qtbot, tmp_path, download_server + "/report.pdf")
    assert "Download complete!" in toasts
    assert target.read_bytes().startswith(b"%PDF")
    assert opened == [str(target)]


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_one_download_asks_once_with_several_tabs_open(mock_injector, qtbot, webengine, tmp_path, download_server):
    _target, _opened, toasts, dialogs = _download(webengine, qtbot, tmp_path, download_server + "/report.pdf", tabs=3)
    assert len(dialogs) == 1
    assert toasts.count("Download complete!") == 1


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_finished_download_that_is_not_a_pdf_is_not_opened(mock_injector, qtbot, webengine, tmp_path, download_server):
    target, opened, toasts, _dialogs = _download(webengine, qtbot, tmp_path, download_server + "/bundle.zip")
    assert "Download complete!" in toasts
    assert target.exists()
    assert opened == []


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_a_downloaded_ebook_opens_and_source_code_does_not(mock_injector, qtbot, webengine, tmp_path, download_server):
    target, opened, *_ = _download(webengine, qtbot, tmp_path, download_server + "/book.epub")
    assert opened == [str(target)]
    target, opened, *_ = _download(webengine, qtbot, tmp_path, download_server + "/script.py")
    assert target.exists() and opened == []


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_downloads_stay_closed_when_the_setting_is_off(mock_injector, qtbot, webengine, tmp_path, download_server):
    app_settings().setValue(AUTO_OPEN_DOWNLOADS_KEY, False)
    try:
        _target, opened, toasts, _dialogs = _download(webengine, qtbot, tmp_path, download_server + "/report.pdf")
    finally:
        app_settings().remove(AUTO_OPEN_DOWNLOADS_KEY)
    assert "Download complete!" in toasts and opened == []


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_print_to_pdf_reports_success_and_failure_once(mock_injector, qtbot, webengine, tmp_path):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    toasts = []
    tab.downloads.toast.connect(toasts.append)
    page = tab.web.page()
    target = tmp_path / "page.pdf"
    with patch("riemann.ui.browser.downloads.QFileDialog.getSaveFileName", return_value=(str(target), "")), patch(
        "riemann.ui.browser.downloads.save_last_directory"
    ), patch.object(page, "printToPdf", lambda path: page.pdfPrintingFinished.emit(path, True)):
        tab.print_to_pdf()
    assert toasts[-1] == "PDF saved successfully!"
    toasts.clear()
    with patch("riemann.ui.browser.downloads.QFileDialog.getSaveFileName", return_value=(str(target), "")), patch(
        "riemann.ui.browser.downloads.save_last_directory"
    ), patch.object(page, "printToPdf", lambda path: page.pdfPrintingFinished.emit(path, False)):
        tab.print_to_pdf()
    assert toasts[-1] == "Failed to render PDF."


@patch("riemann.ui.browser.downloads.YtDlpSettingsDialog")
@patch("riemann.ui.browser.tab.ScriptInjector")
@patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value="/fake/dir")
@patch("riemann.ui.browser.downloads.YtDlpWorker")
def test_browser_tab_download_video(mock_worker, mock_dialog, mock_injector, mock_settings_dlg, qtbot, webengine):
    mock_settings_dlg.return_value.exec.return_value = 1
    mock_settings_dlg.return_value.get_options.return_value = {}
    tab = webengine.tab()
    qtbot.addWidget(tab)
    with patch.object(tab.web, "url", return_value=QUrl("http://youtube.com")):
        tab.btn_download.click()
        mock_worker.assert_called_once()
        mock_worker.return_value.start.assert_called_once()
    assert tab.btn_download.toolTip() == "Cancel Download"


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_cancel_video(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.downloads.dl_worker = MagicMock()
    tab.downloads.dl_worker.isRunning.return_value = True
    tab.downloads.video_active = True
    tab.btn_download.click()
    tab.downloads.dl_worker.stop.assert_called_once()


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_browser_tab_download_finished(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.downloads.video_active = True
    tab._show_video_download_running(True)
    tab.downloads._on_video_finished(True, "Done")
    assert tab.progress.value() == 100
    assert tab.btn_download.toolTip() == "Download Video via yt-dlp"
    assert not tab.downloads.video_active


def test_ytdlp_worker_downloads_no_solver_code_and_single_video():
    """YtDlpWorker never asks yt-dlp to download solver code, and sets noplaylist for a single video."""
    worker = YtDlpWorker("https://youtube.com/watch?v=123", "/tmp", {"playlist": False})
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_ydl.return_value.__enter__.return_value = mock_inst
        worker.run()
        assert mock_ydl.called
        opts = mock_ydl.call_args[0][0]
        assert "remote_components" not in opts
        assert opts.get("noplaylist") is True


def test_ytdlp_worker_playlist_options():
    """Item 8: YtDlpWorker configures playlist output template and flags."""
    worker = YtDlpWorker("https://youtube.com/playlist?list=PL123", "/tmp", {"playlist": True})
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_ydl.return_value.__enter__.return_value = mock_inst
        worker.run()
        opts = mock_ydl.call_args[0][0]
        assert opts.get("noplaylist") is False
        assert "playlist_index" in opts.get("outtmpl", "")


def test_ytdlp_stream_worker_downloads_no_solver_code():
    """YtDlpStreamWorker never asks yt-dlp to download solver code."""
    worker = YtDlpStreamWorker("https://youtube.com/watch?v=123")
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_inst.extract_info.return_value = {"url": "https://stream.url"}
        mock_ydl.return_value.__enter__.return_value = mock_inst
        worker.run()
        opts = mock_ydl.call_args[0][0]
        assert "remote_components" not in opts


def test_ytdlp_stream_worker_reads_a_cookie_file():
    worker = YtDlpStreamWorker("https://youtube.com/watch?v=123", cookie_file="/tmp/cookies.txt")
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.return_value = {"url": "https://stream.url"}
        worker.run()
    opts = mock_ydl.call_args[0][0]
    assert opts["cookiefile"] == "/tmp/cookies.txt"
    assert opts["js_runtimes"] == js_runtimes()
    assert "remote_components" not in opts


def test_a_stream_worker_leaves_the_tracked_threads_once_it_has_finished(qapp):
    found = []
    worker = YtDlpStreamWorker("https://youtube.com/watch?v=123")
    worker.found.connect(found.append)
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.return_value = {"url": "https://stream.url"}
        track(worker).start()
        assert worker.wait(10000)
    for _ in range(5):
        QCoreApplication.processEvents()
    assert found == ["https://stream.url"]
    assert worker not in threads._TRACKED


class _FakeStreamWorker(QObject):
    found = Signal(str)
    error = Signal(str)
    made = []

    def __init__(self, url, cookies_browser=None, cookie_file=None):
        super().__init__()
        self.url, self.cookies_browser, self.cookie_file = url, cookies_browser, cookie_file
        self.started = False
        _FakeStreamWorker.made.append(self)

    def start(self):
        self.started = True


def _launcher(qtbot, answers, monkeypatch):
    owner = QWidget()
    qtbot.addWidget(owner)
    launcher = StreamLauncher(owner)
    toasts, played = [], []
    launcher.toast.connect(toasts.append)
    launcher.play = played.append
    replies = iter(answers)
    monkeypatch.setattr("riemann.ui.browser.stream.QInputDialog.getItem", lambda *a, **k: next(replies))
    monkeypatch.setattr("riemann.ui.browser.stream.YtDlpStreamWorker", _FakeStreamWorker)
    monkeypatch.setattr("riemann.ui.browser.stream.track", lambda worker: worker)
    _FakeStreamWorker.made.clear()
    return launcher, toasts, played


def test_stream_launcher_works_without_a_browser_tab(qtbot, monkeypatch):
    launcher, toasts, played = _launcher(qtbot, [(BUILT_IN_PLAYER, True), ("Auto-extract: Firefox", True)], monkeypatch)
    launcher.stream("-https://example.com")
    assert toasts == ["Invalid or insecure URL for streaming."]
    launcher.stream("https://example.com/watch")
    (worker,) = _FakeStreamWorker.made
    assert worker.started and worker.cookies_browser == "firefox" and worker.cookie_file is None
    worker.found.emit("https://cdn.example.com/video.mp4")
    assert played == ["https://cdn.example.com/video.mp4"]


def test_vlc_that_fails_to_start_falls_back_to_the_built_in_player(qtbot, monkeypatch):
    launcher, toasts, played = _launcher(qtbot, [("VLC", True), ("No Cookies (Public Video)", True)], monkeypatch)

    def no_vlc(*args, **kwargs):
        raise OSError("vlc missing")

    monkeypatch.setattr("riemann.ui.browser.stream.subprocess.Popen", no_vlc)
    launcher.stream("https://example.com/watch")
    _FakeStreamWorker.made[0].found.emit("https://cdn.example.com/video.mp4")
    assert played == ["https://cdn.example.com/video.mp4"]
    assert "Failed to start VLC. Falling back to native player." in toasts


def test_a_second_stream_takes_over_the_first_window_s_controls(qtbot):
    owner = QWidget()
    qtbot.addWidget(owner)
    launcher = StreamLauncher(owner)
    first = launcher.play("")
    second = launcher.play("")
    assert first.player is second.player
    assert launcher.video_window is second


def test_video_player_shows_the_time(qtbot):
    player = VideoPlayer("")
    qtbot.addWidget(player)
    assert player.time_label.text() == "00:00 / 00:00"
    assert player.btn_rewind.toolTip() == "Back 10 seconds"
    assert player.btn_forward.toolTip() == "Forward 10 seconds"
    assert player.btn_play_pause.toolTip() == "Pause"
    player._on_playback_state_changed(QMediaPlayer.PlaybackState.PausedState)
    assert player.btn_play_pause.toolTip() == "Play"
    player._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert player.btn_play_pause.toolTip() == "Pause"
    assert format_time(61_000) == "01:01" and format_time(3_723_000) == "1:02:03"


def test_find_previous_in_a_browser_tab_searches_backwards():
    host = SimpleNamespace(web=MagicMock(), txt_find=SimpleNamespace(text=lambda: "query"))
    BrowserTab.find_prev(host)
    host.web.findText.assert_called_with("query", QWebEnginePage.FindFlag.FindBackward)


def test_ytdlp_settings_dialog_playlist_autodetection(qapp):
    """Item 8: Settings dialog reflects playlist autodetection."""
    dlg_single = YtDlpSettingsDialog(None, is_playlist=False)
    assert not dlg_single.chk_playlist.isChecked()

    dlg_pl = YtDlpSettingsDialog(None, is_playlist=True)
    assert dlg_pl.chk_playlist.isChecked()



def test_local_pages_may_only_load_static_files_from_their_folder(tmp_path):
    import os

    from PySide6.QtCore import QUrl
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInfo as Info

    from riemann.ui.browser import HOMEPAGE_PATH, local_load_allowed

    T = Info.ResourceType
    page = QUrl.fromLocalFile(str(tmp_path / "site" / "page.html"))
    own_css = QUrl.fromLocalFile(str(tmp_path / "site" / "assets" / "a.css"))
    other = QUrl.fromLocalFile(str(tmp_path / "secret.txt"))
    assert local_load_allowed(page, own_css, T.ResourceTypeStylesheet)
    assert local_load_allowed(page, own_css, T.ResourceTypeScript)
    assert not local_load_allowed(page, own_css, T.ResourceTypeXhr)
    assert not local_load_allowed(page, other, T.ResourceTypeImage)
    assert not local_load_allowed(page, other, T.ResourceTypeSubFrame)
    assert local_load_allowed(page, other, T.ResourceTypeMainFrame)
    assert not local_load_allowed(QUrl("https://evil.example/"), other, T.ResourceTypeImage)
    assert not local_load_allowed(page, QUrl("https://cdn.example/x.js"), T.ResourceTypeScript)
    assert not local_load_allowed(page, QUrl("https://cdn.example/x.png"), T.ResourceTypeImage)
    assert local_load_allowed(page, QUrl("https://example.com/"), T.ResourceTypeMainFrame)
    assert local_load_allowed(QUrl("https://site.example/"), QUrl("https://cdn.example/x.js"), T.ResourceTypeScript)

    home = QUrl.fromLocalFile(HOMEPAGE_PATH)
    font = QUrl.fromLocalFile(
        os.path.join(os.path.dirname(os.path.dirname(HOMEPAGE_PATH)), "fonts", "Inter-Variable.ttf")
    )
    assert local_load_allowed(home, font, T.ResourceTypeFontResource)
    assert not local_load_allowed(home, other, T.ResourceTypeXhr)
    icon = QUrl("https://www.google.com/s2/favicons?domain=example.com&sz=64")
    assert local_load_allowed(home, icon, T.ResourceTypeImage)
    assert not local_load_allowed(home, QUrl("https://evil.example/collect"), T.ResourceTypeXhr)
    assert not local_load_allowed(home, QUrl("https://evil.example/x.js"), T.ResourceTypeScript)


def test_tabs_on_one_profile_share_an_interceptor_that_outlives_them(webengine):
    from riemann.ui.browser import RequestInterceptor

    profile = webengine.profile()
    first = webengine.tab(start_url="about:blank", profile=profile)
    second = webengine.tab(start_url="about:blank", profile=profile)
    assert first.request_interceptor is second.request_interceptor

    second.close()
    second.deleteLater()
    _flush_deferred_deletes()
    interceptor = profile.findChild(RequestInterceptor)
    assert interceptor is not None and shiboken6.isValid(interceptor)
    assert interceptor is first.request_interceptor


def test_address_bar_search_encodes_the_query(webengine):
    tab = webengine.tab(start_url="about:blank", profile=webengine.profile())
    loaded = []
    with patch.object(tab.web, "load", side_effect=loaded.append):
        tab.txt_url.setText("fish & chips #recipes")
        tab.navigate_to_url()
    url = loaded[0]
    assert url.fragment() == ""
    assert url.toString(QUrl.ComponentFormattingOption.FullyEncoded) == "https://www.google.com/search?q=fish+%26+chips+%23recipes"


def test_whatsapp_user_agent_script_is_valid_javascript(webengine):
    from PySide6.QtQml import QJSEngine

    from riemann.ui.browser_handlers import WHATSAPP_NAVIGATOR_UA, ScriptInjector

    profile = webengine.profile()
    ScriptInjector(profile).inject_whatsapp_ua()
    source = next(s.sourceCode() for s in profile.scripts().toList() if s.name() == "RiemannWhatsAppUA")
    assert WHATSAPP_NAVIGATOR_UA in source
    result = QJSEngine().evaluate(f"(function () {{ {source} }})")
    assert not result.isError(), result.toString()


@pytest.mark.parametrize("url, blocked", [
    ("https://doubleclick.net/ad", True),
    ("https://stats.g.doubleclick.net/x", True),
    ("https://www.youtube.com/pagead/conversion", True),
    ("https://www.youtube.com/watch?v=pagead", False),
    ("https://loads.example.org/ads.example.org", False),
    ("https://notdoubleclick.net/", False),
    ("https://example.com/?ref=google-analytics.com", False),
])
def test_ad_blocker_matches_hosts_not_substrings(url, blocked):
    from riemann.ui.browser import BLOCKED_DOMAINS, is_blocked

    assert is_blocked(QUrl(url), BLOCKED_DOMAINS) is blocked


def test_blocked_requests_are_counted_without_touching_settings(qapp):
    interceptor = RequestInterceptor()
    info = MagicMock(spec=QWebEngineUrlRequestInfo)
    info.requestUrl.return_value = QUrl("https://doubleclick.net/ad")
    info.firstPartyUrl.return_value = QUrl("https://news.example/")
    with patch("riemann.ui.browser.interceptor.app_settings") as settings:
        for _ in range(3):
            interceptor.interceptRequest(info)
        settings.assert_not_called()
        settings.return_value.value.return_value = 10
        interceptor.flush_block_count()
        settings.return_value.setValue.assert_called_once_with("browser/ads_blocked", 13)


def test_cancelled_download_removes_only_its_own_partial_files(tmp_path):
    ours = tmp_path / "Talk.f137.mp4"
    for suffix in (".part", ".ytdl"):
        (tmp_path / (ours.name + suffix)).write_bytes(b"x")
    theirs = [tmp_path / "Other.mp4.part", tmp_path / "Other.mp4.ytdl"]
    for f in theirs:
        f.write_bytes(b"resume data")
    worker = YtDlpWorker("https://example.org/v", str(tmp_path))
    worker.progress_hook({"status": "downloading", "filename": str(ours), "tmpfilename": str(ours) + ".part"})
    worker.cleanup_remnants()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Other.mp4.part", "Other.mp4.ytdl"]


def _run_js(page, script, world):
    out = []
    page.runJavaScript(script, world, out.append)
    assert _wait_until(lambda: out, 5000)
    return out[0]


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_captions_token_is_hidden_from_page_scripts(mock_injector, qtbot, webengine, download_server):
    from PySide6.QtWebEngineCore import QWebEngineScript

    from riemann.ui.browser import CAPTIONS_WORLD

    tab = webengine.tab(profile=webengine.profile())
    qtbot.addWidget(tab)
    tab.show_toast = lambda *a: None
    loaded = []
    tab.web.page().loadFinished.connect(loaded.append)
    tab.web.setHtml("<html><body><p>page</p></body></html>", QUrl(download_server + "/"))
    assert _wait_until(lambda: loaded)
    tab.media.captions_server = MagicMock(port=4321, token="secret-token")
    tab.btn_captions.setChecked(True)
    tab.toggle_captions()
    main = QWebEngineScript.ScriptWorldId.MainWorld
    assert _run_js(tab.web.page(), "typeof window.RIEMANN_CAPTIONS_TOKEN", main) == "undefined"
    assert _run_js(tab.web.page(), "typeof window.RiemannCaptions", main) == "undefined"
    assert _run_js(tab.web.page(), "window.RIEMANN_CAPTIONS_TOKEN", CAPTIONS_WORLD) == "secret-token"
    assert _run_js(tab.web.page(), "document.body.innerText.trim().slice(0, 4)", CAPTIONS_WORLD) == "page"


def test_yt_dlp_runs_the_pinned_deno_from_the_venv(tmp_path, monkeypatch):
    import os

    from riemann.core import dependencies
    from riemann.ui.browser import ytdlp

    python = tmp_path / "bin" / "python"
    python.parent.mkdir()
    python.write_text("")
    monkeypatch.setattr(dependencies, "venv_python", lambda: str(python))
    assert ytdlp.js_runtimes() == {"deno": {}}
    deno = tmp_path / "bin" / ("deno.exe" if os.name == "nt" else "deno")
    deno.write_text("")
    assert ytdlp.js_runtimes() == {"deno": {"path": str(deno)}}
    monkeypatch.setattr(dependencies, "venv_python", lambda: "")
    assert ytdlp.js_runtimes() == {"deno": {}}


def test_yt_dlp_runs_deno_from_the_packages_folder_of_a_packaged_build(tmp_path, monkeypatch):
    import os

    from riemann.core import dependencies
    from riemann.ui.browser import ytdlp

    monkeypatch.setattr(dependencies, "venv_python", lambda: "")
    monkeypatch.setattr(dependencies, "_is_frozen", lambda: True)
    monkeypatch.setattr(dependencies, "packages_dir", lambda: str(tmp_path))
    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir()
    deno = scripts / ("deno.exe" if os.name == "nt" else "deno")
    deno.write_text("")
    assert ytdlp.js_runtimes() == {"deno": {"path": str(deno)}}


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL; script(params) plays what yt-dlp would do."""

    script = None

    def __init__(self, params):
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        type(self).script(self.params)
        return 0


def _run_with_fake_ytdlp(worker, script):
    _FakeYoutubeDL.script = staticmethod(script)
    results = []
    worker.finished.connect(lambda ok, msg: results.append((ok, msg)))
    with patch("yt_dlp.YoutubeDL", _FakeYoutubeDL):
        worker.run()
    return results


def test_ytdlp_failed_video_is_reported_not_called_complete(qapp):
    """With ignoreerrors, yt-dlp reports a failed video to the logger and returns 0."""

    def script(params):
        params["logger"].debug("[info] Writing video subtitles to: Talk.en.vtt")
        params["logger"].error("\x1b[0;31mERROR:\x1b[0m unable to download video data: HTTP Error 403: Forbidden")

    worker = YtDlpWorker("https://example.org/v", "/tmp")
    assert _run_with_fake_ytdlp(worker, script) == [
        (False, "Download failed: unable to download video data: HTTP Error 403: Forbidden")
    ]


def test_ytdlp_stop_ends_a_download_that_has_not_reached_progress(qapp):
    """Stop works while yt-dlp extracts or retries, when no progress hook runs."""
    lines = []

    def script(params):
        for i in range(50):
            params["logger"].debug(f"[youtube] step {i}")
            lines.append(i)
            if i == 2:
                worker.stop()

    worker = YtDlpWorker("https://example.org/v", "/tmp")
    assert _run_with_fake_ytdlp(worker, script) == [(False, "Download cancelled.")]
    assert len(lines) == 3


def test_ytdlp_subtitles_skip_machine_translations():
    import re as _re

    from riemann.ui.browser.ytdlp import SUBTITLE_LANGS

    def wanted(code):
        return any(_re.match(lang + "$", code) for lang in SUBTITLE_LANGS)

    assert all(wanted(c) for c in ("en", "en-US", "en-GB", "en-orig"))
    assert not any(wanted(c) for c in ("en-de", "en-fr", "de", "fr-en"))


@patch("riemann.ui.browser.tab.ScriptInjector")
def test_page_loading_line_stays_thin_under_the_theme_style_sheets(mock_injector, qtbot, webengine):
    """The themes give progress bars min-height: 8px, which made the 4px loading line 8px tall."""
    from riemann.core.assets import asset_path

    host = QWidget()
    qtbot.addWidget(host)
    with open(asset_path("theme", "modern_dark.css"), encoding="utf-8") as f:
        host.setStyleSheet(f.read())
    tab = webengine.tab()
    tab.setParent(host)
    host.show()
    QCoreApplication.processEvents()
    assert tab.progress.height() == 2
