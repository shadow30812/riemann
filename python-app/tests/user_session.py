"""
Drives the real app the way a person does, for tests/test_user_sessions.py.

Run as a script: python user_session.py SCENARIO WORKDIR [FILE...]. It starts
Riemann through riemann.app.run (the same entry point as the desktop launcher,
with FILE... on its command line and --new-window, so it never hands files to
a Riemann the developer has open), then plays SCENARIO once the window is
shown. The caller sets HOME and the XDG folders to throwaway ones and
QT_QPA_PLATFORM=offscreen.

A scenario is a generator. It acts through real input (QTest keystrokes, mouse
presses, drags and right-clicks on the widgets a person would use, buttons in
the dialogs that come up) and yields what it waits for: a condition (a
callable, waited on for up to WAIT seconds) or a number of seconds. Anything
that opens a modal dialog or a menu is started with later(), because the
dialog runs its own event loop and the scenario must keep stepping inside it.
Observations go into the dict notes, written to WORKDIR/result.json when the
scenario ends; the app then quits through Ctrl+Q, so the session is saved as
it would be. An exception, a condition that never comes true, or the app
hanging past DEADLINE writes the error there instead and exits non-zero; a
hang also prints every thread's stack to stderr.
"""

import faulthandler
import json
import os
import sys
import threading
import time
import traceback

WAIT = 20.0
DEADLINE = 150.0

notes: dict = {}
state = {"app": None, "window": None, "workdir": None, "done": False}


def finish(error=None):
    if state["done"]:
        return
    state["done"] = True
    if error:
        notes["error"] = error
    with open(os.path.join(state["workdir"], "result.json"), "w") as f:
        json.dump(notes, f, indent=2, default=str)


def watchdog():
    time.sleep(DEADLINE)
    finish(f"the app did not finish within {DEADLINE:.0f} s")
    faulthandler.dump_traceback(all_threads=True)
    os._exit(3)


def later(fn, *args):
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, lambda: fn(*args))


def window():
    return state["window"]


def current_tab():
    return window().tabs_main.currentWidget()


def reader_ready(path=None):
    from riemann.ui.reader.tab import ReaderTab

    def ready():
        tab = current_tab()
        if not isinstance(tab, ReaderTab) or tab.current_doc is None:
            return False
        if path and os.path.abspath(tab.current_path or "") != os.path.abspath(path):
            return False
        page = tab.page_widgets.get(tab.current_page_index)
        return page is not None and getattr(page, "has_full_page", False)

    return ready


def key(sequence, widget=None):
    """Presses a shortcut such as "Ctrl+Shift+A" on widget (the focused widget by default)."""
    from PySide6.QtGui import QKeySequence
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    target = widget or QApplication.focusWidget() or window()
    top = target.window()
    if QApplication.activeModalWidget() is None and QApplication.activeWindow() is not top:
        top.activateWindow()
        QApplication.processEvents()
    combo = QKeySequence(sequence)[0]
    QTest.keyClick(target, combo.key(), combo.keyboardModifiers())


def type_text(text, widget=None):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    QTest.keyClicks(widget or QApplication.focusWidget(), text)


def word_rect(tab, word, page=0):
    """Where word is on a page widget, in its pixels: (left, top, right, bottom)."""
    boxes = tab.current_doc.get_char_boxes(page)
    text = "".join(c for c, _ in boxes)
    start = text.index(word)
    chosen = [b for _, b in boxes[start : start + len(word)]]
    page_w, page_h = tab.current_doc.get_page_size(page)
    widget = tab.page_widgets[page]
    sx, sy = widget.width() / page_w, widget.height() / page_h
    return (
        min(b[0] for b in chosen) * sx,
        (page_h - max(b[1] for b in chosen)) * sy,
        max(b[2] for b in chosen) * sx,
        (page_h - min(b[3] for b in chosen)) * sy,
    )


