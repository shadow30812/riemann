import pytest
from riemann.ui.palette import DARK, LIGHT
from riemann.ui.reader.utils import generate_markdown_html, generate_reflow_html


def test_generate_reflow_html_dark():
    html_out = generate_reflow_html("Test <Content>", dark_mode=True)

    assert "Test &lt;Content&gt;" in html_out
    assert f"background: {DARK['canvas']}" in html_out
    assert f"color: {DARK['text']}" in html_out
    assert "katex.min.css" in html_out


def test_generate_reflow_html_light():
    html_out = generate_reflow_html("Math: $x^2$", dark_mode=False)

    assert "Math: $x^2$" in html_out
    assert f"background: {LIGHT['surface']}" in html_out
    assert f"color: {LIGHT['text']}" in html_out


def test_generate_markdown_html_dark():
    md = "# Hello\n\n**Bold**"
    html_out = generate_markdown_html(md, dark_mode=True)

    assert "<h1>Hello</h1>" in html_out
    assert "<strong>Bold</strong>" in html_out
    assert f"background:{DARK['canvas']}" in html_out
    assert f"color:{DARK['text']}" in html_out
    assert f"background: {DARK['raised']}" in html_out


def test_generate_markdown_html_light():
    md = "Table\n---\nA"
    html_out = generate_markdown_html(md, dark_mode=False)

    assert f"background:{LIGHT['surface']}" in html_out
    assert f"background: {LIGHT['raised']}" in html_out


def test_markdown_preview_shows_raw_html_as_text():
    from riemann.ui.reader.utils import generate_markdown_html

    out = generate_markdown_html("hi <script>alert(1)</script>\n\n<img src=x onerror=alert(2)>", False)
    body = out.split("<body>", 1)[1]
    assert "<script>alert" not in body and "<img src=x" not in body
    assert "&lt;script&gt;" in body


def test_markdown_preview_blocks_script_links():
    from riemann.ui.reader.utils import render_markdown_safely

    out = render_markdown_safely("[a](javascript:alert(1)) [b](https://example.org) ![c](file:///etc/passwd)")
    assert 'href="javascript:' not in out and 'src="file:' not in out
    assert 'href="https://example.org"' in out


@pytest.mark.parametrize(
    "link",
    [
        "[x](javascript&#58;alert(1))",
        "[x](javascript&colon;alert(1))",
        "[x](java&#x09;script:alert(1))",
        "[x](JaVaScRiPt:alert(1))",
        "[x](vbscript:msgbox)",
        "![x](data:image/svg+xml;base64,AA)",
        "[x](file:///etc/passwd)",
    ],
)
def test_markdown_links_are_checked_as_the_browser_decodes_them(link):
    from riemann.ui.reader.utils import render_markdown_safely

    out = render_markdown_safely(link)
    assert 'href="#blocked"' in out or 'src="#blocked"' in out


def test_markdown_keeps_ordinary_links():
    from riemann.ui.reader.utils import render_markdown_safely

    out = render_markdown_safely("[a](https://a.org/?x=1&y=2) [b](notes/b.md) [c](#top) [d](mailto:a@b.org)")
    assert "#blocked" not in out
    assert render_markdown_safely("[p. 3](page:3)", schemes=("page",)).count('href="page:3"') == 1


def test_text_preview_markdown_drops_raw_html_and_script_links(qapp, tmp_path):
    from riemann.ui.browser import PreviewTextTab

    note = tmp_path / "note.md"
    note.write_text("<b onclick='x()'>hi</b> [a](javascript&#58;x) [b](file:///etc/passwd)")
    tab = PreviewTextTab(str(note))
    html_out = tab._markdown_html(note.read_text())
    tab.deleteLater()
    assert "onclick" not in html_out.replace("&lt;b onclick", "")
    assert "javascript" not in html_out and "file:///etc" not in html_out


def test_text_preview_opens_only_relative_local_links(qapp, tmp_path):
    from unittest.mock import MagicMock

    from PySide6.QtCore import QUrl

    from riemann.ui.browser import PreviewTextTab

    note = tmp_path / "note.md"
    note.write_text("x")
    other = tmp_path / "other.md"
    other.write_text("y")
    tab = PreviewTextTab(str(note))
    window = MagicMock()
    tab.window = lambda: window
    tab._on_anchor_clicked(QUrl.fromLocalFile("/etc/passwd"))
    window._open_document.assert_not_called()
    tab._on_anchor_clicked(QUrl("other.md"))
    window._open_document.assert_called_once_with(str(other))
    tab.deleteLater()


def test_ai_answers_are_rendered_without_raw_html_or_script_links(qapp):
    from riemann.ui.sidecar import AiSidecarPanel

    panel = AiSidecarPanel()
    panel.messages = [{"role": "assistant", "content": "<img src=x onerror=alert(1)> [a](javascript&#58;x) [p. 2]"}]
    shown = []
    panel.view.setHtml = shown.append
    panel._render()
    panel.deleteLater()
    assert "onerror" not in shown[-1].replace("&lt;img src=x onerror", "")
    assert "javascript" not in shown[-1]
    assert 'href="page:2"' in shown[-1]


def test_streamed_answer_is_redrawn_while_it_streams_not_per_chunk(qapp):
    import time

    from PySide6.QtCore import QCoreApplication

    from riemann.ui.sidecar import AiSidecarPanel

    panel = AiSidecarPanel()
    renders = []
    panel._render = lambda: renders.append(len(panel._pending))
    panel._render_timer.timeout.disconnect()
    panel._render_timer.timeout.connect(panel._render)
    panel._busy = True
    end = time.monotonic() + 0.5
    chunks = 0
    while time.monotonic() < end:
        panel._on_chunk(panel._request_id, "word ")
        chunks += 1
        QCoreApplication.processEvents()
        time.sleep(0.005)
    panel.deleteLater()
    assert 2 <= len(renders) < chunks / 3
