"""
Installing LibreOffice: the commands chosen for each system (built, never
run), the install dialog running plans in order with real child processes
that stand in for the installers, and the window asking only when it
should: once per kind of file, never for restored tabs, never after "Don't
ask again". When Riemann can read the file itself, the prompt offers the
basic view, which later files of that kind get without asking, and choosing
it without the packages offers to install them.
"""

import sys
import time
import zipfile
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from riemann.core import office
from riemann.core.endpoints import FLATHUB_REPO
from riemann.core.office import Office
from riemann.core.office_install import InstallPlan, install_plans, os_release
from riemann.core.settings import OFFICE_ASK_KEY, app_settings


def wait_for(condition, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def have(*tools):
    return lambda name: f"/usr/bin/{name}" if name in tools else None


def test_os_release_is_read_with_its_quotes_removed(tmp_path):
    path = tmp_path / "os-release"
    path.write_text('NAME="Rocky Linux"\nID="rocky"\nID_LIKE="rhel centos fedora"\n# comment\nVERSION_ID=10.2\n')
    assert os_release(str(path)) == {"NAME": "Rocky Linux", "ID": "rocky", "ID_LIKE": "rhel centos fedora", "VERSION_ID": "10.2"}
    assert os_release(str(tmp_path / "missing")) == {}


@pytest.mark.parametrize(
    "release,tool",
    [
        ({"ID": "fedora"}, "dnf"),
        ({"ID": "rocky", "ID_LIKE": "rhel centos fedora"}, "dnf"),
        ({"ID": "ubuntu", "ID_LIKE": "debian"}, "apt-get"),
        ({"ID": "neon", "ID_LIKE": "ubuntu debian"}, "apt-get"),
        ({"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"}, "zypper"),
        ({"ID": "manjaro", "ID_LIKE": "arch"}, "pacman"),
    ],
)
def test_linux_uses_the_system_packages_first_then_flatpak(release, tool):
    plans = install_plans("linux", release, have("pkexec", tool, "flatpak"))
    assert [len(p.steps) for p in plans] == [1, 2]
    assert plans[0].steps[0][:2] == ["pkexec", tool]
    assert "password" in plans[0].note
    remote, install = plans[1].steps
    assert remote == ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO]
    assert install[:3] == ["flatpak", "install", "--user"] and install[-1] == office.FLATPAK_ID


def test_without_pkexec_or_a_known_system_only_flatpak_is_offered():
    assert [p.name for p in install_plans("linux", {"ID": "fedora"}, have("dnf", "flatpak"))] == ["Flatpak from Flathub"]
    assert [p.name for p in install_plans("linux", {"ID": "gentoo"}, have("pkexec", "flatpak"))] == ["Flatpak from Flathub"]
    assert install_plans("linux", {"ID": "gentoo"}, have()) == []


def test_windows_uses_winget_with_the_installer_window_and_macos_the_website():
    (plan,) = install_plans("win32", {}, have("winget"))
    assert plan.steps[0][:5] == ["winget", "install", "--exact", "--id", "TheDocumentFoundation.LibreOffice"]
    assert "--interactive" in plan.steps[0]
    assert install_plans("win32", {}, have()) == []
    assert install_plans("darwin", {}, have("brew")) == []


def step(code: int, text: str):
    return [sys.executable, "-c", f"print({text!r}); raise SystemExit({code})"]


def test_the_dialog_runs_nothing_until_install_then_finds_libreoffice(qtbot, monkeypatch):
    from riemann.ui.office_install import InstallDialog

    found = Office(("/opt/lo/soffice",), "/opt/lo/soffice", "x")
    calls = []
    monkeypatch.setattr(office, "find_office", lambda override="": found if calls else None)
    monkeypatch.setattr(office, "refresh", lambda: calls.append("refresh"))
    plan = InstallPlan("Test packages", "Installs it.", [step(0, "one"), step(0, "two")])
    dialog = InstallDialog(None, [plan])
    qtbot.addWidget(dialog)
    dialog.show()
    assert "-c" in dialog.txt_commands.toPlainText()
    assert dialog.txt_output.toPlainText() == ""
    dialog.start()
    assert wait_for(lambda: dialog.found)
    output = dialog.txt_output.toPlainText()
    assert "one" in output and "two" in output and output.index("one") < output.index("two")
    assert calls == ["refresh"]


def test_a_failed_plan_offers_the_next_then_the_website(qtbot, monkeypatch):
    from riemann.ui.office_install import InstallDialog

    monkeypatch.setattr(office, "find_office", lambda override="": None)
    first = InstallPlan("System packages", "", [step(1, "no match"), step(0, "never runs")])
    second = InstallPlan("Flatpak from Flathub", "", [step(0, "done")])
    dialog = InstallDialog(None, [first, second])
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.start()
    assert wait_for(lambda: dialog.plan() is second)
    assert "code 1" in dialog.lbl_status.text() and "Flatpak from Flathub" in dialog.lbl_status.text()
    assert "never runs" not in dialog.txt_output.toPlainText()
    dialog.start()
    assert wait_for(lambda: dialog.plan() is None)
    assert "still can't find LibreOffice" in dialog.lbl_status.text()
    assert not dialog.btn_install.isVisibleTo(dialog) and dialog.btn_download.isVisibleTo(dialog)
    assert not dialog.found


