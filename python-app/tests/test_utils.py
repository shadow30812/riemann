from riemann.ui.reader.utils import generate_markdown_html, generate_reflow_html


def test_generate_reflow_html_dark():
    html_out = generate_reflow_html("Test <Content>", dark_mode=True)

    assert "Test &lt;Content&gt;" in html_out
    assert "background: #1e1e1e" in html_out
    assert "color: #ddd" in html_out
    assert "katex.min.css" in html_out


def test_generate_reflow_html_light():
    html_out = generate_reflow_html("Math: $x^2$", dark_mode=False)

    assert "Math: $x^2$" in html_out
    assert "background: #fff" in html_out
    assert "color: #222" in html_out


def test_generate_markdown_html_dark():
    md = "# Hello\n\n**Bold**"
    html_out = generate_markdown_html(md, dark_mode=True)

    assert "<h1>Hello</h1>" in html_out
    assert "<strong>Bold</strong>" in html_out
    assert "background:#1e1e1e" in html_out
    assert "color:#ddd" in html_out
    assert "background: #333" in html_out


def test_generate_markdown_html_light():
    md = "Table\n---\nA"
    html_out = generate_markdown_html(md, dark_mode=False)

    assert "background:#fff" in html_out
    assert "background: #f5f5f5" in html_out
