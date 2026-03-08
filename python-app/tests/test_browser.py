from unittest.mock import MagicMock, mock_open, patch

import pytest
from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QFocusEvent
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
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
)


@pytest.fixture
def app(qtbot):
    return qtbot


@patch("subprocess.Popen")
def test_ytdlpworker_success(mock_popen, qtbot):
    mock_proc = MagicMock()
    mock_proc.stdout = ["[download]  50.0%", "[download] 100.0%"]
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    worker = YtDlpWorker("http://fake.url", "/fake/dir")

    with qtbot.waitSignals([worker.progress, worker.finished], timeout=1000):
        worker.run()


@patch("subprocess.Popen")
def test_ytdlpworker_failure(mock_popen, qtbot):
    mock_proc = MagicMock()
    mock_proc.stdout = []
    mock_proc.returncode = 1
    mock_popen.return_value = mock_proc

    worker = YtDlpWorker("http://fake.url", "/fake/dir")

    with qtbot.waitSignal(worker.finished, timeout=1000) as blocker:
        worker.run()

    assert blocker.args == [False, "Download failed."]


@patch("subprocess.Popen")
def test_ytdlpworker_cancelled(mock_popen, qtbot):
    mock_proc = MagicMock()
    mock_proc.stdout = ["[download]  10.0%", "[download]  20.0%"]
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    worker = YtDlpWorker("http://fake.url", "/fake/dir")
    worker.is_cancelled = True

    with qtbot.waitSignal(worker.finished, timeout=1000) as blocker:
        worker.run()

    assert blocker.args == [False, "Download cancelled."]


@patch("subprocess.Popen", side_effect=FileNotFoundError)
def test_ytdlpworker_not_found(mock_popen, qtbot):
    worker = YtDlpWorker("http://fake.url", "/fake/dir")
    with qtbot.waitSignal(worker.finished, timeout=1000) as blocker:
        worker.run()
    assert "not installed" in blocker.args[1]


def test_ytdlpworker_stop():
    worker = YtDlpWorker("http://fake.url", "/fake/dir")
    worker.process = MagicMock()
    worker.stop()
    assert worker.is_cancelled is True
    worker.process.terminate.assert_called_once()


def test_webpage_createwindow_background(qtbot):
    profile = QWebEngineProfile()
    main_win = QWidget()
    qtbot.addWidget(main_win)
    parent_view = QWebEngineView(main_win)

    mock_tab = MagicMock()
    mock_tab.web.page.return_value = QWebEnginePage(profile)
    main_win.new_browser_tab = MagicMock(return_value=mock_tab)

    page = WebPage(profile, parent_view)
    new_page = page.createWindow(QWebEnginePage.WebWindowType.WebBrowserBackgroundTab)

    main_win.new_browser_tab.assert_called_with(url="", background=True)
    assert new_page is not None


def test_webpage_createwindow_tab(qtbot):
    profile = QWebEngineProfile()
    main_win = QWidget()
    qtbot.addWidget(main_win)
    parent_view = QWebEngineView(main_win)

    mock_tab = MagicMock()
    mock_tab.web.page.return_value = QWebEnginePage(profile)
    main_win.new_browser_tab = MagicMock(return_value=mock_tab)

    page = WebPage(profile, parent_view)
    new_page = page.createWindow(QWebEnginePage.WebWindowType.WebBrowserTab)

    main_win.new_browser_tab.assert_called_with(url="", background=False)
    assert new_page is not None


def test_webpage_createwindow_popup(qtbot):
    profile = QWebEngineProfile()
    page = WebPage(profile)
    new_page = page.createWindow(QWebEnginePage.WebWindowType.WebDialog)
    assert len(page._popups) == 1

    popup_view = page._popups[0]
    page._cleanup_popup(popup_view)
    assert len(page._popups) == 0


def test_webpage_js_console(capsys):
    profile = QWebEngineProfile()
    page = WebPage(profile)
    page.javaScriptConsoleMessage(
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel,
        "Test Message",
        10,
        "test.js",
    )
    captured = capsys.readouterr()
    assert "[JS] Test Message" in captured.out


@patch("PySide6.QtCore.QSettings.setValue")
def test_webpage_accept_navigation_request(mock_set_value):
    profile = QWebEngineProfile()
    page = WebPage(profile)
    url = QUrl("riemann-save://test_payload")
    result = page.acceptNavigationRequest(
        url, QWebEnginePage.NavigationType.NavigationTypeTyped, True
    )

    assert result is False
    mock_set_value.assert_called_once_with("homepage_links", "test_payload")


def test_request_interceptor_block():
    interceptor = RequestInterceptor()
    info = MagicMock(spec=QWebEngineUrlRequestInfo)
    info.requestUrl().toString.return_value = "https://doubleclick.net/ad"

    interceptor.interceptRequest(info)
    info.block.assert_called_once_with(True)


