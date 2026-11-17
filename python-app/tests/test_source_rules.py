"""
Rules checked on the app's source.

Delayed callbacks are tied to the object they touch
(QTimer.singleShot(ms, widget, fn)), so Qt drops them when that object is
deleted first; a two-argument singleShot has no such tie.
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

