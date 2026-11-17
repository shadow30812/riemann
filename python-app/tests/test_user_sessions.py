"""
Whole-app sessions: Riemann started as a person starts it, used through real
keystrokes, mouse drags, menus and dialogs, then quit with Ctrl+Q. Each test
runs one scenario of tests/user_session.py in a child process with its own
throwaway home and XDG folders, and checks what a person would see
afterwards: files on disk, the history, the clipboard.

They start QtWebEngine, so each takes a few seconds.
"""

import json
import os
import subprocess
import sys
import zipfile

import pikepdf
import pytest

from text_fixtures import all_stream_bytes, make_text_pdf

DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_session.py")


def run_session(tmp_path, scenario, *files, **extra_env):
    env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
    env.update(extra_env)
    for var, sub in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        env[var] = str(tmp_path / "profile" / sub)
        os.makedirs(env[var], exist_ok=True)
    env.update(QT_QPA_PLATFORM="offscreen", QTWEBENGINE_DISABLE_SANDBOX="1", _RIEMANN_SANITIZED="1")
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    done = subprocess.run(
        [sys.executable, DRIVER, scenario, str(work), *map(str, files)],
        env=env, cwd=os.path.dirname(DRIVER), capture_output=True, text=True, timeout=240,
    )
    result_file = work / "result.json"
    notes = json.loads(result_file.read_text()) if result_file.exists() else {"error": "no result written"}
    assert "error" not in notes, f"{notes['error']}\n--- stderr ---\n{done.stderr[-3000:]}"
    assert done.returncode == 0, done.stderr[-3000:]
    return notes, env


def history(env):
    with open(os.path.join(env["XDG_DATA_HOME"], "Riemann", "history.json")) as f:
        return json.load(f)


def test_reading_finding_selecting_and_copying(tmp_path):
    pdf = make_text_pdf(tmp_path / "paper.pdf", [(72, 700, 24, "KEEP SECRET KEEP")])
    notes, env = run_session(tmp_path, "read", pdf)
    assert notes["search_found"]
    assert notes["selected"].strip() == "SECRET"
    assert notes["clipboard"].strip() == "SECRET"
    assert [e["path"] for e in history(env)["pdf"]] == [os.path.abspath(pdf)]


def test_redacting_a_word_and_saving_on_close(tmp_path):
    pdf = make_text_pdf(tmp_path / "paper.pdf", [(72, 700, 24, "KEEP SECRET KEEP")], compress=False)
    notes, _ = run_session(tmp_path, "redact", pdf)
    assert notes["word_kept_until_save"]
    assert "Save the page changes" in notes["close_asked"]
    assert b"SECRET" not in all_stream_bytes(pdf)
    assert b"KEEP" in all_stream_bytes(pdf)


def test_the_open_dialog_opens_text_and_images_and_right_steps_to_the_next_image(tmp_path):
    from PIL import Image

    text_file = tmp_path / "notes.txt"
    text_file.write_text("Shopping list\n")
    photos = tmp_path / "photos"
    photos.mkdir()
    image, next_image = photos / "photo 2.png", photos / "photo 10.jpg"
    Image.new("RGB", (120, 80), "teal").save(image)
    Image.new("RGB", (60, 40), "orange").save(next_image)
    notes, env = run_session(tmp_path, "open_text", text_file, image, next_image)
    assert notes["text_view"]
    assert notes["image_offered"]
    assert notes["image_size"] == [120, 80]
    assert notes["stepped_title"] == "photo 10.jpg"
    assert notes["history"] == {"photo 2.png": "Image", "notes.txt": "Text"}


def test_quitting_with_page_edits_waiting_asks_first(tmp_path):
    pdf = make_text_pdf(tmp_path / "paper.pdf", [(72, 700, 24, "Rotate me")])
    notes, _ = run_session(tmp_path, "quit_with_edits", pdf)
    assert notes["file_unchanged_before_quit"]
    assert notes["quit_asked"] and "Save the page changes" in notes["quit_asked"]
    with pikepdf.open(pdf) as doc:
        assert int(doc.pages[0].obj.get("/Rotate", 0)) == 90


LEAVE_PAGE = """<!doctype html><title>{0}</title>
<input style="position:absolute;left:0;top:0;width:240px;height:48px">
<script>addEventListener("beforeunload", e => {{ e.preventDefault(); e.returnValue = ""; }});</script>
"""


def test_a_page_with_unsaved_changes_asks_before_its_tab_or_the_app_closes(tmp_path):
    pages = []
    for name in ("first", "second"):
        page = tmp_path / f"{name}.html"
        page.write_text(LEAVE_PAGE.format(name))
        pages.append(page)
    notes, _ = run_session(tmp_path, "leave_site", *pages)
    assert "may not be saved" in notes["close_asked"]
    assert notes["stayed_open"]
    assert not notes["asked_again"]
    assert "may not be saved" in notes["quit_asked"]
    assert notes["running_after_stay"]


def word_file(path, text):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:rPr><w:sz w:val="48"/></w:rPr>'
            f"<w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return path


def _office():
    from riemann.core.office import find_office

    found = find_office()
    return found is not None and not found.flatpak


@pytest.mark.skipif(os.environ.get("RIEMANN_NO_OFFICE") == "1" or not _office(), reason="LibreOffice is not installed")
def test_a_word_file_saved_as_pdf_takes_a_highlight(tmp_path):
    docx = word_file(tmp_path / "letter.docx", "Hello reader")
    pdf = tmp_path / "letter.pdf"
    notes, env = run_session(tmp_path, "office", docx, pdf)
    assert notes["banner"] and notes["annotations_blocked"]
    assert not notes["banner_after_save"]
    assert notes["docx_unchanged"]
    with pikepdf.open(pdf) as doc:
        subtypes = [str(a.get("/Subtype")) for a in doc.pages[0].obj.get("/Annots", [])]
    assert "/Highlight" in subtypes
    paths = [e["path"] for e in history(env)["pdf"]]
    assert os.path.abspath(docx) in paths and os.path.abspath(pdf) in paths


def test_without_libreoffice_the_prompt_offers_the_basic_view_once(tmp_path):
    first = word_file(tmp_path / "first.docx", "First words")
    second = word_file(tmp_path / "second.docx", "Second words")
    notes, _ = run_session(tmp_path, "office_basic", first, second, RIEMANN_NO_OFFICE="1")
    assert "LibreOffice" in notes["prompt"] and "basic view" in notes["prompt"]
    assert "First words" in notes["first_text"]
    assert not notes["asked_again"] and notes["second_text"]


def test_changing_a_csv_cell_saves_only_after_asking(tmp_path):
    table = tmp_path / "scores.csv"
    table.write_bytes(b"name,score\r\nAda,3\r\nBob,1\r\n")
    notes, _ = run_session(tmp_path, "edit_table", table)
    assert notes["unchanged_before_save"]
    assert "replaces the file" in notes["save_asked"]
    assert notes["after_save"] == "name,score\nAda,30\nBob,1\n"
    assert "aren't saved" in notes["quit_asked"]
    assert table.read_bytes() == b"name,score\r\nAda,30\r\nBob,1\r\n"
