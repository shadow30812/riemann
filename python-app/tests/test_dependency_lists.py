"""
pyproject.toml is the one list of Python dependencies. These tests check
that its optional groups match the versions the app installs on demand
(core/dependencies.py PINNED), and that every third-party package the app
imports is declared somewhere in it.
"""

import ast
import os
import re
import sys
import tomllib

from riemann.core.dependencies import PINNED

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
APP = os.path.join(ROOT, "python-app", "riemann")
IMPORT_TO_DIST = {
    "PIL": "pillow",
    "markdown": "markdown",
    "yt_dlp": "yt-dlp",
    "pyhanko_certvalidator": "pyhanko-certvalidator",
    "faster_whisper": "faster-whisper",
    "shiboken6": "pyside6",
    "pygments": "pygments",
    "pyhanko": "pyhanko",
    "PySide6": "pyside6",
}
NOT_DISTRIBUTIONS = {"riemann", "riemann_core"}


def _project():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)["project"]


def _name(requirement):
    return re.split(r"[\[<>=! @]", requirement, 1)[0].lower().replace("_", "-")


def test_optional_groups_match_the_versions_the_app_installs():
    groups = _project()["optional-dependencies"]
    declared = {r.replace(" ", "") for r in groups["bundled"] + groups["extra"]}
    assert declared == {r.replace(" ", "") for r in PINNED.values()}


def test_every_imported_package_is_declared():
    project = _project()
    declared = {_name(r) for r in project["dependencies"]}
    for group in ("bundled", "extra"):
        declared |= {_name(r) for r in project["optional-dependencies"][group]}
    missing = {}
    for folder, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in ("assets", "__pycache__")]
        for name in files:
            if not name.endswith(".py") or name.startswith("script"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    top = module.split(".")[0]
                    if top in sys.stdlib_module_names or top in NOT_DISTRIBUTIONS:
                        continue
                    dist = IMPORT_TO_DIST.get(top, top.lower().replace("_", "-"))
                    if dist not in declared:
                        missing.setdefault(dist, os.path.relpath(path, APP))
    assert missing == {}


def test_nothing_declared_is_unused():
    project = _project()
    imported = set()
    for folder, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in ("assets", "__pycache__")]
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    for node in ast.walk(ast.parse(f.read())):
                        if isinstance(node, ast.Import):
                            imported |= {a.name.split(".")[0] for a in node.names}
                        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                            imported.add(node.module.split(".")[0])
    dists = {IMPORT_TO_DIST.get(m, m.lower().replace("_", "-")) for m in imported}
    unused = [r for r in project["dependencies"] if _name(r) not in dists]
    assert unused == []
