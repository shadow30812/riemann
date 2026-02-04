"""
Utility functions for the Reader package.

Handles HTML generation for markdown and text reflow modes.
"""

import html

import markdown


def generate_reflow_html(text: str, dark_mode: bool) -> str:
    """
    Generates an HTML document with Katex support for reflowed text.

    Args:
        text: The raw text content.
        dark_mode: Whether to apply dark theme styles.

    Returns:
        A complete HTML string.
    """
    escaped_text = html.escape(text)
    bg = "#1e1e1e" if dark_mode else "#fff"
    fg = "#ddd" if dark_mode else "#222"

    katex_cdn = """
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
        onload="renderMathInElement(document.body, {delimiters: [{left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false}], throwOnError: false});"></script>
    """

    style = f"""
    body {{
        background: {bg};
        color: {fg};
        padding: 40px;
        font-family: sans-serif;
        white-space: pre-wrap;
        line-height: 1.6;
        max-width: 800px;
        margin: 0 auto;
    }}
    """

    return f"<!DOCTYPE html><html><head>{katex_cdn}<style>{style}</style></head><body>{escaped_text}</body></html>"


def generate_markdown_html(markdown_text: str, dark_mode: bool) -> str:
    """
    Converts Markdown text to stylized HTML.

    Args:
        markdown_text: The raw markdown content.
        dark_mode: Whether to apply dark theme styles.

    Returns:
        A complete HTML string.
    """
    html_content = markdown.markdown(
        markdown_text, extensions=["fenced_code", "tables"]
    )

    bg = "#1e1e1e" if dark_mode else "#fff"
    fg = "#ddd" if dark_mode else "#222"
    pre_bg = "#333" if dark_mode else "#f5f5f5"

    style = f"""
    body {{ background:{bg}; color:{fg}; padding:40px; font-family: sans-serif; max-width: 800px; margin: 0 auto; line-height: 1.6; }}
    pre {{ background: {pre_bg}; padding: 10px; border-radius: 5px; overflow-x: auto; }}
    code {{ font-family: monospace; }}
    a {{ color: #50a0ff; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #555; padding: 8px; text-align: left; }}
    """

    return (
        f"<html><head><style>{style}</style></head><body>{html_content}</body></html>"
    )
