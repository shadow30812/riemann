"""
Riemann's version is stated in six files, and scripts/set_version.py sets them all.

Bumps used to be made by hand, file by file; one missed file leaves Settings,
the crates and the README naming different versions.
"""

import importlib.util
import pathlib
import shutil

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("set_version", REPO / "scripts" / "set_version.py")
set_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(set_version)


def copy_of_repo(tmp_path):
    for name in {name for name, _ in set_version.PLACES}:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    return tmp_path


def test_every_file_states_the_same_version():
    versions = {version for _, version in set_version.found(REPO)}
    assert len(versions) == 1, set_version.found(REPO)


def test_setting_a_version_changes_every_file_and_nothing_else(tmp_path):
    root = copy_of_repo(tmp_path)
    before = {name: (root / name).read_text(encoding="utf-8") for name, _ in set_version.PLACES}
    old = set_version.found(root)[0][1]
    assert set_version.main(["--root", str(root), "98.76.54"]) == 0
    assert {version for _, version in set_version.found(root)} == {"98.76.54"}
    for name, text in before.items():
        after = (root / name).read_text(encoding="utf-8")
        assert after.replace("98.76.54", old) == text


def test_dependency_versions_in_the_lock_stay(tmp_path):
    root = copy_of_repo(tmp_path)
    set_version.main(["--root", str(root), "98.76.54"])
    assert (root / "Cargo.lock").read_text(encoding="utf-8").count("98.76.54") == 2


@pytest.mark.parametrize("part, expected", [("major", "8.0.0"), ("minor", "7.2.0"), ("patch", "7.1.1")])
def test_bumps(part, expected):
    assert set_version.bumped("7.1.0", part) == expected


def test_a_missing_line_writes_nothing(tmp_path):
    root = copy_of_repo(tmp_path)
    readme = root / "docs" / "README.md"
    readme.write_text("no version here\n", encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        set_version.main(["--root", str(root), "98.76.54"])
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == pyproject


def test_rejects_a_malformed_version(tmp_path):
    root = copy_of_repo(tmp_path)
    with pytest.raises(SystemExit):
        set_version.main(["--root", str(root), "7.2"])
