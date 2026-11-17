"""
Takes the screenshots in docs/images/ from the running app.

Run from the repository root (after benchmarks/fetch_corpus.py, which
downloads the PDFs the shots show):

    env -u LD_LIBRARY_PATH riemann/bin/python scripts/take_screenshots.py [--out DIR] [NAME...]

It starts Riemann through riemann.app.run, as the desktop launcher does, on
Qt's offscreen platform at SCALE times WIDTH x HEIGHT, with HOME and the XDG
folders pointed at a throwaway folder (removed at the end), so nothing of the
developer's own settings, history or session shows or changes. A folder of
sample files (three corpus PDFs, a Markdown note, a CSV table and a Word file
written as plain XML) is made there too, and the start page greets "Ada".

Each scene is a generator, played inside the app's event loop: it sets up the
window through the same methods the menus and keys call and yields what it
waits for, a condition (a callable, up to WAIT seconds) or a number of
seconds; then the window is grabbed (status toasts hidden first, since they
fade anyway) and saved as docs/images/NAME.png. Give scene names to take only
those, and --out to write somewhere else. The split-view scene loads the
paper's arXiv page, so it needs the network; the office scene needs
LibreOffice and is skipped without it.
"""

import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "python-app")
CORPUS = os.path.join(ROOT, "benchmarks", "corpus")
OUT = os.path.join(ROOT, "docs", "images")
WIDTH, HEIGHT, SCALE = 1280, 720, 1.5
WAIT = 60.0

state = {"window": None, "scenes": [], "failed": [], "out": OUT}


def isolate(home: str) -> None:
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.environ[name] = os.path.join(home, name.lower())
    os.environ["HOME"] = home
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(SCALE)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"


NOTES = """# Reading notes: generative models

## Attention Is All You Need (Vaswani et al., 2017)

- Drops recurrence entirely; every layer is **self-attention plus a feed-forward block**.
- Scaled dot-product attention:

```text
Attention(Q, K, V) = softmax(Q Kᵀ / √d_k) V
```

- Multi-head attention lets each head attend to a different subspace.
- Sinusoidal positional encodings, because attention itself is order-blind.

| Model | BLEU (EN-DE) | Training cost |
| --- | --- | --- |
| Transformer (base) | 27.3 | 12 h on 8 P100s |
| Transformer (big) | 28.4 | 3.5 days on 8 P100s |

## Latent Diffusion Models (Rombach et al., 2022)

1. Train an autoencoder that compresses images into a smaller latent space.
2. Run the diffusion process in that latent space instead of pixel space.
3. Condition through cross-attention, which is where the Transformer comes back in.

> Open question: how much detail does the autoencoder throw away at f = 8?
"""

RESULTS = """model,params (M),BLEU EN-DE,BLEU EN-FR,training (days),GPUs
Transformer (base),65,27.3,38.1,0.5,8
Transformer (big),213,28.4,41.8,3.5,8
ByteNet,,23.75,,,
ConvS2S,,25.16,40.46,,
GNMT + RL,,24.6,39.92,,
MoE,,26.03,40.56,,
Deep-Att + PosUnk,,,39.2,,
GNMT + RL Ensemble,,26.30,41.16,,
ConvS2S Ensemble,,26.36,41.29,,
"""


def word_file(path: str) -> None:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def para(text, style=""):
        props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{props}<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"

    body = "".join([
        para("Reading group: week 6", "Title"),
        para("Transformers and diffusion", "Heading1"),
        para("This week we read two papers that changed how sequence and image models are built. "
             "The first replaces recurrence with attention; the second runs diffusion in a learned "
             "latent space, which makes high-resolution image synthesis affordable."),
        para("Questions for discussion", "Heading1"),
        para("1. Why does attention need positional encodings at all?"),
        para("2. What does the autoencoder in latent diffusion lose, and does it matter?"),
        para("3. Which parts of either paper would you test first on your own data?"),
        para("Next week", "Heading1"),
        para("Bring one figure from either paper that you found hard to read, and we will redraw it together."),
    ])
    styles = "".join(
        f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>'
        for sid, name, size in (("Title", "Title", 48), ("Heading1", "heading 1", 32))
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>',
        )
        z.writestr("word/styles.xml", f'<w:styles xmlns:w="{w}">{styles}</w:styles>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')


