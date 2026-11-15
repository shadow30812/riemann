"""Tests for per-paper notes (riemann/ui/reader/notes.py)."""

import os
import stat

from PySide6.QtCore import QUrl

from riemann.ui.reader.notes import NotesPanel, notes_path_for


def test_notes_live_next_to_the_pdf(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    assert notes_path_for(str(pdf)) == str(tmp_path / "paper.notes.md")


def test_read_only_folders_use_the_app_data_folder(tmp_path, monkeypatch):
    folder = tmp_path / "ro"
    folder.mkdir()
    pdf = folder / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr("riemann.ui.reader.notes.app_data_dir", lambda: str(tmp_path / "data"))
    os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(folder, os.W_OK):
            return
        path = notes_path_for(str(pdf))
        assert path.startswith(str(tmp_path / "data" / "notes"))
    finally:
        os.chmod(folder, stat.S_IRWXU)


def test_panel_saves_only_written_notes_and_links_pages(qapp, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    panel = NotesPanel()
    panel.page_provider = lambda: 6
    panel.load(str(pdf))
    panel.save()
    assert not (tmp_path / "paper.notes.md").exists()

    panel.editor.setPlainText("Key idea: ")
    panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
    panel.insert_page_link()
    panel.save()
    text = (tmp_path / "paper.notes.md").read_text()
    assert text == "Key idea: [p. 7](#page=7)"

    jumps = []
    panel.page_link_clicked.connect(jumps.append)
    panel.set_preview(True)
    assert "p. 7" in panel.preview.toPlainText()
    panel._on_link(QUrl("#page=7"))
    assert jumps == [6]

    again = NotesPanel()
    again.load(str(pdf))
    assert again.editor.toPlainText() == text