def select_word(tab, word, page=0):
    """
    Selects word by double-clicking it, as a person would. (A drag can't be
    played here: QTest's mouse moves never arrive on the offscreen platform.)
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    l, t, r, b = word_rect(tab, word, page)
    centre = QPoint(int((l + r) / 2), int((t + b) / 2))
    QTest.mouseDClick(tab.page_widgets[page], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
    QApplication.processEvents()


def right_click(widget, point):
    """A right-click's context menu event (QTest can't press the right button so that Qt makes one)."""
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QApplication

    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, widget.mapToGlobal(point))
    later(QApplication.sendEvent, widget, event)


def popup_menu():
    from PySide6.QtWidgets import QApplication, QMenu

    popup = QApplication.activePopupWidget()
    return popup if isinstance(popup, QMenu) else None


def choose(menu, text):
    """Clicks the menu entry whose text is text."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    action = next(a for a in menu.actions() if a.text() == text)
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, menu.actionGeometry(action).center())


def dialog(kind=None):
    from PySide6.QtWidgets import QApplication

    def found():
        modal = QApplication.activeModalWidget()
        if modal is None or not modal.isVisible():
            return None
        return modal if kind is None or isinstance(modal, kind) else None

    return found


def button(box, label):
    """The dialog button labelled label (mnemonic ampersands ignored)."""
    from PySide6.QtWidgets import QAbstractButton

    return next(b for b in box.findChildren(QAbstractButton) if b.isVisible() and b.text().replace("&", "") == label)


def press(box, label):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(button(box, label), Qt.MouseButton.LeftButton)


def click(widget):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def open_with_dialog(path):
    """Ctrl+O, then types path into the file dialog and clicks Open; returns False when Open stays disabled (and cancels)."""
    from PySide6.QtWidgets import QFileDialog, QLineEdit

    later(key, "Ctrl+O", window())
    box = yield dialog(QFileDialog)
    yield 0.5
    name = box.findChild(QLineEdit, "fileNameEdit")
    name.setFocus()
    type_text(path, name)
    yield lambda: name.text() == path
    yield 0.2
    offered = button(box, "Open").isEnabled()
    press(box, "Open" if offered else "Cancel")
    yield lambda: dialog(QFileDialog)() is None
    return offered


def scenario_read(files):
    """Opens a paper from the command line, finds a word, double-clicks it and copies it."""
    from PySide6.QtWidgets import QApplication

    yield reader_ready(files[0])
    tab = current_tab()
    tab.setFocus()
    key("Ctrl+F", tab)
    yield lambda: tab.search_bar.isVisible()
    tab.txt_search.setFocus()
    type_text("SECRET", tab.txt_search)
    key("Return", tab.txt_search)
    yield lambda: tab.search_result is not None
    notes["search_found"] = True
    key("Escape", tab.txt_search)
    select_word(tab, "SECRET")
    yield lambda: bool(tab.current_selected_text)
    key("Ctrl+C", tab.page_widgets[0])
    notes["selected"] = tab.current_selected_text
    notes["clipboard"] = QApplication.clipboard().text()


def scenario_redact(files):
    """Selects a word, marks it for redaction from the right-click menu, applies it and saves on closing the tab."""
    import shiboken6

    yield reader_ready(files[0])
    tab = current_tab()
    select_word(tab, "SECRET")
    yield lambda: bool(tab.current_selected_text)
    from PySide6.QtCore import QPoint

    l, t, r, b = word_rect(tab, "SECRET")
    right_click(tab.page_widgets[0], QPoint(int((l + r) / 2), int((t + b) / 2)))
    yield popup_menu
    choose(popup_menu(), "Mark for Redaction")
    yield lambda: any(a.get("type") == "redact" for a in tab.annotations.get("0", []))
    key("Ctrl+Shift+A", tab)
    yield lambda: tab.anno_toolbar.btn_apply_redactions.isVisible()
    later(click, tab.anno_toolbar.btn_apply_redactions)
    yield dialog()
    press(dialog()(), "Apply")
    yield lambda: tab.has_unsaved_page_edits() and tab.current_doc is not None
    notes["word_kept_until_save"] = b"SECRET" in stream_text(files[0])
    later(key, "Ctrl+W", window())
    yield dialog()
    notes["close_asked"] = dialog()().text()
    press(dialog()(), "Save")
    yield lambda: not shiboken6.isValid(tab) or window().tabs_main.indexOf(tab) == -1


