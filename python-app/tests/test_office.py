"""
Office files: recognising them, finding LibreOffice, converting with it (a
fake soffice script for the conversion service, the real one when it is
installed), the locked-down profile that keeps remote pictures from being
fetched, the cache, and the read-only reader tab with Save as PDF, Edit in
LibreOffice and updating when the file changes.
"""

import base64
import http.server
import json
import os
import shutil
import stat
import sys
import threading
import time
import zipfile
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from riemann.core import office
from riemann.core.doctypes import dialog_filter, document_class, document_kind, office_type
from riemann.core.office import OfficeConversion, OfficeError, find_office
from riemann.core.settings import OFFICE_PATH_KEY, app_settings

CORPUS_PDF = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")
FAKE = '''#!{python}
import json, os, shutil, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_SOFFICE_LOG"], "a") as log:
    log.write(json.dumps({{"args": args, "env": {{k: os.environ.get(k) for k in ("LD_LIBRARY_PATH", "PYTHONPATH", "SAL_USE_VCLPLUGIN")}}}}) + "\\n")
mode = os.environ.get("FAKE_SOFFICE_MODE", "ok")
if mode == "sleep":
    time.sleep(60)
if mode == "child":
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.environ["FAKE_SOFFICE_CHILD"], "w") as f:
        f.write(str(child.pid))
    child.wait()
if mode == "ok" and "--convert-to" in args:
    out = args[args.index("--outdir") + 1]
    stem = os.path.splitext(os.path.basename(args[-1]))[0]
    shutil.copy({pdf!r}, os.path.join(out, stem + "." + args[args.index("--convert-to") + 1]))
'''


def wait_for(condition, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "soffice"
    script.parent.mkdir()
    script.write_text(FAKE.format(python=sys.executable, pdf=os.path.abspath(CORPUS_PDF)))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "soffice.log"
    monkeypatch.setenv("FAKE_SOFFICE_LOG", str(log))
    monkeypatch.setenv("FAKE_SOFFICE_MODE", "ok")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/somewhere/else")
    monkeypatch.delenv("RIEMANN_NO_OFFICE", raising=False)
    office.clear_cache()
    office.refresh()

    class Fake:
        path = str(script)
        found = find_office(str(script))

        @staticmethod
        def calls():
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines()]

    yield Fake
    office.clear_cache()
    office.refresh()


def docx(path, text="Hello", external_image=""):
    rels = (
        f'<Relationship Id="rImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="{external_image}" TargetMode="External"/>'
        if external_image
        else ""
    )
    picture = (
        '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/><wp:docPr id="1" name="p"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
        '<pic:nvPicPr><pic:cNvPr id="1" name="p"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:link="rImg"/>'
        '<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="914400" cy="914400"/></a:xfrm><a:prstGeom prst="rect"/></pic:spPr></pic:pic></a:graphicData>'
        "</a:graphic></wp:inline></w:drawing></w:r></w:p>"
        if external_image
        else ""
    )
    ns = (
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {ns}><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>{picture}</w:body></w:document>',
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
    return str(path)


def odf(path, mimetype):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", "<x/>")
    return str(path)


def test_office_files_are_known_by_name_and_by_content(tmp_path):
    for name, kind, cls in (
        ("a.docx", "document", "Word"), ("b.DOC", "document", "Word"), ("c.odt", "document", "Word"),
        ("d.rtf", "document", "Word"), ("e.pptx", "presentation", "Slides"), ("f.odp", "presentation", "Slides"),
        ("g.xlsx", "spreadsheet", "Spreadsheet"), ("h.ods", "spreadsheet", "Spreadsheet"), ("i.odg", "drawing", "Slides"),
    ):
        assert document_kind(name) == "office"
        assert office_type(name) == kind
        assert document_class(name) == cls
    assert document_kind(str(tmp_path / "graph.dot")) != "office"
    renamed = docx(tmp_path / "report")
    assert document_kind(renamed) == "office" and office_type(renamed) == "document"
    slides = odf(tmp_path / "talk", "application/vnd.oasis.opendocument.presentation")
    assert document_kind(slides) == "office" and office_type(slides) == "presentation"
    rtf = tmp_path / "letter"
    rtf.write_text("{\\rtf1\\ansi Hello}")
    assert document_kind(str(rtf)) == "office"
    plain = tmp_path / "data.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", "x")
    assert document_kind(str(plain)) == "binary"
    assert "*.docx" in dialog_filter() and "Office Files" in dialog_filter()


