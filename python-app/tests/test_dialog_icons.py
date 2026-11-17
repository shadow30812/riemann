"""
Dialogs carry a Lucide icon tinted for the theme of the window they belong
to (ui/palette.dialog_icon): the theme is a stylesheet on the window, so the
icon follows the window's dark_mode, not the application palette.
"""

from PySide6.QtWidgets import QDialog, QLabel, QMainWindow, QMessageBox, QVBoxLayout

from riemann.ui.favourites import ManageFavoritesDialog
from riemann.ui.office_install import OfficePrompt
from riemann.ui.palette import dialog_icon, tokens, window_is_dark


def window(qtbot, dark):
    win = QMainWindow()
    win.dark_mode = dark
    qtbot.addWidget(win)
    return win


def badge_colour(dialog):
    image = dialog.findChild(QLabel, "dialogIcon").pixmap().toImage()
    colours = {
        image.pixelColor(x, y).name().upper()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() == 255
    }
    return colours


def test_the_icon_follows_the_window_theme_not_the_application(qtbot):
    for dark in (True, False):
        dialog = QDialog(window(qtbot, dark))
        QVBoxLayout(dialog).addWidget(QLabel("text"))
        dialog_icon(dialog, "file-cog")
        assert window_is_dark(dialog) is dark
        assert badge_colour(dialog) == {tokens(dark)["accent"].upper()}
        assert dialog.layout().itemAt(0).widget().objectName() == "dialogIcon"


def test_a_message_box_shows_it_beside_the_text(qtbot):
    box = QMessageBox(window(qtbot, True))
    dialog_icon(box, "shield-alert", "warning")
    assert not box.iconPixmap().isNull()
    assert not box.windowIcon().isNull()


def test_settings_dialogs_carry_their_cog(qtbot):
    parent = window(qtbot, False)
    for dialog in (OfficePrompt(parent, "report.docx"), ManageFavoritesDialog(parent)):
        assert dialog.findChild(QLabel, "dialogIcon") is not None
