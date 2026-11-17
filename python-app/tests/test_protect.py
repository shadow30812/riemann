"""
Encrypted and signed PDFs in the reader: the Protect button turns into Remove
password on an encrypted file, removing it goes through the scratch copy, and
the first edit of a signed file asks before going ahead.
"""

import os
import sys
import time
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from riemann.core import annotation_store, page_edit
from riemann.ui.reader.tab import ReaderTab

PASSWORD = "secret"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def reader(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(annotation_store, "_shared", annotation_store.AnnotationStore(str(tmp_path / "store")))
    settings = MagicMock()
    settings.return_value.value.side_effect = lambda key, default=None, type=None: default
    with patch("riemann.ui.reader.tab.app_settings", settings), patch(
        "riemann.ui.reader.mixins.appearance.app_settings", settings
    ):
        tab = ReaderTab()
        tab.resize(900, 700)
        tab.show()
        yield tab
        tab.cleanup()
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pump(qapp, until, seconds=15):
    end = time.time() + seconds
    while time.time() < end and not until():
        qapp.processEvents()
        time.sleep(0.01)
    assert until()


def make_pdf(path, pages=3, password=None, signed=False):
    with pikepdf.new() as pdf:
        for _ in range(pages):
            pdf.add_blank_page(page_size=(300, 400))
        if signed:
            pdf.Root.AcroForm = pikepdf.Dictionary(SigFlags=3, Fields=pikepdf.Array())
        encryption = pikepdf.Encryption(user=password, owner=password) if password else False
        pdf.save(path, encryption=encryption)
    return str(path)


def answer(monkeypatch, label):
    """Makes the next message boxes click the button whose text is label (Cancel for None)."""
    asked = []

    def exec_(box):
        asked.append(box.text())
        return 0

    def clicked(box):
        for button in box.buttons():
            if label is not None and button.text() == label:
                return button
            if label is None and box.buttonRole(button) == QMessageBox.ButtonRole.RejectRole:
                return button
        return None

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec", exec_)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.clickedButton", clicked)
    return asked


def open_pdf(qapp, reader, path, password=None):
    reader.load_document(path, password)
    pump(qapp, lambda: reader.current_doc is not None)


def is_locked(path):
    try:
        pikepdf.open(path).close()
    except pikepdf.PasswordError:
        return True
    return False


def test_the_protect_button_offers_remove_password_on_an_encrypted_pdf(qapp, reader, tmp_path):
    plain = make_pdf(tmp_path / "plain.pdf")
    locked = make_pdf(tmp_path / "locked.pdf", password=PASSWORD)
    button = reader.btn_secure_export

    open_pdf(qapp, reader, plain)
    assert "password-protected copy" in button.toolTip()
    assert button.property("riemannLabel") is None

    reader.load_document(locked, PASSWORD)
    pump(qapp, lambda: reader.current_path == locked and reader.current_doc is not None)
    assert reader.document_encrypted is True
    assert button.toolTip().startswith("Remove the password")
    assert button.property("riemannLabel") == "Remove password…"
    menu_texts = []
    menu = MagicMock()
    menu.addAction.side_effect = lambda icon, text: menu_texts.append(text) or MagicMock()
    reader.toolbar_layout.add_command_action(menu, "secure_export")
    assert menu_texts[0].startswith("Remove password…")


def test_removing_the_password_from_the_file_waits_for_save(qapp, reader, tmp_path, monkeypatch):
    locked = make_pdf(tmp_path / "locked.pdf", password=PASSWORD)
    open_pdf(qapp, reader, locked, PASSWORD)
    answer(monkeypatch, "Remove from this file")

    reader.remove_password()
    pump(qapp, lambda: reader.current_doc is not None and not reader.document_encrypted)
    assert reader.has_unsaved_page_edits()
    assert is_locked(locked)
    assert "password-protected copy" in reader.btn_secure_export.toolTip()
    assert reader.btn_secure_export.property("riemannLabel") is None

    assert reader.save_page_edits()
    pump(qapp, lambda: reader.current_doc is not None)
    assert not is_locked(locked)
    with pikepdf.open(locked) as pdf:
        assert len(pdf.pages) == 3


