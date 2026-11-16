"""
Every build reads its module exclusions from scripts/build-excludes.txt.

The list used to be written out in four places, and the copies had drifted:
the Windows script missed eight of the modules the Linux one skipped.
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