def test_a_running_riemann_accepts_office_files_by_name(tmp_path):
    from riemann.window.instance import accepts_handoff

    assert accepts_handoff(docx(tmp_path / "report.docx"))
    assert not accepts_handoff(docx(tmp_path / "report"))


def test_finding_libreoffice(tmp_path, monkeypatch):
    office.refresh()
    monkeypatch.setenv("RIEMANN_NO_OFFICE", "1")
    assert find_office() is None
    monkeypatch.delenv("RIEMANN_NO_OFFICE")
    folder = tmp_path / "bin"
    folder.mkdir()
    versioned = folder / "libreoffice9.4"
    versioned.write_text("#!/bin/sh\n")
    versioned.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    monkeypatch.setattr(office, "_linux_folders", lambda: [])
    monkeypatch.setattr(office, "_flatpak", lambda: None)
    found = find_office()
    assert found is not None and found.command == (str(versioned),)
    chosen = folder / "my-soffice"
    chosen.write_text("#!/bin/sh\n")
    chosen.chmod(0o755)
    assert find_office(str(chosen)).command == (str(chosen),)
    versioned.unlink()
    assert find_office() is not None
    office.refresh()
    assert find_office() is None
    office.refresh()


def test_conversion_is_cached_until_the_file_changes(fake, tmp_path):
    source = docx(tmp_path / "report.docx")
    first = OfficeConversion(source, office=fake.found).run()
    assert first.endswith(".pdf") and os.path.dirname(first) == office.files_dir()
    with open(first, "rb") as f:
        assert f.read(5) == b"%PDF-"
    assert OfficeConversion(source, office=fake.found).run() == first
    assert len(fake.calls()) == 1
    os.utime(source, (time.time() + 5, time.time() + 5))
    second = OfficeConversion(source, office=fake.found).run()
    assert second != first and len(fake.calls()) == 2
    assert not [e for e in os.listdir(office.files_dir()) if e.startswith("convert-")]


def test_libreoffice_runs_headless_in_a_locked_down_profile_of_its_own(fake, tmp_path):
    source = docx(tmp_path / "report.docx")
    OfficeConversion(source, office=fake.found).run()
    call = fake.calls()[0]
    args = call["args"]
    assert any(a.startswith("-env:UserInstallation=file://") and "profile" in a for a in args)
    assert "--headless" in args and args[-1] == os.path.abspath(source)
    assert call["env"] == {"LD_LIBRARY_PATH": None, "PYTHONPATH": None, "SAL_USE_VCLPLUGIN": "svp"}
    settings = open(os.path.join(office.profile_dir(), "user", "registrymodifications.xcu")).read()
    for name, value in (("BlockUntrustedRefererLinks", "true"), ("DisableMacrosExecution", "true"), ("MacroSecurityLevel", "3")):
        assert f'oor:name="{name}" oor:op="fuse"><value>{value}</value>' in settings


def test_a_failed_slow_or_cancelled_conversion_says_so(fake, tmp_path, monkeypatch):
    source = docx(tmp_path / "report.docx")
    monkeypatch.setenv("FAKE_SOFFICE_MODE", "nothing")
    with pytest.raises(OfficeError, match="damaged or protected by a password"):
        OfficeConversion(source, office=fake.found).run()
    monkeypatch.setenv("FAKE_SOFFICE_MODE", "sleep")
    start = time.time()
    with pytest.raises(OfficeError, match="more than 1 seconds"):
        OfficeConversion(source, office=fake.found, timeout=1).run()
    assert time.time() - start < 10
    conversion = OfficeConversion(source, office=fake.found)
    errors = []
    thread = threading.Thread(target=lambda: errors.append(pytest.raises(OfficeError, conversion.run).value))
    thread.start()
    assert wait_for(lambda: conversion._proc is not None, 10)
    conversion.cancel()
    thread.join(10)
    assert str(errors[0]) == "Cancelled."
    with pytest.raises(OfficeError, match="isn't installed"):
        OfficeConversion(source, office=None).run()


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_cancelling_stops_the_process_libreoffice_starts(fake, tmp_path, monkeypatch):
    source = docx(tmp_path / "report.docx")
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_SOFFICE_MODE", "child")
    monkeypatch.setenv("FAKE_SOFFICE_CHILD", str(pid_file))
    conversion = OfficeConversion(source, office=fake.found)
    thread = threading.Thread(target=lambda: pytest.raises(OfficeError, conversion.run))
    thread.start()
    assert wait_for(lambda: pid_file.exists() and pid_file.read_text(), 10)
    child = int(pid_file.read_text())
    conversion.cancel()
    thread.join(10)
    assert wait_for(lambda: not alive(child), 5)


