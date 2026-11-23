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
    "pptx": "python-pptx",
}
NOT_DISTRIBUTIONS = {"riemann", "riemann_core"}


def _project():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)["project"]


def _name(requirement):
    return re.split(r"[\[<>=! @]", requirement, maxsplit=1)[0].lower().replace("_", "-")


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


def _lock_pins(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    entries = re.findall(r"^([A-Za-z0-9][^\s;\\]*)[^\n]*((?:\n\s+--hash=sha256:[0-9a-f]{64}[^\n]*)+)", text, re.M)
    return (
        {_name(req): req.split("==", 1)[1] for req, _ in entries},
        len(entries),
        len(re.findall(r"^[A-Za-z0-9]", text, re.M)),
    )


def test_every_on_demand_package_has_a_hashed_lock_with_its_pinned_version():
    from riemann.core.dependencies import LATEX_OCR_LOCK, LATEX_OCR_REQUIREMENTS, lock_file

    for name, req in PINNED.items():
        if name == "yt-dlp-ejs":
            continue
        pins, hashed, listed = _lock_pins(lock_file(name))
        assert hashed == listed, name
        assert pins[_name(req)] == req.split("==", 1)[1], name
    pins, hashed, listed = _lock_pins(lock_file(LATEX_OCR_LOCK))
    assert hashed == listed
    for name in LATEX_OCR_REQUIREMENTS:
        assert pins[_name(PINNED[name])] == PINNED[name].split("==", 1)[1]
    pins, _, _ = _lock_pins(lock_file("yt-dlp"))
    assert pins["yt-dlp-ejs"] == PINNED["yt-dlp-ejs"].split("==", 1)[1]


def test_installs_require_hashes_and_take_nothing_outside_the_lock():
    from riemann.core.dependencies import pip_install_command

    command = pip_install_command("python", "pix2tex[gui]")
    assert command[:4] == ["python", "-m", "pip", "install"]
    assert "--require-hashes" in command and "--no-deps" in command
    assert command[-2] == "-r" and command[-1].endswith(os.path.join("requirements", "pix2tex.txt"))


def test_startup_never_creates_the_venv_or_looks_for_python(tmp_path, monkeypatch):
    from riemann.core import dependencies

    monkeypatch.setattr(
        dependencies,
        "_venv_paths",
        lambda: (str(tmp_path), str(tmp_path / "venv"), str(tmp_path / "venv" / "bin" / "python")),
    )
    started = []
    monkeypatch.setattr(dependencies.subprocess, "check_output", lambda *a, **k: started.append(a))
    monkeypatch.setattr(dependencies.subprocess, "check_call", lambda *a, **k: started.append(a))
    monkeypatch.setattr(dependencies, "_find_base_python", lambda: started.append("probe") or "python")
    assert dependencies.mount_existing_venv() is False
    assert dependencies.venv_python() == ""
    assert started == []
    assert not (tmp_path / "venv").exists()


def test_app_startup_mounts_only_an_existing_venv():
    with open(os.path.join(APP, "app.py"), encoding="utf-8") as f:
        source = f.read()
    assert "mount_existing_venv()" in source
    assert "setup_and_get_venv" not in source


def _frozen_at(monkeypatch, tmp_path, version="3.11.9"):
    from riemann.core import dependencies

    monkeypatch.setattr(dependencies, "_is_frozen", lambda: True)
    monkeypatch.setattr(dependencies, "packages_dir", lambda: str(tmp_path / "packages" / "py311"))
    monkeypatch.setattr(
        dependencies, "_bundled_version", lambda full=False: version if full else version.rsplit(".", 1)[0]
    )
    return dependencies


def test_packaged_build_installs_wheels_for_its_own_python_with_any_python(tmp_path, monkeypatch):
    """A Python 3.12 installs the 3.11 build's packages: --target and the 3.11 lines of the lock."""
    dependencies = _frozen_at(monkeypatch, tmp_path)
    command = dependencies.pip_install_command(sys.executable, "faster-whisper")
    assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--require-hashes" in command and "--no-deps" in command
    assert command[command.index("--target") + 1] == str(tmp_path / "packages" / "py311")
    assert command[command.index("--python-version") + 1] == "3.11"
    with open(command[-1], encoding="utf-8") as f:
        lock = f.read()
    assert re.search(r"^av==18\.1\.0 ; python_full_version < '3\.12' --hash=sha256:[0-9a-f]{64}", lock, re.M)
    assert "av==19.0.2" not in lock
    assert "faster-whisper==" in lock


def test_packaged_build_finds_a_python_of_another_version(monkeypatch):
    from riemann.core import dependencies

    monkeypatch.setattr(dependencies, "_is_frozen", lambda: True)
    monkeypatch.setattr(dependencies.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(dependencies.subprocess, "check_output", lambda *a, **k: "3\n/usr/bin/python3.12\n")
    assert dependencies._find_base_python() == "/usr/bin/python3.12"


def test_packaged_build_uninstalls_only_the_files_its_package_recorded(tmp_path, monkeypatch):
    dependencies = _frozen_at(monkeypatch, tmp_path)
    folder = tmp_path / "packages" / "py311"
    (folder / "yt_dlp").mkdir(parents=True)
    (folder / "yt_dlp" / "__init__.py").write_text("")
    (folder / "bin").mkdir()
    (folder / "bin" / "yt-dlp").write_text("")
    (folder / "deno").mkdir()
    (folder / "deno" / "__init__.py").write_text("")
    info = folder / "yt_dlp-2026.10.26.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: yt-dlp\nVersion: 2026.10.26\n")
    (info / "RECORD").write_text(
        "yt_dlp/__init__.py,,\nbin/yt-dlp,,\nyt_dlp-2026.10.26.dist-info/METADATA,,\n"
        "yt_dlp-2026.10.26.dist-info/RECORD,,\n../outside.txt,,\n"
    )
    (tmp_path / "packages" / "outside.txt").write_text("keep")
    assert dependencies.installed_names() >= {"yt-dlp"}
    assert dependencies._remove_from_folder(str(folder), "yt-dlp")
    assert sorted(p.name for p in folder.iterdir()) == ["deno"]
    assert (tmp_path / "packages" / "outside.txt").exists()
    assert "yt-dlp" not in dependencies.installed_names()
    assert not dependencies._remove_from_folder(str(folder), "yt-dlp")
