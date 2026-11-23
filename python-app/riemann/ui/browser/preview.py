"""
PreviewTextTab: the lightweight view of Markdown, HTML and text files.
"""

import os
import re
from ..reader.utils import render_markdown_safely

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..palette import tokens


class PreviewTextTab(QWidget):
    """
    A lightweight read-only viewer for previewing Markdown, HTML and text files.

    Everything is drawn by QTextBrowser, so no Chromium process is started:
    Markdown is rendered to rich text, HTML is shown with its markup and
    local images (scripts never run), and other text files get syntax
    highlighting when Pygments is available.
    """

    MAX_BYTES = 2 * 1024 * 1024

    def __init__(self, path: str, dark_mode: bool = False, parent=None):
        super().__init__(parent)
        self.current_path = path
        self.is_preview = True
        self.dark_mode = dark_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QTextBrowser

        self.editor = QTextBrowser()
        self.editor.setOpenLinks(False)
        self.editor.anchorClicked.connect(self._on_anchor_clicked)
        layout.addWidget(self.editor)

        self.apply_theme(dark_mode)
        self._load()

    def _read_text(self) -> str:
        with open(self.current_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(self.MAX_BYTES)
            if len(content) == self.MAX_BYTES:
                content += "\n\n[... File truncated for preview ...]"
        return content

    def _load(self) -> None:
        path = self.current_path
        lower = path.lower()
        folder = os.path.dirname(os.path.abspath(path))
        self.editor.document().setBaseUrl(QUrl.fromLocalFile(folder + os.sep))
        self.editor.setSearchPaths([folder])

        try:
            content = self._read_text()
        except Exception as e:
            self.editor.setPlainText(f"Error loading preview: {e}")
            return

        if lower.endswith((".md", ".markdown")):
            self.editor.setHtml(self._markdown_html(content))
        elif lower.endswith((".html", ".htm", ".xhtml")):
            self.editor.setHtml(self._strip_active_content(content))
        else:
            self.editor.setHtml(self._code_html(content, path))

    def _page_css(self) -> str:
        t = tokens(self.dark_mode)
        fg, muted, code_bg, border, link = t["text"], t["muted"], t["raised"], t["border_strong"], t["accent"]
        return f"""
            body {{ color: {fg}; font-size: 15px; }}
            h1, h2 {{ border-bottom: 1px solid {border}; padding-bottom: 4px; }}
            a {{ color: {link}; }}
            code {{ background-color: {code_bg}; font-family: monospace; }}
            pre {{ background-color: {code_bg}; font-family: monospace; padding: 10px; }}
            blockquote {{ color: {muted}; border-left: 3px solid {border}; padding-left: 10px; }}
            table {{ border-collapse: collapse; }}
            th, td {{ border: 1px solid {border}; padding: 4px 8px; }}
        """

    def _markdown_html(self, text: str) -> str:
        try:
            body = render_markdown_safely(text, extensions=("fenced_code", "tables", "sane_lists", "toc"))
        except Exception:
            import html as html_lib

            body = f"<pre>{html_lib.escape(text)}</pre>"
        return f"<html><head><style>{self._page_css()}</style></head><body>{self._qt_blocks(body)}</body></html>"

    def _qt_blocks(self, body: str) -> str:
        """
        Qt's rich text pads and borders table cells only, so code blocks become
        a padded cell and quotes a cell beside a narrow bar. Opening and closing
        tags are replaced one by one, which keeps nested quotes nested.
        """
        t = tokens(self.dark_mode)
        body = re.sub(
            r"<pre\b",
            f'<table width="100%" cellspacing="0" cellpadding="10" bgcolor="{t["raised"]}"'
            ' style="margin: 6px 0; border-style: none;"><tr><td><pre',
            body,
        )
        body = re.sub(r"\n?(</code>)?</pre>", lambda m: (m.group(1) or "") + "</pre></td></tr></table>", body)
        body = re.sub(
            r"<blockquote\b[^>]*>",
            f'<table cellspacing="0" cellpadding="0" style="margin: 6px 0; border-style: none;"><tr>'
            f'<td style="border-style: none none none solid; border-width: 3px; border-color: {t["border_strong"]};'
            f' padding-left: 12px; color: {t["muted"]};">',
            body,
        )
        return body.replace("</blockquote>", "</td></tr></table>")

    @staticmethod
    def _strip_active_content(content: str) -> str:
        """Removes scripts and embedded frames; the preview only shows the page."""
        content = re.sub(r"(?is)<script\b.*?</script>", "", content)
        content = re.sub(r"(?is)<(iframe|object|embed)\b.*?(</\1>|/?>)", "", content)
        return content

    def _code_html(self, text: str, path: str) -> str:
        import html as html_lib

        try:
            from pygments import highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import TextLexer, get_lexer_for_filename

            try:
                lexer = get_lexer_for_filename(path, stripnl=False)
            except Exception:
                lexer = TextLexer()
            formatter = HtmlFormatter(
                noclasses=True,
                nobackground=True,
                style="monokai" if self.dark_mode else "default",
            )
            body = highlight(text, lexer, formatter)
        except Exception:
            body = f"<pre>{html_lib.escape(text)}</pre>"
        return (
            f"<html><head><style>{self._page_css()} pre {{ font-size: 13px; }}</style>"
            f"</head><body>{body}</body></html>"
        )

    def _on_anchor_clicked(self, url: QUrl) -> None:
        if not url.scheme() and url.fragment():
            self.editor.scrollToAnchor(url.fragment())
            return
        relative = url.isRelative()
        if relative:
            url = self.editor.document().baseUrl().resolved(url)
        win = self.window()
        if url.isLocalFile():
            if not relative:
                return
            local = url.toLocalFile()
            if os.path.isfile(local) and hasattr(win, "_open_document"):
                win._open_document(local)
            return
        if url.scheme() in ("http", "https") and hasattr(win, "new_browser_tab"):
            win.new_browser_tab(url.toString())

    def apply_theme(self, dark_mode: bool):
        self.dark_mode = dark_mode
        t = tokens(dark_mode)
        bg = t["canvas"] if dark_mode else t["surface"]
        fg = t["text"]
        self.editor.setStyleSheet(
            f"QTextBrowser {{ background-color: {bg}; color: {fg}; border: none; padding: 16px 24px; }}"
        )

    def toggle_theme(self):
        self.apply_theme(not self.dark_mode)
        self._load()

    def cleanup(self):
        self.editor.clear()

    def update_fullscreen_icon(self, state):
        pass