def test_the_cache_is_trimmed_oldest_first_and_can_be_cleared(fake, tmp_path):
    folder = office.files_dir()
    os.makedirs(folder, exist_ok=True)
    for i in range(5):
        name = os.path.join(folder, f"{i}.pdf")
        with open(name, "wb") as f:
            f.write(b"x" * 100)
        os.utime(name, (1000 + i, 1000 + i))
    office.prune(limit=250)
    assert sorted(os.listdir(folder)) == ["3.pdf", "4.pdf"]
    office.clear_cache()
    assert not os.path.exists(office.cache_root())


def test_edit_in_libreoffice_opens_the_file_in_its_own_editor(fake, tmp_path):
    source = docx(tmp_path / "report.docx")
    office.open_in_office(source, fake.found)
    assert wait_for(lambda: fake.calls(), 10)
    call = fake.calls()[0]
    assert call["args"] == [os.path.abspath(source)]
    assert call["env"]["SAL_USE_VCLPLUGIN"] is None and call["env"]["LD_LIBRARY_PATH"] is None


REAL = find_office() if os.environ.get("RIEMANN_NO_OFFICE") != "1" else None
needs_libreoffice = pytest.mark.skipif(
    REAL is None or REAL.flatpak, reason="LibreOffice is not installed"
)


@needs_libreoffice
def test_real_libreoffice_converts_without_fetching_remote_pictures(tmp_path):
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        source = docx(tmp_path / "tracked.docx", "Hello tracked", f"http://127.0.0.1:{server.server_port}/pixel.png")
        pdf = OfficeConversion(source, office=REAL, folder=str(tmp_path / "out")).run()
        with open(pdf, "rb") as f:
            assert f.read(5) == b"%PDF-"
        time.sleep(0.3)
    finally:
        server.shutdown()
    assert hits == []


@needs_libreoffice
def test_real_libreoffice_recalculates_a_workbook_for_the_table_view(qtbot, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from riemann.ui.viewers import SpreadsheetTab

    book = openpyxl.Workbook()
    book.active.append(["Rent", 1200.5])
    book.active.append(["Food", 800])
    book.active["B3"] = "=SUM(B1:B2)"
    path = str(tmp_path / "budget.xlsx")
    book.save(path)
    tab = SpreadsheetTab(path)
    qtbot.addWidget(tab)
    assert wait_for(lambda: tab.models, timeout=120)
    assert tab.model.cell(2, 1) == "2000.5"
    assert tab.btn_pages.isVisibleTo(tab)
    tab.cleanup()


@pytest.fixture
def window(qtbot, fake):
    from riemann.app import RiemannWindow

    app_settings().setValue(OFFICE_PATH_KEY, fake.path)
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)
    win.show()
    yield win
    app_settings().remove(OFFICE_PATH_KEY)


def test_an_office_file_opens_read_only_and_save_as_pdf_makes_it_editable(window, tmp_path, fake):
    from riemann.ui.reader.tab import ReaderTab

    source = docx(tmp_path / "report.docx")
    before = open(source, "rb").read()
    tab = window._open_document(source, preview=False)
    assert isinstance(tab, ReaderTab)
    assert wait_for(lambda: tab.current_doc is not None)
    assert tab.current_path == source and tab.pdf_path() == tab.converted_pdf
    assert window.tabs_main.tabText(window.tabs_main.indexOf(tab)) == "report.docx"
    assert tab.converted_banner.isVisible()
    tab.toggle_annotation_mode(True)
    assert not tab.anno_toolbar.isVisible()
    tab._add_anno_data(0, {"type": "note", "rel_pos": (0.1, 0.1), "text": "x", "color": "#000000"})
    assert not any(tab.annotations.values())
    assert not tab._editable() and tab._forms_blocked

    dest = str(tmp_path / "report.pdf")
    with patch("riemann.ui.reader.mixins.document.QFileDialog.getSaveFileName", return_value=(dest, "")):
        tab.save_document()
    assert wait_for(lambda: tab.current_path == dest and tab.current_doc is not None)
    assert tab.converted_pdf is None and not tab.converted_banner.isVisible()
    assert tab._editable()
    assert open(source, "rb").read() == before
    tab.cleanup()


