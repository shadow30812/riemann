"""
Every relative import in the app resolves to a module that has the imported names.

Imports inside functions only run when the function does, so a module moved to
another folder can break one silently: the reader's full-screen button imported
RiemannWindow from "...app", which stopped existing when the reader was split
into ui/reader/mixins/.
"""

import ast
import importlib
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "riemann"


def _relative_imports():
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT.parent).with_suffix("")
        package = rel.parts[:-1]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = list(package[: len(package) - (node.level - 1)])
                module = ".".join(base + ([node.module] if node.module else []))
                names = [a.name for a in node.names if a.name != "*"]
                yield pytest.param(module, names, id=f"{rel}:{node.lineno}")


@pytest.mark.parametrize("module,names", list(_relative_imports()))
def test_relative_import_resolves(module, names):
    mod = importlib.import_module(module)
    for name in names:
        if not hasattr(mod, name):
            importlib.import_module(f"{module}.{name}")