def scenario_open_text(files):
    """
    Opens a text file through Ctrl+O, then an image the same way, steps to the
    next image in its folder with Right, then looks both files up in History.
    """
    text_file, image, next_image = files
    yield from open_with_dialog(text_file)
    yield lambda: getattr(current_tab(), "text_document", False) and current_tab().current_path == text_file
    notes["text_view"] = True
    notes["image_offered"] = yield from open_with_dialog(image)
    yield lambda: getattr(current_tab(), "decoded", None) is not None
    tab = current_tab()
    notes["image_size"] = [tab.decoded.width, tab.decoded.height]
    key("Right", tab)
    yield lambda: tab.current_path == next_image and tab.decoded is not None
    tabs = window().tabs_main
    notes["stepped_title"] = tabs.tabText(tabs.indexOf(tab))
    key("Ctrl+H", window())
    yield lambda: getattr(current_tab(), "page", None) == "history"
    page = current_tab()
    tree = page.trees["pdf"]
    notes["history"] = {tree.topLevelItem(i).text(0): tree.topLevelItem(i).text(1) for i in range(tree.topLevelItemCount())}


def scenario_quit_with_edits(files):
    """Rotates a page from the Pages menu, presses Ctrl+Q while the change waits for Save, and saves when asked."""
    yield reader_ready(files[0])
    tab = current_tab()
    later(tab.show_pages_menu)
    yield popup_menu
    choose(popup_menu(), "Rotate page 1 clockwise")
    yield lambda: tab.has_unsaved_page_edits() and tab.current_doc is not None
    notes["file_unchanged_before_quit"] = open(files[0], "rb").read() == state["original"]
    later(key, "Ctrl+Q", window())
    box = None
    end = time.time() + 5
    while time.time() < end and box is None:
        box = dialog()()
        yield 0.05
    notes["quit_asked"] = box.text() if box is not None else None
    if box is not None:
        press(box, "Save")
    state["quitting"] = True


def browser_tab(path):
    """The browser tab showing the local file path, once its page has loaded."""
    from riemann.ui.browser import BrowserTab

    def loaded():
        for i in range(window().tabs_main.count()):
            tab = window().tabs_main.widget(i)
            if isinstance(tab, BrowserTab) and tab.web.url().toLocalFile() == os.path.abspath(path) and tab.web.title():
                return tab
        return None

    return loaded


def type_into_page(tab, text):
    """Clicks the page's top-left corner (where the test pages put a text box) and types text."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    target = tab.web.focusProxy() or tab.web
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 20))
    QTest.keyClicks(target, text)


def answer_leave_question(label):
    """Waits for the Leave page? question, notes its text and clicks label."""
    from PySide6.QtWidgets import QMessageBox

    box = yield dialog(QMessageBox)
    text = box.text()
    press(box, label)
    return text


def scenario_leave_site(files):
    """
    Types into two pages that warn before they are left. Ctrl+W on the first
    is answered Stay, then Leave; Ctrl+Q with the second open is answered
    Stay, then Leave.
    """
    import shiboken6

    first = yield browser_tab(files[0])
    second = yield browser_tab(files[1])
    yield 1.0
    window().tabs_main.setCurrentWidget(first)
    type_into_page(first, "draft")
    yield 0.5
    later(key, "Ctrl+W", window())
    notes["close_asked"] = yield from answer_leave_question("Stay")
    yield 0.5
    notes["stayed_open"] = window().tabs_main.indexOf(first) != -1
    later(key, "Ctrl+W", window())
    yield from answer_leave_question("Leave")
    yield lambda: not shiboken6.isValid(first) or window().tabs_main.indexOf(first) == -1
    yield 0.5
    notes["asked_again"] = dialog()() is not None
    window().tabs_main.setCurrentWidget(second)
    type_into_page(second, "draft")
    yield 0.5
    later(key, "Ctrl+Q", window())
    notes["quit_asked"] = yield from answer_leave_question("Stay")
    yield 0.5
    notes["running_after_stay"] = window().isVisible() and window().tabs_main.indexOf(second) != -1
    later(key, "Ctrl+Q", window())
    yield from answer_leave_question("Leave")
    state["quitting"] = True


def save_dialog(path):
    """Waits for a Save dialog, types path into it and clicks Save."""
    from PySide6.QtWidgets import QFileDialog, QLineEdit

    box = yield dialog(QFileDialog)
    yield 0.5
    name = box.findChild(QLineEdit, "fileNameEdit")
    name.setFocus()
    name.selectAll()
    type_text(path, name)
    yield lambda: name.text() == path
    press(box, "Save")
    yield lambda: dialog(QFileDialog)() is None


def highlight_word(tab, word, page=0):
    """Text Highlight from the annotation tools, pressed at the word's start and released at its end."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    key("Ctrl+Shift+A", tab)
    yield lambda: tab.anno_toolbar.isVisible()
    click(tab.anno_toolbar.btn_markup_h)
    yield lambda: tab.current_tool == "markup_highlight"
    l, t, r, b = word_rect(tab, word, page)
    middle = int((t + b) / 2)
    widget = tab.page_widgets[page]
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(l) + 1, middle))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(r) - 1, middle))
    yield lambda: any(a.get("type") == "markup" for a in tab.annotations.get(str(page), []))


