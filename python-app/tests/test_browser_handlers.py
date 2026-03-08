from unittest.mock import MagicMock

from PySide6.QtWebEngineCore import QWebEngineScript
from riemann.ui.browser_handlers import ScriptInjector


def test_inject_ad_skipper():
    mock_profile = MagicMock()
    injector = ScriptInjector(mock_profile)

    injector.inject_ad_skipper()

    mock_profile.scripts().insert.assert_called_once()
    inserted_script = mock_profile.scripts().insert.call_args[0][0]

    assert inserted_script.name() == "RiemannAdBlock"
    assert "const clearAds = () =>" in inserted_script.sourceCode()
    assert (
        inserted_script.injectionPoint()
        == QWebEngineScript.InjectionPoint.DocumentCreation
    )
    assert inserted_script.worldId() == QWebEngineScript.ScriptWorldId.ApplicationWorld
    assert inserted_script.runsOnSubFrames() is True


def test_inject_backspace_handler():
    mock_profile = MagicMock()
    injector = ScriptInjector(mock_profile)

    injector.inject_backspace_handler()

    mock_profile.scripts().insert.assert_called_once()
    inserted_script = mock_profile.scripts().insert.call_args[0][0]

    assert inserted_script.name() == "RiemannBackspace"
    assert 'e.key === "Backspace"' in inserted_script.sourceCode()
    assert (
        inserted_script.injectionPoint()
        == QWebEngineScript.InjectionPoint.DocumentCreation
    )


def test_inject_dark_mode_true():
    mock_profile = MagicMock()
    mock_page = MagicMock()
    injector = ScriptInjector(mock_profile)

    injector.inject_smart_dark_mode(mock_page, True)

    mock_profile.scripts().insert.assert_called_once()
    inserted_script = mock_profile.scripts().insert.call_args[0][0]

    assert inserted_script.name() == "RiemannSmartDark"
    assert "html { filter: invert(1) hue-rotate(180deg)" in inserted_script.sourceCode()
    assert (
        inserted_script.injectionPoint()
        == QWebEngineScript.InjectionPoint.DocumentReady
    )
    assert inserted_script.worldId() == QWebEngineScript.ScriptWorldId.UserWorld

    mock_page.runJavaScript.assert_called_once()


def test_inject_dark_mode_false():
    mock_profile = MagicMock()
    mock_page = MagicMock()
    injector = ScriptInjector(mock_profile)

    injector.inject_smart_dark_mode(mock_page, False)

    mock_profile.scripts().insert.assert_called_once()
    inserted_script = mock_profile.scripts().insert.call_args[0][0]

    assert "if(el) el.remove();" in inserted_script.sourceCode()
    mock_page.runJavaScript.assert_called_once()