def test_request_interceptor_whatsapp():
    interceptor = RequestInterceptor()
    info = MagicMock(spec=QWebEngineUrlRequestInfo)
    info.requestUrl().toString.return_value = "https://web.whatsapp.com/"

    interceptor.interceptRequest(info)
    info.setHttpHeader.assert_called_with(b"User-Agent", interceptor.spoofed_ua)


def test_request_interceptor_monkeytype():
    interceptor = RequestInterceptor()
    info = MagicMock(spec=QWebEngineUrlRequestInfo)
    info.requestUrl().toString.return_value = "https://monkeytype.com/api"
    info.resourceType.return_value = (
        QWebEngineUrlRequestInfo.ResourceType.ResourceTypeXhr
    )

    interceptor.interceptRequest(info)
    info.setHttpHeader.assert_any_call(b"Referer", b"https://monkeytype.com/")


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_init(mock_injector, qtbot):
    tab = BrowserTab(start_url="https://example.com")
    qtbot.addWidget(tab)
    assert tab.txt_url.placeholderText() == "Enter URL or Search..."


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_incognito_init(mock_injector, qtbot):
    tab = BrowserTab(incognito=True)
    qtbot.addWidget(tab)
    assert tab.txt_url.placeholderText() == "Incognito Mode"
    assert tab.incognito is True


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_focus_in(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    event = QFocusEvent(QEvent.Type.FocusIn)
    with patch.object(tab.web, "setFocus") as mock_set_focus:
        tab.focusInEvent(event)
        mock_set_focus.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
@patch("os.getlogin", return_value="TestUser")
def test_browser_tab_homepage_load(mock_login, mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab.web.page().runJavaScript = MagicMock()
    with patch.object(tab.web, "url", return_value=QUrl("file:///homepage.html")):
        tab._on_homepage_load_finished(True)
        tab.web.page().runJavaScript.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_feature_permission(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab.web.page().setFeaturePermission = MagicMock()

    try:
        feature = getattr(QWebEnginePage.Feature, "ClipboardReadWrite", None)
        tab._on_feature_permission_requested(QUrl("https://test.com"), feature)
    except AttributeError:
        pass


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_devtools(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab.open_devtools()
    assert hasattr(tab, "_devtools_window")
    assert tab._devtools_window.isVisible()


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_zoom(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab.web.setZoomFactor = MagicMock()
    tab.web.zoomFactor = MagicMock(return_value=1.0)

    tab.modify_zoom(0.5)
    tab.web.setZoomFactor.assert_called_with(1.5)
    assert tab.lbl_zoom.text() == "150%"

    tab.reset_zoom()
    tab.web.setZoomFactor.assert_called_with(1.0)
    assert tab.lbl_zoom.text() == "100%"


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_search_toggle(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    assert tab.search_bar.isHidden() is True
    tab.toggle_search()
    assert tab.search_bar.isHidden() is False


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_navigate(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab.web.load = MagicMock()

    tab.txt_url.setText("https://example.com")
    tab.navigate_to_url()
    tab.web.load.assert_called_with(QUrl("https://example.com"))


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_toast(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab.show_toast("Hello World")
    assert tab.lbl_toast.isHidden() is False
    assert tab.lbl_toast.text() == "Hello World"


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_bookmark_toggle(mock_injector, qtbot):
    main_win = MagicMock()
    main_win.bookmarks_manager.is_bookmarked.return_value = False

    tab = BrowserTab()
    qtbot.addWidget(tab)
    with patch.object(tab, "window", return_value=main_win):
        tab.toggle_bookmark()
        main_win.bookmarks_manager.add.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
@patch("builtins.open", new_callable=mock_open, read_data="console.log('audio');")
@patch("os.path.exists", return_value=True)
def test_browser_tab_music_mode(mock_exists, mock_file, mock_injector, qtbot):
    tab = BrowserTab()
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
def test_browser_tab_download_handler(mock_dialog, mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    mock_item = MagicMock(spec=QWebEngineDownloadRequest)

    tab._handle_download(mock_item)
    mock_item.setDownloadDirectory.assert_called_with("/fake")
    mock_item.setDownloadFileName.assert_called_with("path.pdf")
    mock_item.accept.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
@patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value="/fake/dir")
@patch("riemann.ui.browser.YtDlpWorker")
def test_browser_tab_download_video(mock_worker, mock_dialog, mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    with patch.object(tab.web, "url", return_value=QUrl("http://youtube.com")):
        tab.download_video()
        mock_worker.assert_called_once()
        mock_worker.return_value.start.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_cancel_video(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab.dl_worker = MagicMock()
    tab.dl_worker.isRunning.return_value = True
    tab.cancel_download()
    tab.dl_worker.stop.assert_called_once()


@patch("riemann.ui.browser.ScriptInjector")
def test_browser_tab_download_finished(mock_injector, qtbot):
    tab = BrowserTab()
    qtbot.addWidget(tab)
    tab._on_download_finished(True, "Done")
    assert tab.progress.value() == 100
    assert tab.btn_download.text() == "⬇"
