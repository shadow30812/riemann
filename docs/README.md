# Riemann

Riemann is a desktop PDF reader and web browser in one window. PDFs, Markdown files and web pages open as tabs side by side, so a paper, its references and your notes can sit next to each other. The interface is written in Python with PySide6 (Qt 6). Rendering, text extraction and search run in a Rust extension built on PDFium.

It runs on Linux and Windows. Version 5.1.0, MIT licensed.

## What it does

### Reading PDFs

- Continuous or single-page scrolling, facing pages, and Auto Fit, Fit Width, Fit Height or manual zoom. Changing the zoom keeps the same spot of the same page in view.
- Pages are rendered on a background thread and kept in a shared memory cache. A quick low-resolution pass fills a page first and the sharp version replaces it a moment later, so fast scrolling (including middle-click autoscroll) never shows blank pages.
- Internal links (table of contents entries, cross-references, citations) jump to the exact spot they point at. External links open in a browser tab with Ctrl+click.
- Select text with the mouse: drag, double-click for a word, triple-click for a line. Or use the keyboard. Shift+arrow keys extend the selection a character or a line at a time, Ctrl+Shift+Left/Right by a word and Shift+Home/End to the line edges. Ctrl+C copies.
- Three page themes: light, fast dark (inverted) and smart dark (inverts lightness, keeps hues).
- Text reflow mode, find in page, OCR of the current page, and snipping an equation to LaTeX (needs the optional Pix2Tex modules).
- Annotations: sticky notes, text labels, pen, highlighter, text highlight, underline and strikeout, rectangles, ovals, tick and cross stamps, with undo and redo. Notes can be exported to Markdown.
- Digital signatures: signed PDFs are checked on open, the result shows in a side panel, and you can sign a document with a PKCS#12 certificate.
- Password-protected PDFs open after a prompt, and "Lock | Save" writes an encrypted copy.
- Rotation, printing, saving a copy, auto-renaming a file from its metadata, copying a BibTeX citation, and opening the file in another application.
- AI semantic search (Ctrl+I) finds passages by meaning. It runs a local sidecar process with a small sentence-embedding model; nothing leaves your machine.

### Browsing

- A Chromium-based browser (Qt WebEngine) with a persistent profile, bookmarks, history, autocomplete, a download manager and incognito windows.
- Per-page dark mode, an ad skipper, a video speed controller, an "audiophile" music mode, live captions translated to English (needs faster-whisper), saving a page to PDF, and downloading or streaming video through yt-dlp.
- A mini player in the menu bar controls whatever tab is playing media.

### Working with folders

- Open a folder (Ctrl+K, Ctrl+O, or "Open with Riemann" on a folder in your file manager) and it appears as a file tree on the left.
- A single click shows a file in a temporary preview tab that the next click replaces. A double click opens it for good.
- Favourite folders and recent folders are listed on the Workspaces dashboard (Ctrl+Shift+E).

### Preview mode

Turn on Settings > "Open Documents in Preview Mode" and every document opens as a lightweight preview, however it was opened. A preview PDF has no toolbar and skips annotations, signature checks, AI indexing and metadata extraction. Markdown is rendered and HTML is shown without running scripts, both without starting Chromium. Double-click a preview tab's title (or right-click it and choose "Open in Full Mode") to switch it to the full reader or browser at the same page.

### Layout

- Split view shows two tabs side by side. Use the "Split View" button in the menu bar, Ctrl+\\, or right-click a tab and choose "Move to Other Pane". You can also drag a tab onto the other pane, or out of the window to give it a window of its own.
- Vertical tabs put the main tab strip in a column on the left ("Vertical Tabs" button, the View menu or Settings). The toolbars stay where they are.
- Edge snapping: drag the window until the pointer touches a screen edge. Left or right edge gives that half of the screen, the bottom edge the bottom half, the middle of the top edge maximises, and the top corners give the top half. A translucent preview shows the target first. See the Wayland note under "Known limitations".
- Three-step fullscreen (F11 or F): reading mode with toolbars, then pure fullscreen, then back.

### Other tools

Split and merge PDFs, convert PDFs to images, text, Markdown or HTML, compress PDFs and images, generate captions for local media, and search your library by keyword or tags such as `author:Smith year:2024` (Ctrl+L).

### Sessions

