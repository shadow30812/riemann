"""
Every build reads its module exclusions from scripts/build-excludes.txt.

The list used to be written out in four places, and the copies had drifted:
the Windows script missed eight of the modules the Linux one skipped.

The Nuitka builds also share one pinned version, scripts/build-requirements.txt.
The release workflow installed the newest Nuitka while local builds kept 4.2,
whose PySide6 plugin left the callback of QTimer.singleShot(ms, widget, fn)
unguarded; PySide6 then freed it while still in use, and those builds crashed
at random.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BUILDS = ["scripts/nbuild.sh", "scripts/nbuild.ps1", ".github/workflows/release.yml", "Riemann.spec"]


@pytest.mark.parametrize("build", BUILDS)
def test_build_reads_the_shared_exclude_list(build):
    text = (REPO / build).read_text(encoding="utf-8")
    assert "build-excludes.txt" in text
    assert re.findall(r"--nofollow-import-to=[A-Za-z_]", text) == []


def test_exclude_list_names_modules():
    lines = [
        line.strip()
        for line in (REPO / "scripts" / "build-excludes.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines and len(lines) == len(set(lines))
    assert all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", line) for line in lines)


NUITKA_BUILDS = ["scripts/nbuild.sh", "scripts/nbuild.ps1", ".github/workflows/release.yml"]


def test_nuitka_version_is_pinned_at_a_fixed_release():
    pins = re.findall(
        r"^nuitka==(\S+)$", (REPO / "scripts" / "build-requirements.txt").read_text(encoding="utf-8"), re.M
    )
    assert len(pins) == 1
    assert tuple(int(part) for part in pins[0].split(".")) >= (4, 3)


@pytest.mark.parametrize("build", NUITKA_BUILDS)
def test_nuitka_builds_use_the_pinned_version(build):
    text = (REPO / build).read_text(encoding="utf-8")
    assert "build-requirements.txt" in text
    assert re.findall(r"pip install[^\n]*\bnuitka\b(?!-)", text) == []


@pytest.mark.parametrize("build", BUILDS)
def test_build_leaves_out_the_javascript_tests_and_logs(build):
    text = (REPO / build).read_text(encoding="utf-8")
    assert "__tests__" in text
    assert ".log" in text


@pytest.mark.parametrize("build", BUILDS)
def test_build_bundles_the_local_youtube_solver(build):
    assert "yt_dlp_ejs" in (REPO / build).read_text(encoding="utf-8")


@pytest.mark.parametrize("build", BUILDS)
def test_build_bundles_the_office_readers(build):
    text = (REPO / build).read_text(encoding="utf-8")
    for package in ("mammoth", "pptx", "openpyxl", "xlrd", "striprtf"):
        assert f"--include-package={package}" in text or f"'{package}'" in text, package


def test_windows_builds_agree_on_console_mode_and_icon():
    builds = [(REPO / b).read_text(encoding="utf-8") for b in ("scripts/nbuild.ps1", ".github/workflows/release.yml")]
    modes = [re.findall(r"^\s*--windows-console-mode=(\w+)", t, re.M) for t in builds]
    assert modes[0] == modes[1] and len(modes[0]) == 1
    for text in builds:
        icons = re.findall(r"^\s*--windows-icon-from-ico=(\S+)", text, re.M)
        assert len(icons) == 1
        assert (REPO / icons[0]).read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_no_two_assets_differ_only_in_case():
    seen = {}
    for path in (REPO / "python-app" / "riemann" / "assets").rglob("*"):
        key = str(path).lower()
        assert key not in seen, (seen.get(key), path)
        seen[key] = path


def test_shipped_code_names_no_developer_machine_paths():
    for path in [REPO / "build_entry.py", *(REPO / "python-app" / "riemann").glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert "plecs" not in text, path
        assert "PDFIUM_DYNAMIC_LIB_PATH" not in text, path


def test_frozen_entry_drops_only_folders_with_another_qt(tmp_path):
    import ast

    tree = ast.parse((REPO / "build_entry.py").read_text(encoding="utf-8"))
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_foreign_qt_dirs")
    scope = {"os": __import__("os"), "__file__": str(REPO / "build_entry.py")}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "build_entry.py", "exec"), scope)
    qt = tmp_path / "tool"
    qt.mkdir()
    (qt / "libQt6Core.so.6").write_bytes(b"")
    other = tmp_path / "plain"
    other.mkdir()
    assert scope["_foreign_qt_dirs"]([str(qt), str(other), ""]) == [str(qt)]