def scenario_office(files):
    """
    Opens a Word file from the command line (LibreOffice converts it), saves it
    as a PDF with Ctrl+S, highlights a word in that PDF and quits.
    """
    docx, pdf = files
    yield reader_ready(docx)
    tab = current_tab()
    notes["banner"] = tab.converted_banner is not None and tab.converted_banner.isVisible()
    notes["annotations_blocked"] = not tab.anno_toolbar.isVisible()
    later(key, "Ctrl+S", tab)
    yield from save_dialog(pdf)
    yield reader_ready(pdf)
    notes["banner_after_save"] = tab.converted_banner is not None and tab.converted_banner.isVisible()
    yield from highlight_word(tab, "Hello")
    notes["docx_unchanged"] = open(docx, "rb").read() == state["original"]
    later(key, "Ctrl+Q", window())
    state["quitting"] = True


def page_text(tab):
    """The text of the page in tab's web view."""
    from PySide6.QtWidgets import QApplication

    found = []
    tab.web.page().toPlainText(found.append)
    end = time.time() + 5
    while not found and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return found[0] if found else ""


def scenario_office_basic(files):
    """
    Without LibreOffice: a Word file from the command line brings the install
    prompt, answered "Use the basic view"; a second Word file through Ctrl+O
    then opens the same way without asking.
    """
    from PySide6.QtWidgets import QFileDialog, QLabel, QLineEdit

    from riemann.ui.office_install import OfficePrompt

    first, second = files
    box = yield dialog(OfficePrompt)
    notes["prompt"] = box.findChildren(QLabel)[0].text()
    press(box, "Use the basic view")
    yield lambda: getattr(current_tab(), "current_path", None) == first and current_tab().web is not None
    tab = current_tab()
    yield lambda: "First words" in page_text(tab)
    notes["first_text"] = page_text(tab)
    later(key, "Ctrl+O", window())
    picker = yield dialog(QFileDialog)
    yield 0.5
    name = picker.findChild(QLineEdit, "fileNameEdit")
    name.setFocus()
    type_text(second, name)
    yield lambda: name.text() == second
    press(picker, "Open")
    yield lambda: dialog(QFileDialog)() is None
    yield 1.0
    notes["asked_again"] = isinstance(dialog()(), OfficePrompt)
    yield lambda: getattr(current_tab(), "current_path", None) == second and current_tab().web is not None
    tab = current_tab()
    yield lambda: "Second words" in page_text(tab)
    notes["second_text"] = True


def table_view():
    from riemann.ui.viewers import TableTab

    tab = current_tab()
    return tab if isinstance(tab, TableTab) and tab.model is not None and tab.model.rowCount() else None


def edit_cell(tab, row, column, text):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    tab.view.setCurrentIndex(tab.model.index(row, column))
    tab.view.setFocus()
    key("F2", tab.view)
    yield lambda: tab.cell_editor() is not None
    editor = tab.cell_editor()
    editor.selectAll()
    type_text(text, editor)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    yield lambda: tab.model.cell(row, column) == text


