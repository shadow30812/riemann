"""
Settings keys shared between modules are named once, in core/settings.py.

Two modules spelling the same key by hand is how a setting silently stops
working: one side gets renamed or mistyped and reads its default forever. The
names' string values are what existing installs have stored, so they are
pinned here too.
"""

import ast
import collections
import pathlib

from riemann.core import settings

ROOT = pathlib.Path(__file__).resolve().parent.parent / "riemann"


def _sources():
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_no_literal_settings_key_is_shared_between_modules():
    users = collections.defaultdict(set)
    for path, tree in _sources():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("value", "setValue", "remove", "contains")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                users[node.args[0].value].add(str(path.relative_to(ROOT)))
    shared = {key: sorted(files) for key, files in users.items() if len(files) > 1}
    assert shared == {}


def test_settings_stores_are_opened_only_in_core_settings():
    offenders = [
        f"{path.relative_to(ROOT)}:{node.lineno}"
        for path, tree in _sources()
        if path.relative_to(ROOT) != pathlib.Path("core/settings.py")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "QSettings"
    ]
    assert offenders == []


def test_stored_names_never_change():
    assert (settings.ORGANISATION, settings.APPLICATION) == ("Riemann", "PDFReader")
    assert (settings.FAVOURITES_STORE, settings.BROWSER_STORE, settings.FAVICON_STORE) == (
        "Favorites",
        "BrowserSettings",
        "FaviconCache",
    )
    names = [
        "DARK_MODE_KEY", "UI_SCALE_KEY", "DEFAULT_DIR_KEY", "LAST_DIR_KEY", "VERTICAL_TABS_KEY",
        "TABS_COLLAPSED_KEY", "AUTOSCROLL_SPEED_KEY", "CLEAN_EXIT_KEY", "HOMEPAGE_NAME_KEY",
        "NOTES_VISIBLE_KEY", "SIDEBAR_VISIBLE_KEY",
        "FAVOURITE_FOLDERS_KEY",
    ]
    stored = [
        "darkMode", "app/ui_scale", "app/default_dir", "app/last_dir", "app/vertical_tabs",
        "app/tabs_collapsed", "app/autoscroll_speed", "session/clean_exit", "homepage/custom_name",
        "reader/notes_visible", "reader/sidebar_visible",
        "folders",
    ]
    assert [getattr(settings, name) for name in names] == stored
