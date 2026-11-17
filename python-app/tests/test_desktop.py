"""The Linux desktop entry: what the file manager offers Riemann for."""

import os
import re
from unittest.mock import patch

from riemann.window import desktop

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "install_icon.sh")


def test_a_packaged_build_registers_pdfs_text_markdown_html_and_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with (
        patch.object(desktop.sys, "platform", "linux"),
        patch.object(desktop, "is_frozen", return_value=True),
        patch.object(desktop.subprocess, "run"),
    ):
        desktop.install_linux_integration()
    entry = (tmp_path / ".local" / "share" / "applications" / "Riemann.desktop").read_text()
    types = re.search(r"^MimeType=(.*);$", entry, re.M).group(1).split(";")
    assert {"application/pdf", "text/plain", "text/markdown", "text/html", "inode/directory"} <= set(types)


def test_the_install_script_registers_the_same_types():
    with open(SCRIPT) as f:
        line = re.search(r"^MimeType=(.*);$", f.read(), re.M).group(1)
    assert line.split(";") == list(desktop.MIME_TYPES)
