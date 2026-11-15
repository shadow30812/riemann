"""Tests for the command palette in riemann/ui/command_palette.py."""

from PySide6.QtWidgets import QMainWindow, QPushButton, QVBoxLayout, QWidget

from riemann.ui.command_palette import CommandPalette, menu_commands, score, toolbar_commands


def test_score_prefers_word_starts_and_runs():
    assert score("tsv", "Toggle Split View") > score("tsv", "Testing several values")
    assert score("split", "Toggle Split View") > 0
    assert score("xyz", "Toggle Split View") == 0
    assert score("", "anything") == 1.0


def test_menu_actions_become_commands_with_keys(qapp):
    window = QMainWindow()
    menu = window.menuBar().addMenu("&View")
    fired = []
    menu.addAction("Toggle Split View (Ctrl+\\\\)").triggered.connect(lambda: fired.append("split"))
    sub = menu.addMenu("Recent")
    sub.addAction("paper.pdf")
    disabled = menu.addAction("Nope")
    disabled.setEnabled(False)
    commands = {c.title: c for c in menu_commands(window.menuBar())}
    assert set(commands) == {"Toggle Split View", "paper.pdf"}
    assert commands["Toggle Split View"].keys == ("Ctrl", "\\\\")
    assert commands["paper.pdf"].group == "View › Recent"
    commands["Toggle Split View"].run()
    assert fired == ["split"]


def test_toolbar_buttons_become_commands(qapp):
    tab = QWidget()
    layout = QVBoxLayout(tab)
    a = QPushButton()
    a.setToolTip("OCR Current Page")
    b = QPushButton()
    b.setToolTip("Print Document (Ctrl+P)")
    c = QPushButton()
    layout.addWidget(a)
    layout.addWidget(b)
    layout.addWidget(c)
    tab.show()
    commands = {cmd.title: cmd for cmd in toolbar_commands(tab, "Document")}
    assert set(commands) == {"OCR Current Page", "Print Document"}
    assert commands["Print Document"].keys == ("Ctrl", "P")


def test_palette_runs_the_best_match(qapp):
    window = QMainWindow()
    window.resize(900, 600)
    menu = window.menuBar().addMenu("File")
    fired = []
    menu.addAction("Merge PDFs").triggered.connect(lambda: fired.append("merge"))
    menu.addAction("Split Current PDF").triggered.connect(lambda: fired.append("split"))
    window.show()
    window.open_system_page = lambda *a: fired.append(a)
    palette = CommandPalette(window)
    palette.open()
    palette.input.setText("mrg")
    palette.run_current()
    assert fired == ["merge"]
    assert not palette.isVisible()