def test_closing_the_dialog_kills_a_running_installer(qtbot, monkeypatch):
    from riemann.ui.office_install import InstallDialog

    monkeypatch.setattr(office, "find_office", lambda override="": None)
    dialog = InstallDialog(None, [InstallPlan("Slow", "", [[sys.executable, "-c", "import time; time.sleep(60)"]])])
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.start()
    process = dialog.process
    assert wait_for(lambda: process.processId() > 0)
    dialog.reject()
    assert process.state() == process.ProcessState.NotRunning


def make_docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return str(path)


@pytest.fixture
def window(qtbot, monkeypatch):
    from riemann.app import RiemannWindow

    monkeypatch.setattr(office, "find_office", lambda override="": None)
    app_settings().remove(OFFICE_ASK_KEY)
    win = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(win)
    yield win
    app_settings().remove(OFFICE_ASK_KEY)


def test_the_window_asks_once_per_kind_and_opens_nothing_when_declined(window, tmp_path, monkeypatch):
    monkeypatch.setattr("riemann.window.documents.packages_ready", lambda fmt: False)
    with patch("riemann.ui.office_install.offer_office", return_value="") as offer:
        assert window._open_document(make_docx(tmp_path / "a.docx"), preview=False) is None
        assert window._open_document(make_docx(tmp_path / "b.docx"), preview=False) is None
        assert offer.call_count == 1
        assert window._open_document(make_docx(tmp_path / "c.pptx"), preview=False) is None
        assert offer.call_count == 2
    assert not window.history_manager.history.get("pdf") or all(
        not str(entry).endswith((".docx", ".pptx")) for entry in window.history_manager.history.get("pdf", [])
    )


def test_dont_ask_again_and_restored_tabs_never_ask(window, tmp_path):
    from riemann.ui.office_install import OfficePrompt

    prompt = OfficePrompt(window, "a.docx")
    prompt.cb_dont_ask.setChecked(True)
    prompt.reject()
    assert app_settings().value(OFFICE_ASK_KEY, True, type=bool) is False
    with patch("riemann.ui.office_install.offer_office") as offer:
        basic = window._open_document(make_docx(tmp_path / "a.docx"), preview=False)
        window._restore_tabs_from_items([{"type": "doc", "data": make_docx(tmp_path / "b.docx")}], window.tabs_main)
    offer.assert_not_called()
    restored = window.tabs_main.widget(window.tabs_main.count() - 1)
    assert restored.current_path.endswith("b.docx")
    restored.cleanup()
    assert basic is not None
    basic.cleanup()


def test_installing_from_the_prompt_opens_the_file(window, tmp_path, monkeypatch):
    found = Office((sys.executable,), "fake", "fake")
    installed = []
    monkeypatch.setattr(office, "find_office", lambda override="": found if installed else None)

    def offer(parent, path, basic=False):
        installed.append(path)
        return "office"

    with patch("riemann.ui.office_install.offer_office", side_effect=offer):
        tab = window._open_document(make_docx(tmp_path / "a.docx"), preview=False)
    assert tab is not None and installed
    tab.cleanup()


def test_the_prompt_offers_the_basic_view_only_when_riemann_can_read_the_file(qtbot):
    from riemann.ui.office_install import OfficePrompt

    plain = OfficePrompt(None, "a.vsd")
    qtbot.addWidget(plain)
    assert plain.btn_basic is None
    prompt = OfficePrompt(None, "a.docx", basic=True)
    qtbot.addWidget(prompt)
    prompt.btn_basic.click()
    assert prompt.choice == "basic" and prompt.result() == prompt.DialogCode.Accepted


def test_the_basic_view_is_asked_once_then_opens_without_asking(window, tmp_path):
    with patch("riemann.ui.office_install.offer_office", return_value="basic") as offer:
        first = window._open_document(make_docx(tmp_path / "a.docx"), preview=False)
        second = window._open_document(make_docx(tmp_path / "b.docx"), preview=False)
    assert offer.call_count == 1 and offer.call_args.kwargs == {"basic": True}
    assert first is not None and second is not None
    first.cleanup()
    second.cleanup()


def test_choosing_the_basic_view_without_its_packages_offers_them(window, tmp_path, monkeypatch):
    ready = []
    monkeypatch.setattr("riemann.window.documents.packages_ready", lambda fmt: bool(ready))

    def install(parent):
        ready.append(True)
        return True

    with patch("riemann.ui.office_install.offer_office", return_value="basic"), patch(
        "riemann.ui.office_install.offer_basic_packages", side_effect=install
    ) as packages:
        tab = window._open_document(make_docx(tmp_path / "a.docx"), preview=False)
    assert packages.call_count == 1 and tab is not None
    tab.cleanup()
    with patch("riemann.ui.office_install.offer_office", return_value="basic"), patch(
        "riemann.ui.office_install.offer_basic_packages", return_value=False
    ):
        ready.clear()
        assert window._open_document(make_docx(tmp_path / "b.pptx"), preview=False) is None


def test_offer_basic_packages_installs_from_the_lock(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from riemann.core import dependencies
    from riemann.ui import office_install

    names = []

    class Worker(dependencies.DependencyWorker):
        def run(self):
            names.append(self.pip_name)
            self.finished_task.emit(self.pip_name, self.action, True)

    monkeypatch.setattr(dependencies, "DependencyWorker", Worker)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert office_install.offer_basic_packages(None) is True
    assert names == [dependencies.OFFICE_VIEWERS_LOCK]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert office_install.offer_basic_packages(None) is False and len(names) == 1