def test_discarding_a_removed_password_keeps_the_file_locked(qapp, reader, tmp_path, monkeypatch):
    locked = make_pdf(tmp_path / "locked.pdf", password=PASSWORD)
    before = open(locked, "rb").read()
    open_pdf(qapp, reader, locked, PASSWORD)
    answer(monkeypatch, "Remove from this file")
    reader.remove_password()
    pump(qapp, lambda: reader.current_doc is not None and not reader.document_encrypted)

    reader.discard_page_edits()
    reader._reopen_at(0)
    pump(qapp, lambda: reader.current_doc is not None and reader.document_encrypted)
    assert open(locked, "rb").read() == before
    assert reader.btn_secure_export.property("riemannLabel") == "Remove password…"


def test_an_unlocked_copy_is_written_at_once(qapp, reader, tmp_path, monkeypatch):
    locked = make_pdf(tmp_path / "locked.pdf", password=PASSWORD)
    out = str(tmp_path / "copy.pdf")
    open_pdf(qapp, reader, locked, PASSWORD)
    answer(monkeypatch, "Save an unlocked copy…")
    with patch("riemann.ui.reader.mixins.document.QFileDialog.getSaveFileName", return_value=(out, "")):
        reader.remove_password()
    assert not is_locked(out)
    assert is_locked(locked)
    assert not reader.has_unsaved_page_edits()


def test_page_edits_on_an_encrypted_pdf_keep_its_password(qapp, reader, tmp_path):
    locked = make_pdf(tmp_path / "locked.pdf", password=PASSWORD)
    open_pdf(qapp, reader, locked, PASSWORD)
    assert reader._edit_pages(page_edit.rotate_pages, [0], 90)
    pump(qapp, lambda: reader.current_doc is not None)
    assert is_locked(reader.pdf_path())
    assert reader.save_page_edits()
    pump(qapp, lambda: reader.current_doc is not None)
    with pikepdf.open(locked, password=PASSWORD) as pdf:
        assert int(pdf.pages[0].obj.get("/Rotate", 0)) == 90


def test_cancelling_the_signed_warning_leaves_the_file_alone(qapp, reader, tmp_path, monkeypatch):
    signed = make_pdf(tmp_path / "signed.pdf", signed=True)
    before = open(signed, "rb").read()
    open_pdf(qapp, reader, signed)
    assert reader.document_signed is True
    asked = answer(monkeypatch, None)

    assert reader._edit_pages(page_edit.delete_pages, [0]) is False
    assert asked and "digitally signed" in asked[0]
    assert not reader.has_unsaved_page_edits()
    assert open(signed, "rb").read() == before


def test_a_signed_pdf_is_edited_after_the_warning_which_comes_once(qapp, reader, tmp_path, monkeypatch):
    signed = make_pdf(tmp_path / "signed.pdf", signed=True)
    open_pdf(qapp, reader, signed)
    asked = answer(monkeypatch, "Edit anyway")

    assert reader._edit_pages(page_edit.delete_pages, [0])
    pump(qapp, lambda: reader.current_doc is not None)
    assert reader._edit_pages(page_edit.rotate_pages, [0], 90)
    pump(qapp, lambda: reader.current_doc is not None)
    assert len(asked) == 1
    with pikepdf.open(reader.pdf_path()) as pdf:
        assert len(pdf.pages) == 2


def test_the_signed_warning_comes_again_for_another_file(qapp, reader, tmp_path, monkeypatch):
    first = make_pdf(tmp_path / "first.pdf", signed=True)
    second = make_pdf(tmp_path / "second.pdf", signed=True)
    open_pdf(qapp, reader, first)
    asked = answer(monkeypatch, "Edit anyway")
    assert reader._edit_pages(page_edit.rotate_pages, [0], 90)
    pump(qapp, lambda: reader.current_doc is not None)
    reader.save_page_edits()
    pump(qapp, lambda: reader.current_doc is not None)

    reader.load_document(second)
    pump(qapp, lambda: reader.current_path == second and reader.current_doc is not None)
    assert reader._edit_pages(page_edit.rotate_pages, [0], 90)
    assert len(asked) == 2
    assert os.path.exists(second)
