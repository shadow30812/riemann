"""
Service addresses live in core/endpoints.py, the search engine is a setting,
and code loaded from a CDN is pinned by hash.
"""

import ast
import pathlib
import re

import pytest

from riemann.core import endpoints
from riemann.core.settings import SEARCH_URL_KEY, app_settings, search_url

ROOT = pathlib.Path(__file__).resolve().parent.parent / "riemann"


@pytest.fixture
def search_setting():
    store = app_settings()
    yield store
    store.remove(SEARCH_URL_KEY)


def test_search_url_encodes_the_query(search_setting):
    search_setting.remove(SEARCH_URL_KEY)
    assert search_url(" a&b #c ") == "https://www.google.com/search?q=a%26b+%23c"


def test_search_url_follows_the_setting(search_setting):
    search_setting.setValue(SEARCH_URL_KEY, "https://duckduckgo.com/?q={query}&ia=web")
    assert search_url("x y") == "https://duckduckgo.com/?q=x+y&ia=web"


@pytest.mark.parametrize("template", ["https://example.com/search", "javascript:alert({query})", ""])
def test_search_url_ignores_unusable_templates(search_setting, template):
    search_setting.setValue(SEARCH_URL_KEY, template)
    assert search_url("q") == "https://www.google.com/search?q=q"


def test_loopback_url():
    assert endpoints.loopback_url(8123, "/tag") == "http://127.0.0.1:8123/tag"


def test_loopback_address_is_written_only_in_core_endpoints():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "endpoints.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and re.search(r"127\.0\.0\.1|localhost:\d", node.value)
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_katex_tags_carry_their_hashes():
    from riemann.ui.reader import utils

    for page in (utils.generate_markdown_html("$x$", False), utils.generate_reflow_html("x", True)):
        tags = re.findall(r"<(?:script|link)[^>]*cdn\.jsdelivr[^>]*>", page)
        assert len(tags) == len(utils.KATEX_FILES)
        for tag in tags:
            assert 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag
            assert f"katex@{utils.KATEX_VERSION}/" in tag


def test_remote_addresses_are_written_only_in_core_endpoints():
    allowed = {
        "http://www.idpf.org/2007/opf",
        "https://example.com",
        "http://openoffice.org/2001/registry",
        "http://www.w3.org/2001/XMLSchema",
        "http://www.w3.org/2001/XMLSchema-instance",
        "http://www.w3.org/1999/xlink",
    }
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "endpoints.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for url in re.findall(r"https?://[^\s\"'<>]+", node.value):
                    if url not in allowed and not re.match(r"https?://(127\.0\.0\.1|localhost)", url):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {url}")
    assert offenders == []
