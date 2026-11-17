"""
Utility functions for the Reader package.

Handles HTML generation for markdown and text reflow modes.

Both pages typeset maths with KaTeX from a CDN. KATEX_FILES pins each file by
version and by its SHA-384 (subresource integrity, the hash browsers check), so
a changed file on the CDN is refused instead of run; the hashes are the ones
KaTeX publishes for that version. To update, change KATEX_VERSION and every
hash together.
"""

import html
import re

import markdown
import markdown.treeprocessors
import markdown.util

from ...core.endpoints import JSDELIVR_NPM
from ..palette import tokens

KATEX_VERSION = "0.16.9"
KATEX_CDN = f"{JSDELIVR_NPM}/katex@{KATEX_VERSION}/dist"
KATEX_FILES = {
    "katex.min.css": "sha384-n8MVd4RsNIU0tAv4ct0nTaAbDJwPJzDEaqSD1odI+WdtXRGWt2kTvGFasHpSy3SV",
    "katex.min.js": "sha384-XjKyOOlGwcjNTAIQHIpgOno0Hl1YQqzUOEleOLALmuqehneUG+vnGctmUb0ZY0l8",
    "contrib/auto-render.min.js": "sha384-+VBxd3r6XgURycqtZ117nYw44OOcIax56Z4dCRWbxyPt0Koah1uHoK0o4+/RRE05",
}


def _katex_tag(name: str) -> str:
    url, sri = f"{KATEX_CDN}/{name}", KATEX_FILES[name]
    if name.endswith(".css"):
        return f'<link rel="stylesheet" href="{url}" integrity="{sri}" crossorigin="anonymous">'
    return f'<script defer src="{url}" integrity="{sri}" crossorigin="anonymous"></script>'


KATEX_HEAD = "\n".join(_katex_tag(name) for name in KATEX_FILES) + """
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


def generate_reflow_html(text: str, dark_mode: bool) -> str:
    """An HTML page for reflowed text, with KaTeX for the maths."""
    escaped_text = html.escape(text)
    t = tokens(dark_mode)
    bg = t["canvas"] if dark_mode else t["surface"]
    fg = t["text"]

    katex_cdn = KATEX_HEAD

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


SAFE_SCHEMES = ("http", "https", "mailto")
_URL_SCHEME = re.compile(r"([a-z][a-z0-9+.\-]*):", re.I)
_URL_IGNORED = re.compile(r"[\x00-\x20\x7f]+")
BLOCKED_URL = "#blocked"


def url_is_safe(url: str, schemes: tuple = ()) -> bool:
    """
    True for relative URLs and for http, https, mailto and the given extra
    schemes, judged as a browser reads the attribute: entities decoded, and
    the whitespace and control characters it skips removed.
    """
    text = html.unescape(url.replace(markdown.util.AMP_SUBSTITUTE, "&"))
    match = _URL_SCHEME.match(_URL_IGNORED.sub("", text))
    return match is None or match.group(1).lower() in SAFE_SCHEMES + tuple(schemes)


class _SafeLinks(markdown.treeprocessors.Treeprocessor):
    def __init__(self, md, schemes: tuple) -> None:
        super().__init__(md)
        self.schemes = schemes

    def run(self, root) -> None:
        for element in root.iter():
            for attr in ("href", "src"):
                value = element.get(attr)
                if value is not None and not url_is_safe(value, self.schemes):
                    element.set(attr, BLOCKED_URL)


def render_markdown_safely(markdown_text: str, extensions: tuple = ("fenced_code", "tables"), schemes: tuple = ()) -> str:
    """
    Markdown to HTML with raw HTML shown as text and every link and image
    limited to url_is_safe.

    A Markdown file may come from anywhere. Python-Markdown copies raw HTML
    (including <script>) straight through and keeps javascript: links, and the
    reader's preview runs with JavaScript on. The links are checked on the
    parsed tree, after the inline patterns ran, rather than on the source, so
    javascript&#58; and other encodings are caught as the browser decodes them.
    """
    md = markdown.Markdown(extensions=list(extensions))
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    md.treeprocessors.register(_SafeLinks(md, tuple(schemes)), "riemann_safe_links", 1)
    return md.convert(markdown_text)


def generate_markdown_html(markdown_text: str, dark_mode: bool) -> str:
    """Markdown rendered as an HTML page, with KaTeX for the maths."""
    html_content = render_markdown_safely(markdown_text)

    t = tokens(dark_mode)
    bg = t["canvas"] if dark_mode else t["surface"]
    fg = t["text"]
    pre_bg = t["raised"]
    link = t["accent"]
    border = t["border_strong"]

    katex_cdn = KATEX_HEAD

    style = f"""
    body {{ background:{bg}; color:{fg}; padding:40px; font-family: sans-serif; max-width: 800px; margin: 0 auto; line-height: 1.6; }}
    pre {{ background: {pre_bg}; padding: 10px; border-radius: 5px; overflow-x: auto; }}
    code {{ font-family: monospace; }}
    a {{ color: {link}; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid {border}; padding: 8px; text-align: left; }}
    """

    return f"<!DOCTYPE html><html><head>{katex_cdn}<style>{style}</style></head><body>{html_content}</body></html>"
