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

import pikepdf

from text_fixtures import all_stream_bytes, make_text_pdf

DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_session.py")


def run_session(tmp_path, scenario, *files):
    env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
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


def test_the_open_dialog_opens_text_but_wont_offer_an_image(tmp_path):
    text_file = tmp_path / "notes.txt"
    text_file.write_text("Shopping list\n")
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + bytes(64))
    notes, env = run_session(tmp_path, "open_text", text_file, image)
    assert notes["text_view"]
    assert not notes["image_offered"]
    assert notes["tab_count_unchanged"]
    assert notes["history"] == {"notes.txt": "Text"}


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
