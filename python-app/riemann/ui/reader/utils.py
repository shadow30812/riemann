"""
Utility functions for the Reader package.

Handles HTML generation for markdown and text reflow modes.
"""

import html
import re

import markdown

from ..palette import tokens


def generate_reflow_html(text: str, dark_mode: bool) -> str:
    """An HTML page for reflowed text, with KaTeX for the maths."""
    escaped_text = html.escape(text)
    t = tokens(dark_mode)
    bg = t["canvas"] if dark_mode else t["surface"]
    fg = t["text"]

    katex_cdn = """
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            renderMathInElement(document.body, {
                delimiters: [
                    {left: '$$', right: '$$', display: true}, 
                    {left: '$', right: '$', display: false}
                ], 
                throwOnError: false
            });
        });
    </script>
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


UNSAFE_URL = re.compile(r"""(<[a-z]+\b[^<>]*?\s(?:href|src)=")\s*(?:javascript|vbscript|data|file):""", re.I)


def render_markdown_safely(markdown_text: str) -> str:
    """
    Markdown to HTML with raw HTML shown as text and script links disabled.

    A Markdown file may come from anywhere. Python-Markdown copies raw HTML
    (including <script>) straight through and keeps javascript: links, and the
    preview runs with JavaScript on, so both are switched off here.
    """
    md = markdown.Markdown(extensions=["fenced_code", "tables"])
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    return UNSAFE_URL.sub(r"\1#blocked:", md.convert(markdown_text))


def generate_markdown_html(markdown_text: str, dark_mode: bool) -> str:
    """Markdown rendered as an HTML page, with KaTeX for the maths."""
    html_content = render_markdown_safely(markdown_text)

    t = tokens(dark_mode)
    bg = t["canvas"] if dark_mode else t["surface"]
    fg = t["text"]
    pre_bg = t["raised"]
    link = t["accent"]
    border = t["border_strong"]

    katex_cdn = """
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            renderMathInElement(document.body, {
                delimiters: [
                    {left: '$$', right: '$$', display: true}, 
                    {left: '$', right: '$', display: false}
                ], 
                throwOnError: false
            });
        });
    </script>
    """

    style = f"""
    body {{ background:{bg}; color:{fg}; padding:40px; font-family: sans-serif; max-width: 800px; margin: 0 auto; line-height: 1.6; }}
    pre {{ background: {pre_bg}; padding: 10px; border-radius: 5px; overflow-x: auto; }}
    code {{ font-family: monospace; }}
    a {{ color: {link}; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid {border}; padding: 8px; text-align: left; }}
    """

    return f"<!DOCTYPE html><html><head>{katex_cdn}<style>{style}</style></head><body>{html_content}</body></html>"
