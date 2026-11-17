"""
Writes the hashed locks the app installs optional features from.

The Settings dependency manager and the Snip-to-LaTeX installer run
"pip install --require-hashes --no-deps -r <lock>" (core/dependencies.py), so
every package they install, dependencies included, is pinned by version and
SHA-256. This script makes those locks with uv, one per feature, into
python-app/riemann/assets/requirements/:

- every optional requirement in pyproject.toml ("bundled" and "extra", the
  versions PINNED repeats) gets <name>.txt, with the packages in COMPANIONS
  (yt-dlp brings its YouTube solver and Deno, the JavaScript runtime that runs
  it); ONLY_AS_COMPANION ones get no lock of their own;
- LATEX_OCR gets latex-ocr.txt, everything Snip-to-LaTeX needs in one go.

All locks are resolved against one shared set of versions
(requirements/optional-all.txt, every optional package at once) and the app's own pinned dependencies, so two
features never pull different versions of a package they share. --universal
keeps the hashes of every platform's wheels, so the same files serve Linux,
Windows and macOS, and every Python from 3.11 up.

Run it from the repository root after changing a version in pyproject.toml:

    python scripts/lock_optional.py

It needs uv on PATH and network access to PyPI.
"""

import os
import re
import subprocess
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "python-app", "riemann", "assets", "requirements")
PYTHON_VERSION = "3.11"
COMPANIONS = {"yt-dlp": ["yt-dlp-ejs", "deno"]}
ONLY_AS_COMPANION = {"yt-dlp-ejs"}
LATEX_OCR = ["pix2tex", "torch", "torchvision"]


def dist_name(requirement: str) -> str:
    return re.split(r"[\[<>=! @;]", requirement, 1)[0].strip().lower().replace("_", "-")


def compile_lock(requirements: list, output: str, constraints: list) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "requirements.in")
        with open(source, "w", encoding="utf-8") as f:
            f.write("\n".join(requirements) + "\n")
        command = [
            "uv", "pip", "compile", source,
            "--universal", "--generate-hashes", "--no-header", "--no-annotate",
            "--python-version", PYTHON_VERSION,
            "--output-file", output,
        ]
        for constraint in constraints:
            command += ["--constraint", constraint]
        subprocess.run(command, check=True)


def main() -> int:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        project = tomllib.load(f)["project"]
    groups = project["optional-dependencies"]
    optional = groups["bundled"] + groups["extra"]
    by_name = {dist_name(r): r for r in optional}
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        core = os.path.join(tmp, "core.txt")
        with open(core, "w", encoding="utf-8") as f:
            f.write("\n".join(project["dependencies"]) + "\n")
        everything = os.path.join(ROOT, "requirements", "optional-all.txt")
        compile_lock(optional, everything, [core])
        shared = os.path.join(tmp, "shared.txt")
        with open(everything, encoding="utf-8") as f:
            pins = re.findall(r"^([A-Za-z0-9].*?)\s*\\?$", f.read(), re.M)
        with open(shared, "w", encoding="utf-8") as f:
            f.write("\n".join(re.sub(r"\[.*?\]", "", p, count=1) for p in pins) + "\n")
        for name, requirement in by_name.items():
            if name in ONLY_AS_COMPANION:
                continue
            extra = [by_name[c] for c in COMPANIONS.get(name, [])]
            compile_lock([requirement] + extra, os.path.join(OUT, f"{name}.txt"), [core, shared])
        compile_lock([by_name[n] for n in LATEX_OCR], os.path.join(OUT, "latex-ocr.txt"), [core, shared])
    print(f"Wrote the locks in {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
