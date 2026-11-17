"""
core/assets.py is the only place that knows where bundled files are.

The PyInstaller layout can't be built here, so it is checked by faking
sys.frozen and sys._MEIPASS. Two copies of this lookup used to get that layout
wrong (one left out the riemann folder, one walked up from ui/), which nothing
noticed because the source checkout and the Nuitka build don't use it.
"""

import ast
import os
import pathlib
import sys

import pytest

from riemann.core import assets

ROOT = pathlib.Path(__file__).resolve().parent.parent / "riemann"


def _sources():
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_source_layout_finds_real_files():
    assert os.path.isfile(assets.asset_path("icons", "pdf.png"))
    assert os.path.isfile(assets.asset_path("homepage", "homepage.html"))
    assert assets.bundle_dir() is None


def test_pyinstaller_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert assets.is_frozen()
    assert assets.bundle_dir() == str(tmp_path)
    assert assets.asset_path("icons", "pdf.png") == os.path.join(str(tmp_path), "riemann", "assets", "icons", "pdf.png")


def test_icon_path_uses_the_white_variant_only_when_there_is_one():
    assert assets.icon_path("sun.svg", dark=True).endswith("sun-white.svg")
    assert assets.icon_path("sun.svg").endswith(os.sep + "sun.svg")
    assert assets.icon_path("pdf.png", dark=True).endswith("pdf.png")


def test_build_layout_knowledge_lives_only_in_core_assets():
    offenders = []
    for path in _sources():
        if path.name == "assets.py" and path.parent.name == "core":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("_MEIPASS", "__compiled__", '(sys, "frozen"', "abspath(__file__)"):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == []


def _literal_asset_calls():
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("asset_path", "icon_path") or not node.args:
                continue
            if not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in node.args):
                continue
            parts = [a.value for a in node.args]
            yield pytest.param(name, parts, id=f"{path.relative_to(ROOT)}:{node.lineno}")


@pytest.mark.parametrize("name,parts", list(_literal_asset_calls()))
def test_every_literal_asset_exists(name, parts):
    path = assets.icon_path(parts[0]) if name == "icon_path" else assets.asset_path(*parts)
    assert os.path.exists(path), path
