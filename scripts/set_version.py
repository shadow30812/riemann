"""
Sets Riemann's version in every file that states it.

The version is written in six places: pyproject.toml (what Settings shows,
through importlib.metadata), python-app/riemann/__init__.py, the two crates'
Cargo.toml files, their entries in Cargo.lock, and the README. Each place is
matched by a pattern that must hit exactly once; if one doesn't, nothing is
written, so a moved line can't leave the files disagreeing.

Usage: python scripts/set_version.py 7.2.0, or major, minor or patch to bump
the current version (patch on 7.1.0 gives 7.1.1). --check prints the version
each file holds and exits 1 if they differ. Standard library only.

After changing the version, reinstall the package in the environment
(just build, or pip install -e .) so Settings shows the new one.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = r"\d+\.\d+\.\d+"

PLACES = [
    ("pyproject.toml", r'(?m)^(version = ")(%s)(")'),
    ("python-app/riemann/__init__.py", r'(?m)^(__version__ = ")(%s)(")'),
    ("rust-core/Cargo.toml", r'(?m)\A(\[package\][^\[]*?^version = ")(%s)(")'),
    ("rust-ocr-worker/Cargo.toml", r'(?m)\A(\[package\][^\[]*?^version = ")(%s)(")'),
    ("Cargo.lock", r'(?m)(^name = "riemann_core"\nversion = ")(%s)(")'),
    ("Cargo.lock", r'(?m)(^name = "riemann_ocr_worker"\nversion = ")(%s)(")'),
    ("docs/README.md", r"(Version )(%s)(, MIT licensed)"),
]


def read(root, name):
    with open(os.path.join(root, name), encoding="utf-8", newline="") as handle:
        return handle.read()


def found(root):
    """The version each place holds, as (file, version) pairs."""
    result = []
    for name, pattern in PLACES:
        matches = re.findall(pattern % VERSION, read(root, name))
        if len(matches) != 1:
            raise SystemExit(f"{name}: expected one version line, found {len(matches)}")
        result.append((name, matches[0][1]))
    return result


def bumped(current, part):
    major, minor, patch = (int(n) for n in current.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def set_version(root, new):
    if not re.fullmatch(VERSION, new):
        raise SystemExit(f"not a version: {new!r} (expected MAJOR.MINOR.PATCH)")
    found(root)
    texts = {}
    for name, pattern in PLACES:
        text = texts.get(name, read(root, name))
        texts[name] = re.sub(pattern % VERSION, lambda m: m.group(1) + new + m.group(3), text, count=1)
    for name, text in texts.items():
        with open(os.path.join(root, name), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)


def main(argv):
    root = ROOT
    if len(argv) >= 2 and argv[0] == "--root":
        root, argv = argv[1], argv[2:]
    if argv == ["--check"]:
        places = found(root)
        for name, version in places:
            print(f"{version}  {name}")
        return 0 if len({v for _, v in places}) == 1 else 1
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    versions = {v for _, v in found(root)}
    if len(versions) != 1:
        raise SystemExit("the files disagree; run with --check and fix them first")
    current = versions.pop()
    new = bumped(current, argv[0]) if argv[0] in ("major", "minor", "patch") else argv[0]
    set_version(root, new)
    print(f"{current} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