def test_the_tab_updates_when_the_file_is_saved_elsewhere(window, tmp_path, fake):
    source = docx(tmp_path / "report.docx")
    tab = window._open_document(source, preview=False)
    assert wait_for(lambda: tab.current_doc is not None)
    tab._office_timer.setInterval(30)
    first = tab.converted_pdf
    docx(source, "Changed")
    os.utime(source, (time.time() + 5, time.time() + 5))
    assert wait_for(lambda: tab.converted_pdf not in (None, first) and tab.current_doc is not None)
    loads = []
    tab.load_document = lambda *args, **kwargs: loads.append(args)
    wait_for(lambda: False, 0.5)
    assert loads == []
    assert len(fake.calls()) == 2
    with patch.object(office, "open_in_office") as opened:
        with patch("riemann.ui.reader.mixins.document.open_in_office", opened):
            tab.edit_in_office()
    opened.assert_called_once()
    tab.cleanup()


def test_a_restored_tab_without_libreoffice_says_what_is_needed(window, tmp_path, monkeypatch):
    monkeypatch.setenv("RIEMANN_NO_OFFICE", "1")
    monkeypatch.setattr("riemann.ui.reader.mixins.document.packages_ready", lambda fmt: False)
    window._restore_tabs_from_items([{"type": "doc", "data": docx(tmp_path / "report.docx")}], window.tabs_main)
    tab = window.tabs_main.widget(window.tabs_main.count() - 1)
    page = []
    assert wait_for(lambda: tab.convert_worker is None and tab.web is not None)
    assert wait_for(lambda: (tab.web.page().toHtml(page.append), bool(page) and "needs LibreOffice" in page[-1])[1])
    assert tab.current_doc is None
    tab.cleanup()


def test_without_libreoffice_a_word_file_gets_the_basic_view(window, tmp_path, monkeypatch):
    from riemann.core.office_basic import BASIC_NOTE

    monkeypatch.setenv("RIEMANN_NO_OFFICE", "1")
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\nSECRETBYTES")
    window._restore_tabs_from_items(
        [{"type": "doc", "data": docx(tmp_path / "report.docx", "Basic words", external_image=str(secret))}],
        window.tabs_main,
    )
    tab = window.tabs_main.widget(window.tabs_main.count() - 1)
    page = []
    assert wait_for(lambda: tab.convert_worker is None and tab.web is not None)
    assert wait_for(lambda: (tab.web.page().toHtml(page.append), bool(page) and "Basic words" in page[-1])[1])
    assert BASIC_NOTE.split(",")[0] in page[-1]
    assert base64.b64encode(secret.read_bytes()).decode() not in page[-1]
    assert tab.converted_pdf is None and tab.current_path.endswith("report.docx")
    tab.cleanup()


def test_incognito_conversions_stay_out_of_the_cache(qtbot, tmp_path, fake):
    from riemann.app import RiemannWindow

    app_settings().setValue(OFFICE_PATH_KEY, fake.path)
    win = RiemannWindow(incognito=True, restore_session=False)
    qtbot.addWidget(win)
    try:
        tab = win._open_document(docx(tmp_path / "private.docx"), preview=False)
        assert wait_for(lambda: tab.current_doc is not None)
        folder = tab._office_folder
        assert folder and tab.converted_pdf.startswith(folder)
        assert not os.path.exists(office.files_dir()) or not os.listdir(office.files_dir())
        tab.cleanup()
        assert not os.path.exists(folder)
    finally:
        app_settings().remove(OFFICE_PATH_KEY)


def test_settings_finds_libreoffice_and_deletes_converted_copies(window, fake, tmp_path):
    from riemann.ui.system.settings_tab import SettingsTab

    OfficeConversion(docx(tmp_path / "a.docx"), office=fake.found).run()
    settings = SettingsTab(window)
    assert wait_for(lambda: settings.lbl_office.text() == fake.found.label)
    settings.clear_converted()
    assert not os.path.exists(office.cache_root())
    shutil.rmtree(tmp_path / "bin", ignore_errors=True)
