"""
Rules checked on the app's source.

Delayed callbacks are tied to the object they touch
(QTimer.singleShot(ms, widget, fn)), so Qt drops them when that object is
deleted first; a two-argument singleShot has no such tie.

Text files are opened with an explicit encoding: without one, Windows reads
and writes in its ANSI code page, not UTF-8.
"""

import ast
import os

APP = os.path.join(os.path.dirname(__file__), "..", "riemann")


def calls():
    for folder, _, files in os.walk(APP):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    yield os.path.relpath(path, APP), node


def test_every_single_shot_names_its_context():
    loose = []
    for path, node in calls():
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "singleShot"
            and len(node.args) == 2
        ):
            loose.append(f"{path}:{node.lineno}")
    assert loose == []



def mode_of(node) -> str:
    mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
    return mode.value if isinstance(mode, ast.Constant) else "r"


def test_text_files_are_opened_with_an_encoding():
    loose = []
    for path, node in calls():
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in ("read_text", "write_text") and isinstance(func, ast.Attribute):
            if not any(k.arg == "encoding" for k in node.keywords) and len(node.args) < (1 if name == "read_text" else 2):
                loose.append(f"{path}:{node.lineno}")
        elif name == "open" and isinstance(func, ast.Name) and "b" not in str(mode_of(node)):
            if not any(k.arg == "encoding" for k in node.keywords):
                loose.append(f"{path}:{node.lineno}")
    assert loose == []
