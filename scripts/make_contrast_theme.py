"""
Writes python-app/riemann/assets/theme/modern_contrast.css, the high-contrast
theme, from modern_dark.css.

Each colour of the dark sheet is swapped for a token of CONTRAST in
riemann/ui/palette.py (SWAP names the token; the dark sheet's in-between
shades map to the nearest one, and its pressed shade to PRESSED). The accent
and danger tints keep their role with 1.5 times the opacity, so they stay
visible on black. Focus and checked states then get solid 2px borders.

Run it from the repository root after changing modern_dark.css or CONTRAST:

    python scripts/make_contrast_theme.py

tests/test_palette.py fails while the file differs from what this writes.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME = os.path.join(ROOT, "python-app", "riemann", "assets", "theme")
sys.path.insert(0, os.path.join(ROOT, "python-app"))

from riemann.ui.palette import CONTRAST  # noqa: E402

PRESSED = "#22232A"
SWAP = {
    "#1C1D20": "canvas",
    "#232428": "surface",
    "#17181B": "canvas",
    "#2A2C31": "raised",
    "#2C2E34": "raised",
    "#33353B": None,
    "#303237": "border",
    "#3D4047": "border_strong",
    "#43464E": "border_strong",
    "#F2F3F5": "text",
    "#E6E7EA": "text",
    "#D9DADE": "text",
    "#ECEDEF": "text",
    "#9A9FA8": "muted",
    "#6E737C": "faint",
    "#6B9BFF": "accent",
    "#86AEFF": "accent_hover",
    "#0F1626": "accent_text",
    "#F07070": "danger",
}
TINTS = {(107, 155, 255): (95, 195, 255), (240, 112, 112): (255, 123, 123)}

HEADER = """/* Riemann high-contrast theme, for the dark interface with "High contrast"
   on. Written by scripts/make_contrast_theme.py from modern_dark.css, with
   each colour swapped for its riemann/ui/palette.py CONTRAST token: pure
   black surfaces (pixels off on OLED screens), white text, light borders
   where the dark theme separates panels by shade, and a brighter accent.
   Focus and checked states are drawn with solid 2px borders. Don't edit it
   by hand; change modern_dark.css or CONTRAST and run the script. */"""


def _hex(match: re.Match) -> str:
    token = SWAP[match.group(0).upper()]
    return PRESSED if token is None else CONTRAST[token]


def _tint(match: re.Match) -> str:
    rgb = tuple(int(match.group(i)) for i in (1, 2, 3))
    alpha = min(0.9, round(float(match.group(4)) * 1.5 + 1e-9, 2))
    r, g, b = TINTS[rgb]
    return f"rgba({r}, {g}, {b}, {alpha})"


def _once(pattern: str, replacement: str, text: str) -> str:
    out, count = re.subn(pattern, replacement, text)
    if count != 1:
        raise ValueError(f"{pattern!r} matched {count} times in modern_dark.css")
    return out


def render() -> str:
    with open(os.path.join(THEME, "modern_dark.css"), encoding="utf-8") as f:
        text = f.read()
    text = HEADER + text[text.index("*/") + 2 :]
    text = re.sub(r"#[0-9A-Fa-f]{6}\b", _hex, text)
    text = re.sub(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", _tint, text)
    text = _once(r"(QPlainTextEdit:focus, QTextEdit:focus \{\n    border: )1px", r"\g<1>2px", text)
    accent = CONTRAST["accent"]
    text = _once(
        r"(QPushButton:checked, QToolButton:checked \{\n[^}]*?border: )1px solid rgba\([^)]*\)",
        rf"\g<1>2px solid {accent}",
        text,
    )
    return text


def main() -> None:
    with open(os.path.join(THEME, "modern_contrast.css"), "w", encoding="utf-8") as f:
        f.write(render())


if __name__ == "__main__":
    main()