Open tabs, split panes, the folder session and each PDF's page are saved every few seconds. If Riemann closes uncleanly it offers to restore them on the next start, and File > "Restore Last Opened Tabs" brings back the last session at any time. Ctrl+Shift+T reopens the last closed tab.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Ctrl+O | Open files |
| Ctrl+T / Ctrl+B | New PDF tab / new browser tab |
| Ctrl+W, Ctrl+Shift+T | Close tab, reopen last closed tab |
| Ctrl+Tab, Ctrl+Shift+Tab | Next / previous tab |
| Ctrl+\\ | Toggle split view |
| Ctrl+N, Ctrl+Shift+N | New window, new incognito window |
| Ctrl+K, Ctrl+O / Ctrl+K, F | Open folder / close folder session |
| Ctrl+H, Ctrl+J, Ctrl+K K | History, downloads, bookmarks |
| Ctrl+, | Settings |
| Ctrl+D | Toggle the interface theme |
| N | Toggle the current tab's page theme |
| F11 or F | Cycle fullscreen modes |
| A / W / H | Auto Fit / Fit Width / Fit Height |
| Ctrl+= / Ctrl+- / Ctrl+wheel | Zoom |
| R / C / D | Reflow mode / continuous scroll / facing pages |
| Left / Right, Page Up / Page Down, Space | Previous / next page, scroll by a page or a screen |
| Shift+arrows, Ctrl+Shift+Left/Right, Shift+Home/End | Extend the text selection |
| Ctrl+F / Ctrl+I | Find / AI search |
| Ctrl+Shift+A | Annotation tools |
| Ctrl+R / Ctrl+Shift+R | Rotate clockwise / counter-clockwise |
| Ctrl+P, Ctrl+Shift+S, F5 | Print, save an encrypted copy, reload from disk |

In a browser tab, F5 and Ctrl+R reload, Ctrl+Shift+R reloads without the cache, Ctrl+M toggles music mode, Ctrl+G the video controller, and F12 opens the developer tools.

## Running from source

You need Python 3.11, a Rust toolchain, and the PDFium library. Linux and Windows builds of PDFium are in `libs/`, and a copy of `libpdfium.so` sits in the repository root, where the Rust core looks for it when you run from there.

```bash
uv venv riemann --python 3.11
```

```bash
source riemann/bin/activate
```

```bash
uv pip install -e ".[dev]"
```

`pip install -e .` builds the Rust extension with maturin. After changing `rust-core/`, rebuild it with:

```bash
maturin develop --release
```

Then start the app from the repository root:

```bash
env -u LD_LIBRARY_PATH PYTHONPATH=python-app riemann/bin/python -m riemann
```

`just run` does the same after rebuilding the extension. Pass files or folders on the command line to open them, and `--new-window` to start a separate instance instead of handing them to the one already running.

Heavy features install on demand: Settings > "Manage External Dependencies" puts PyMuPDF, PyTorch, Pix2Tex, yt-dlp, Pygments, faster-whisper and a few others into a private virtual environment under the app's data directory. The AI search sidecar is a separate conda environment packed into `python-app/riemann/assets/riemann_ai_engine` by `riemann-ai/build_ai.sh`.

## Building a standalone executable

- `scripts/nbuild.sh` compiles a single-file Linux binary with Nuitka and writes it to `dist/Riemann`.
- `scripts/build.sh` builds with PyInstaller using `Riemann.spec`.
- `.github/workflows/release.yml` builds the Windows release on GitHub Actions.

On Linux, the packaged binary adds itself to the applications menu the first time it runs. The entry registers PDFs, Markdown, HTML and folders, so Riemann shows up under "Open With" in the file manager. `scripts/install_icon.sh` writes the same entry by hand.

## Tests

```bash
env -u LD_LIBRARY_PATH PYTHONPATH=python-app QT_QPA_PLATFORM=offscreen riemann/bin/python -m pytest python-app/tests
```

`scripts/test_runner.sh` also runs the Rust tests (`cargo test`) and the JavaScript tests for the injected browser scripts (`npm test`).

## Project layout

```text
python-app/riemann/
  app.py              main window: tabs, split view, sessions, menus, shortcuts
  core/               managers (history, bookmarks, downloads, library), captions,
                      conversion and compression, optional dependencies, window snapping
  ui/browser.py       browser tab and the lightweight Markdown/HTML/text preview
  ui/reader/          PDF reader: tab.py, background renderer, mixins for rendering,
                      annotations, search, signatures, metadata and AI search
  ui/explorer.py      folder tree and the Workspaces dashboard
  assets/             icons, themes, homepage, injected browser scripts
rust-core/            PyO3 extension wrapping PDFium (rendering, text, links, forms)
rust-ocr-worker/      OCR used by the core
riemann-ai/           local semantic-search sidecar (FastAPI, FAISS)
scripts/              build, packaging and test scripts
docs/                 this file, the developer guide and the directory tree
```

The developer guide in `docs/DevGuide.md` covers the internals in depth.

## Known limitations

- Wayland does not let applications place their own windows, so edge snapping can't work in a native Wayland window. On a Wayland session it is off by default. Turning it on in Settings makes Riemann run through XWayland after a restart. With fractional display scaling, XWayland windows can look slightly soft unless your compositor scales them natively (GNOME has an experimental `xwayland-native-scaling` option for this). GNOME's own Super+Left, Super+Right and Super+Up still tile the native window.
- Keyboard selection works within one page at a time.
- Code previews are syntax-highlighted only when Pygments is installed (it is left out of the Nuitka build; add it from "Manage External Dependencies").

## License

MIT. See `LICENSE`.
