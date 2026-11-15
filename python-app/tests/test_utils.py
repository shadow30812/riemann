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