def samples(folder: str) -> dict:
    papers = os.path.join(folder, "reading-group")
    os.makedirs(papers)
    paths = {}
    for name, source in (("Attention Is All You Need.pdf", "attention.pdf"),
                         ("Latent Diffusion Models.pdf", "latent_diffusion.pdf"),
                         ("Think Python.pdf", "thinkpython2.pdf")):
        paths[source] = os.path.join(papers, name)
        shutil.copy(os.path.join(CORPUS, source), paths[source])
    paths["notes"] = os.path.join(papers, "notes.md")
    with open(paths["notes"], "w", encoding="utf-8") as f:
        f.write(NOTES)
    paths["table"] = os.path.join(papers, "results.csv")
    with open(paths["table"], "w", encoding="utf-8") as f:
        f.write(RESULTS)
    paths["word"] = os.path.join(papers, "week 6.docx")
    word_file(paths["word"])
    paths["folder"] = papers
    return paths


def reader_ready(tab):
    def ready():
        if getattr(tab, "current_doc", None) is None:
            return False
        page = tab.page_widgets.get(tab.current_page_index)
        return page is not None and getattr(page, "has_full_page", False)

    return ready


def press(widget, key, modifiers=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(widget, key, modifiers or Qt.KeyboardModifier.NoModifier)


def reset(win):
    """Back to one empty window: no split, no folder, horizontal tabs, no tabs."""
    if win._split_view_active():
        win.close_split_view()
    win.close_folder()
    win.set_vertical_tabs(False)
    win.set_tabs_collapsed(False)
    for tabs in (win.tabs_side, win.tabs_main):
        while tabs.count():
            widget = tabs.widget(0)
            tabs.removeTab(0)
            if hasattr(widget, "cleanup"):
                widget.cleanup()
            widget.deleteLater()
    win.preview_tab_widget = None


def open_pdf(win, path, page=0, fit_width=True, theme=0):
    from PySide6.QtCore import Qt

    tab = win._open_document(path, preview=False)
    yield reader_ready(tab)
    while tab.theme_mode != theme:
        tab.toggle_theme()
    if fit_width:
        press(tab, Qt.Key.Key_W)
        yield 0.5
    if page:
        tab.jump_to_page(page)
        yield 0.5
    yield reader_ready(tab)
    return tab


def scene_split_view(win, paths):
    browser = win._add_browser_tab("https://arxiv.org/abs/1706.03762", win.tabs_main)
    yield from open_pdf(win, paths["attention.pdf"])
    win.tabs_main.setCurrentWidget(browser)
    win.toggle_split_view()
    yield lambda: win.tabs_side.indexOf(browser) != -1
    win.tabs_main.setCurrentIndex(0)
    yield lambda: browser.web.url().host() == "arxiv.org"
    yield 5.0


def scene_workspace(win, paths):
    win.open_folder(paths["folder"])
    win.set_vertical_tabs(True)
    yield from open_pdf(win, paths["latent_diffusion.pdf"])
    yield from open_pdf(win, paths["attention.pdf"], page=2, theme=2)
    yield 2.0


def scene_preview_markdown(win, paths):
    win.open_folder(paths["folder"])
    yield from open_pdf(win, paths["latent_diffusion.pdf"])
    yield from open_pdf(win, paths["attention.pdf"])
    win._on_file_single_clicked(paths["notes"])
    yield 4.0


def scene_collapsed_tabs(win, paths):
    win.open_folder(paths["folder"])
    yield from open_pdf(win, paths["latent_diffusion.pdf"])
    yield from open_pdf(win, paths["attention.pdf"], page=2)
    win.set_tabs_collapsed(True)
    yield 2.0


def scene_table(win, paths):
    from PySide6.QtCore import Qt

    yield from open_pdf(win, paths["attention.pdf"])
    tab = win._open_document(paths["table"], preview=False)
    yield lambda: tab.model is not None and tab.model.rowCount() > 0
    tab.sort_by(2)
    tab.sort_by(2)
    tab.view.resizeColumnsToContents()
    tab.view.setCurrentIndex(tab.model.index(0, 2))
    tab.view.setFocus(Qt.FocusReason.OtherFocusReason)
    yield 1.0


def scene_office(win, paths):
    from PySide6.QtCore import Qt

    yield from open_pdf(win, paths["attention.pdf"])
    tab = win._open_document(paths["word"], preview=False)
    yield reader_ready(tab)
    press(tab, Qt.Key.Key_W)
    yield 1.0
    yield reader_ready(tab)


def play(name, scene, win, paths):
    from PySide6.QtCore import QTimer

    steps = scene(win, paths)
    started = [time.time()]

    def step(waiting=None):
        try:
            if waiting is not None:
                if callable(waiting) and not waiting():
                    if time.time() - started[0] > WAIT:
                        raise TimeoutError(f"{name}: a condition never came true")
                    QTimer.singleShot(100, lambda: step(waiting))
                    return
            item = next(steps)
        except StopIteration:
            from riemann.ui.toast import Toast

            for toast in win.findChildren(Toast):
                toast.hide()
            pixmap = win.grab()
            pixmap.save(os.path.join(state["out"], f"{name}.png"))
            print(f"saved {name}.png ({pixmap.width()}x{pixmap.height()})")
            QTimer.singleShot(0, next_scene)
            return
        except Exception:
            traceback.print_exc()
            state["failed"].append(name)
            QTimer.singleShot(0, next_scene)
            return
        started[0] = time.time()
        if callable(item):
            QTimer.singleShot(0, lambda: step(item))
        else:
            QTimer.singleShot(int(item * 1000), lambda: step(None))

    step()


def next_scene():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from riemann.core.office import find_office

    if not state["scenes"]:
        QApplication.instance().quit()
        return
    name, scene = state["scenes"].pop(0)
    if name == "office" and find_office() is None:
        print("skipped office.png: LibreOffice isn't installed")
        QTimer.singleShot(0, next_scene)
        return
    reset(state["window"])
    play(name, scene, state["window"], state["paths"])


SCENES = {
    "split-view": scene_split_view,
    "workspace": scene_workspace,
    "preview-markdown": scene_preview_markdown,
    "collapsed-tabs": scene_collapsed_tabs,
    "table": scene_table,
    "office": scene_office,
}


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--out"]:
        state["out"] = os.path.abspath(args[1])
        args = args[2:]
    os.makedirs(state["out"], exist_ok=True)
    wanted = args or list(SCENES)
    home = tempfile.mkdtemp(prefix="riemann-shots-")
    try:
        isolate(home)
        state["paths"] = samples(home)
        state["scenes"] = [(name, SCENES[name]) for name in wanted]
        sys.path.insert(0, APP)
        import riemann.app as app_module
        from PySide6.QtCore import QSettings, QTimer

        settings = QSettings("Riemann", "PDFReader")
        settings.setValue("darkMode", True)
        settings.setValue("homepage/custom_name", "Ada")
        settings.sync()
        app_module.CHROMIUM_FLAGS += " --disable-gpu"
        shown = app_module.RiemannWindow.show

        def show(self):
            shown(self)
            if state["window"] is None:
                state["window"] = self
                self.resize(WIDTH, HEIGHT)
                QTimer.singleShot(1500, next_scene)

        app_module.RiemannWindow.show = show
        sys.argv = [sys.argv[0], "--new-window"]
        app_module.run()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return 1 if state["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
