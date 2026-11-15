"""
Notes:

dispose: The tab owns its profile and creates it before its view, so Qt's child
order would delete the profile first. Detach it and release it after the tab
(and its page) are gone.
"""

import json
import urllib.parse
from unittest.mock import MagicMock, mock_open, patch

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QFocusEvent
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestInfo,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget
from riemann.ui.browser import (
    BrowserTab,
    RequestInterceptor,
    WebPage,
    YtDlpWorker,
    YtDlpStreamWorker,
    YtDlpSettingsDialog,
)


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


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_init(mock_injector, qtbot, webengine):
    tab = webengine.tab(start_url="https://example.com")
    qtbot.addWidget(tab)
    assert tab.txt_url.placeholderText() == "Enter URL or Search..."


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_incognito_init(mock_injector, qtbot, webengine):
    tab = webengine.tab(incognito=True)
    qtbot.addWidget(tab)
    assert tab.txt_url.placeholderText() == "Incognito Mode"
    assert tab.incognito is True


@patch("riemann.ui.browser.ScriptInjector")
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


@patch("riemann.ui.browser.ScriptInjector")
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


@patch("riemann.ui.browser.ScriptInjector")
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


@patch("riemann.ui.browser.ScriptInjector")
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


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_devtools(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.open_devtools()
    assert hasattr(tab, "_devtools_window")
    assert tab._devtools_window.isVisible()


@patch("riemann.ui.browser.ScriptInjector")
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


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_search_toggle(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    assert tab.search_bar.isHidden() is True
    tab.toggle_search()
    assert tab.search_bar.isHidden() is False


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_navigate(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.web.load = MagicMock()

    tab.txt_url.setText("https://example.com")
    tab.navigate_to_url()
    tab.web.load.assert_called_with(QUrl("https://example.com"))


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_toast(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.show_toast("Hello World")
    assert tab.lbl_toast.isHidden() is False
    assert tab.lbl_toast.text() == "Hello World"


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_bookmark_toggle(mock_injector, qtbot, webengine):
    main_win = MagicMock()
    main_win.bookmarks_manager.is_bookmarked.return_value = False

    tab = webengine.tab()
    qtbot.addWidget(tab)
    with patch.object(tab, "window", return_value=main_win):
        tab.toggle_bookmark()
        main_win.bookmarks_manager.add.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
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


@patch("riemann.ui.browser.ScriptInjector")
@patch(
    "PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("/fake/path.pdf", "")
)
def test_browser_tab_download_handler(mock_dialog, mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    mock_item = MagicMock()

    tab._handle_download(mock_item)
    mock_item.setDownloadDirectory.assert_called_with("/fake")
    mock_item.setDownloadFileName.assert_called_with("path.pdf")
    mock_item.accept.assert_called_once()


@patch("riemann.ui.browser.YtDlpSettingsDialog")
@patch("riemann.ui.browser.ScriptInjector")
@patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value="/fake/dir")
@patch("riemann.ui.browser.YtDlpWorker")
def test_browser_tab_download_video(mock_worker, mock_dialog, mock_injector, mock_settings_dlg, qtbot, webengine):
    mock_settings_dlg.return_value.exec.return_value = 1
    mock_settings_dlg.return_value.get_options.return_value = {}
    tab = webengine.tab()
    qtbot.addWidget(tab)
    with patch.object(tab.web, "url", return_value=QUrl("http://youtube.com")):
        tab.download_video()
        mock_worker.assert_called_once()
        mock_worker.return_value.start.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_cancel_video(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab.dl_worker = MagicMock()
    tab.dl_worker.isRunning.return_value = True
    tab.cancel_download()
    tab.dl_worker.stop.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_download_finished(mock_injector, qtbot, webengine):
    tab = webengine.tab()
    qtbot.addWidget(tab)
    tab._on_download_finished(True, "Done")
    assert tab.progress.value() == 100
    assert tab.btn_download.toolTip() == "Download Video via yt-dlp"


def test_ytdlp_worker_remote_components_and_single_video():
    """Item 8: YtDlpWorker configures remote_components and noplaylist for single video."""
    worker = YtDlpWorker("https://youtube.com/watch?v=123", "/tmp", {"playlist": False})
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_ydl.return_value.__enter__.return_value = mock_inst
        worker.run()
        assert mock_ydl.called
        opts = mock_ydl.call_args[0][0]
        assert opts.get("remote_components") == ["ejs:github"]
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


def test_ytdlp_stream_worker_remote_components():
    """Item 8: YtDlpStreamWorker sets remote_components."""
    worker = YtDlpStreamWorker("https://youtube.com/watch?v=123")
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_inst.extract_info.return_value = {"url": "https://stream.url"}
        mock_ydl.return_value.__enter__.return_value = mock_inst
        worker.run()
        opts = mock_ydl.call_args[0][0]
        assert opts.get("remote_components") == ["ejs:github"]


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
    assert local_load_allowed(page, QUrl("https://cdn.example/x.js"), T.ResourceTypeScript)

    home = QUrl.fromLocalFile(HOMEPAGE_PATH)
    font = QUrl.fromLocalFile(os.path.join(os.path.dirname(os.path.dirname(HOMEPAGE_PATH)), "fonts", "Inter-Variable.ttf"))
    assert local_load_allowed(home, font, T.ResourceTypeFontResource)
    assert not local_load_allowed(home, other, T.ResourceTypeXhr)