def scenario_edit_table(files):
    """
    Changes a cell of a CSV file and saves it with Ctrl+S, answering Save;
    changes another, then quits with Ctrl+Q and answers Don't Save.
    """
    from PySide6.QtWidgets import QMessageBox

    tab = yield table_view
    yield from edit_cell(tab, 0, 1, "30")
    notes["unchanged_before_save"] = open(files[0], "rb").read() == state["original"]
    later(key, "Ctrl+S", tab)
    box = yield dialog(QMessageBox)
    notes["save_asked"] = box.text()
    press(box, "Save")
    yield lambda: not tab.modified
    notes["after_save"] = open(files[0], encoding="utf-8").read()
    yield from edit_cell(tab, 1, 1, "99")
    later(key, "Ctrl+Q", window())
    box = yield dialog(QMessageBox)
    notes["quit_asked"] = box.text()
    press(box, "Don't Save")
    state["quitting"] = True


SCENARIOS = {
    "read": scenario_read,
    "redact": scenario_redact,
    "open_text": scenario_open_text,
    "quit_with_edits": scenario_quit_with_edits,
    "leave_site": scenario_leave_site,
    "office": scenario_office,
    "office_basic": scenario_office_basic,
    "edit_table": scenario_edit_table,
}


def open_dialog_text():
    from PySide6.QtWidgets import QApplication, QMessageBox

    modal = QApplication.activeModalWidget()
    if modal is None:
        return ""
    text = modal.text() if isinstance(modal, QMessageBox) else modal.windowTitle()
    return f" (a {type(modal).__name__} was open: {text!r})"


def stream_text(path):
    import pikepdf

    with pikepdf.open(path) as pdf:
        return b"".join(o.read_bytes() for o in pdf.objects if isinstance(o, pikepdf.Stream))


def play(name, files):
    from PySide6.QtCore import QTimer

    steps = SCENARIOS[name](files)
    pending = {"cond": None, "until": 0.0, "busy": False}
    timer = QTimer()

    def fail(message):
        timer.stop()
        finish(message)
        state["app"].exit(1)

    def step():
        if pending["busy"] or state["done"]:
            return
        cond, until = pending["cond"], pending["until"]
        if cond is not None:
            try:
                ready = cond()
            except Exception:
                return fail(traceback.format_exc())
            if not ready:
                if time.time() > until:
                    fail(f"waited {WAIT:.0f} s for {getattr(cond, '__qualname__', cond)}{open_dialog_text()}")
                return
        elif time.time() < until:
            return
        pending["busy"] = True
        try:
            wanted = steps.send(cond() if cond is not None else None)
        except StopIteration:
            timer.stop()
            finish()
            if not state.get("quitting"):
                later(key, "Ctrl+Q", window())
            return
        except Exception:
            return fail(traceback.format_exc())
        finally:
            pending["busy"] = False
        if callable(wanted):
            pending["cond"], pending["until"] = wanted, time.time() + WAIT
        else:
            pending["cond"], pending["until"] = None, time.time() + float(wanted or 0)

    timer.timeout.connect(step)
    timer.start(20)
    state["timer"] = timer


def main():
    name, workdir, files = sys.argv[1], sys.argv[2], sys.argv[3:]
    state["workdir"] = workdir
    state["original"] = open(files[0], "rb").read() if files and os.path.isfile(files[0]) else None
    threading.Thread(target=watchdog, daemon=True).start()

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import riemann.app as app_module
    from PySide6.QtWidgets import QApplication

    app_module.CHROMIUM_FLAGS += " --disable-gpu"
    shown = app_module.RiemannWindow.show

    def show(self):
        shown(self)
        if state["window"] is None:
            state["window"] = self
            state["app"] = QApplication.instance()
            self.activateWindow()
            later(play, name, files)

    app_module.RiemannWindow.show = show
    command_files = {"open_text": [], "office": files[:1], "office_basic": files[:1]}.get(name, files)
    sys.argv = [sys.argv[0], "--new-window", *command_files]
    try:
        app_module.run()
    finally:
        if not state["done"]:
            finish("the app quit before the scenario finished")


if __name__ == "__main__":
    main()
