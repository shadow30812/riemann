# AGENTS.md

Guide for anyone (person or AI agent) changing Riemann. Read it before editing. Architecture detail lives in `docs/DevGuide.md`. Update this file whenever the layout, commands or rules below change.

## What Riemann is

A desktop document reader and web browser in one window, written in Python (PySide6 6.10, Qt 6, Python 3.11) with a Rust extension for PDF work and an optional AI sidecar.

| Part | Path | Notes |
| --- | --- | --- |
| Desktop app | `python-app/riemann/` | Entry `app.py` (`RiemannWindow`, `run()`). The window's behaviour is split into mixins in `window/` (documents, session, system, menus, panes, tabs, chrome, folders). |
| Reader tab | `python-app/riemann/ui/reader/` | `tab.py` (`ReaderTab`) plus behaviour split into `mixins/` (toolbar, navigation, selection, input, appearance, document, pages, read_aloud, forms, citations, rendering, annotations, ai, ...). State and widgets that more than one mixin uses are declared in `mixins/host.py` (`ReaderHost`); a test checks `ReaderTab` has them all. Rendering runs in `render_worker.py`. Markdown and text files open here too, as one HTML page in the tab's web view, and so do EPUB books and the basic views of Office files; Office files with LibreOffice open as a converted PDF, read-only (`mixins/document.py`). `replace_text.py` is Replace text. |
| Viewer tabs | `python-app/riemann/ui/viewers/` | `image_tab.py` (pictures), `table_tab.py` (CSV and TSV, editable, saved only after asking), `spreadsheet_tab.py` (Excel and OpenDocument sheets, read-only), `hex_tab.py` (any other file as bytes, read-only; `core/binary.py` says which files are programs). |
| Reader toolbar layouts | `python-app/riemann/ui/reader/toolbar_layout.py` | The buttons are created once in `mixins/toolbar.py`; `ToolbarLayout` only places them (top bar, side rail, ribbon, floating pill, menus, hidden) per the layout chosen in Settings. Add a new button to `COMMANDS` there, or it shows nowhere. |
| System tabs | `python-app/riemann/ui/system/` | Settings, history, bookmarks, downloads, library and help open as tabs (`window/system.py`). Help (`help_tab.py`, F1) is plain-words text for new users plus every keyboard shortcut. |
| Browser tab | `python-app/riemann/ui/browser.py` | Qt WebEngine. Start page is a local file in `assets/homepage/`. |
| Core services | `python-app/riemann/core/` | `ai_engine.py` (shared sidecar), `storage.py` (atomic JSON), `managers.py` (history, bookmarks, downloads, library), `doctypes.py` (what kind of document a file is), `convert_process.py` (EPUB and Office readers in a child process with a time limit: `epub.py`, `office_basic.py`, cleaned by `html_clean.py`), `office.py` (LibreOffice: finding it, converting in a locked-down profile, the cache), `office_install.py` (install commands), `tables.py` (CSV reading and saving), `images.py` (decoding pictures), `pdf_annotations.py` (writes annotations into PDFs), `page_edit.py` (page edits, passwords), `redaction.py` (applies redaction marks), `captions_server.py`. |
| Theme | `python-app/riemann/ui/palette.py`, `assets/theme/modern_dark.css`, `modern_light.css`, `assets/homepage/homepage.css` | Colour tokens are duplicated in all of these. Change them together. |
| Rust core | `rust-core/` | PyO3 0.30 module `riemann_core` on PDFium (pdfium-render 0.9, bindings for chromium/7543). Page indices are `u32` from Python. Typed in `python-app/riemann/riemann_core.pyi`. |
| OCR worker | `rust-ocr-worker/` | Separate crate. `recognize_pngs` reads many pages with one Tesseract run. |
| AI engine | `python-app/riemann/core/ai_server.py` | FastAPI on a random localhost port, per-run token, run as a child of the app (`python -m riemann.core.ai_server`, or the frozen exe with `--riemann-ai-sidecar`). ONNX MiniLM on onnxruntime; the model is downloaded on first use, pinned by commit and SHA-256. It never opens PDFs: the app sends page text. Client side: `core/ai_engine.py`. |
| PDFium | `libs/` | `libpdfium.so`, `pdfium.dll`, not in git: `just fetch-pdfium` (or `python scripts/fetch_pdfium.py`) downloads them, pinned by SHA-256. |

## Commands

Run from the repository root. The project environment is `./riemann/bin/python` (`.venv` is the fallback).

```bash
just fetch-pdfium
```

```bash
just run
```

```bash
just build
```

```bash
just test
```

```bash
just test-rust
```

After changing a version in `pyproject.toml` (needs uv and network; CI fails until the locks match):

```bash
just lock
```

Remake the screenshots in `docs/images/` (throwaway settings, offscreen; the split-view shot loads arXiv):

```bash
env -u LD_LIBRARY_PATH ./riemann/bin/python scripts/take_screenshots.py
```

Modules the packaged builds leave out are listed once, in `scripts/build-excludes.txt`; `nbuild.sh`, `nbuild.ps1`, the release workflow and `Riemann.spec` all read it.

`just clean` deletes build output inside the project folders only. Never widen its paths. `.github/workflows/tests.yml` runs the same tests on GitHub for every push and pull request.

Single Python test file:

```bash
env -u LD_LIBRARY_PATH QT_QPA_PLATFORM=offscreen ./riemann/bin/python -m pytest python-app/tests/test_storage.py -q
```

## Environment traps on the main dev machine

- `LD_LIBRARY_PATH` points at another Qt build (`/opt/plecs`). Unset it (`env -u LD_LIBRARY_PATH`) for every Python command, or PySide6 fails to import.
- `grep` is ugrep and ignores `--include`. Use `/usr/bin/grep`.
- The repository sits on NTFS; `sed -i` warns about permissions. Edit with the editor tool or Python.
- `pytest --timeout` is not installed.
- Offscreen runs that start QtWebEngine need `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`, or the GPU process crashes now and then.
- Node is not installed, so the JavaScript tests in `assets/__tests__/` do not run here; `tests.yml` runs them on GitHub (`npm ci`, then `npx jest python-app/riemann/assets/__tests__`).

## Rules

### Code style

- No comments inside function or method bodies. Explanations go in the module docstring (or file header comment) at the top of the file. Docstrings on functions and classes only where the name and signature don't already say it. The same holds for Rust (`//!` module docs, `///` item docs) and JavaScript.
- Match the surrounding code. Don't write filler docstrings.
- Use palette tokens from `ui/palette.py`, never hard-coded colours. Colours that belong to the document rather than the theme (laser, pen, search hits, the presentation stage) are in its `CONTENT` table. `tests/test_palette.py` fails on any colour literal outside `palette.py` and `tooltip.py`.
- Inside a reader, the toolbar, side rail, sections sidebar, scroll bars and search bars follow the page theme (`theme_mode`, the N key) through `apply_theme`; only the start page, notes panel and citation card follow the interface theme (Ctrl+D, `apply_ui_theme`). Scroll bars styled apart from the app use `scrollbar_style` from `ui/palette.py`.
- Short status messages go through `ui/toast.py` (`Toast`), one per window, reader or browser tab, so a new message replaces the old one.
- Find bundled files with `core.assets` (`asset_path`, `icon_path(name, dark)`), and ask `is_frozen()` whether this is a built executable. Nothing else checks `sys.frozen`, `sys._MEIPASS` or `__compiled__`; `tests/test_assets.py` fails if something does.
- Service addresses (metadata APIs, AI providers, citation links) live in `core/endpoints.py`. Local services use its `LOOPBACK` and `loopback_url`; the search engine is a setting (`core.settings.search_url`).
- Code loaded from a CDN carries a version and an SRI hash (see `KATEX_FILES` in `ui/reader/utils.py`).
- Read and write settings through `core.settings.app_settings()` (the `"Riemann", "PDFReader"` store), and start file dialogs with `get_dialog_directory` / `save_last_directory` from the same module. The `Favorites`, `BrowserSettings` and `FaviconCache` stores are separate on purpose; open them with `favourites_settings()`, `browser_settings()` and `favicon_cache()`. A key that more than one module reads or writes is a named constant in `core/settings.py` (its string never changes: installs have it stored); `tests/test_settings_keys.py` checks this.

### Data safety

- Decide what a file is with `core.doctypes` (`document_kind`, `is_reader_name`, `has_text_name`), not with suffix checks of your own. Binary files open read-only as bytes in the hex view and are never run; a program (`core.binary.program_kind`) gets no Open with.
- Code in a reader that opens a file (start page, drops, dialogs) calls `open_here`, so the file follows the preview setting and reaches history.
- Page edits, redaction and Remove password go through `_edit_pages`, which changes a scratch copy; the user's file changes only on Save. Encrypted files stay encrypted unless the user removes the password; editing a signed file asks first.
- Redaction saves the file in full (never an incremental update) and replaces the source only after `verify_redaction` passes. Where it can't remove exactly what is marked, it stops and names the page.
- Write Riemann's own JSON files with `core.storage.write_json_atomic`; get the data folder with `core.storage.app_data_dir()`.
- Never overwrite a user's file without asking (PDF, `.srt`, video).
- Annotation writing appends an incremental update with pikepdf. New object numbers start after the highest existing object, not at the trailer `/Size`; see the module docstring of `core/pdf_annotations.py`.

### Security

- Local services (AI sidecar, captions server) listen on `127.0.0.1`, on a random port, and require a per-run token compared with `hmac.compare_digest`. Keep it that way for any new service.
- The start page is a local file with file-access rights. Treat anything that reaches it from a web page as untrusted: validate with `sanitize_homepage_links`, build DOM with `textContent`, allow only http and https links. Use `is_local_homepage(url)` for "is this the start page" checks.
- Downloads of code or models must be pinned by version and SHA-256. Optional packages install from the hashed locks in `assets/requirements/` (`pip install --require-hashes --no-deps`); after changing a version in `pyproject.toml`, run `just lock`. yt-dlp must never be given `remote_components`: its YouTube solver comes from the pinned `yt-dlp-ejs` package and runs in the pinned Deno (`js_runtimes` in `ui/browser.py`). One exception, agreed with the owner: when the person clicks Install in the LibreOffice dialog, Riemann may run the system's package manager (through pkexec), Flatpak or winget to install LibreOffice (`core/office_install.py`). Those check their own downloads by signature or hash, but no version is pinned. The exception covers LibreOffice only, and nothing runs without that click.
- Markdown from files or AI answers is rendered only through `render_markdown_safely` (`ui/reader/utils.py`), which checks links on the parsed output.
- The single-instance socket is per user (`UserAccessOption`), and a running instance opens only what `accepts_handoff` allows.

### Qt pitfalls

- Never install an application-wide Python event filter (`app.installEventFilter`): even an empty one crashes QtWebEngine at startup. Filter specific widgets instead (see `ui/tooltip.py`).
- Don't call `QAction.menu()` on menu-bar actions from Python and let the result go out of scope: it invalidates the menu's actions. Use `findChildren(QMenu)`.
- Mixins that override Qt event handlers must come before `QWidget`/`QMainWindow` in the class bases.
- Tie delayed callbacks to the widget they touch (`QTimer.singleShot(ms, widget, fn)`), so they are dropped if the widget is deleted first.
- Every browser tab on a profile receives its `downloadRequested`; `_handle_download` claims each request once. Don't connect another handler to it.
- A widget that registers `QShortcut`s calls `ui/shortcuts.share_keys(self)` after creating them; otherwise the keys stop working in split view, where two tabs own the same shortcut. Describe every new shortcut in `SHORTCUTS` in `ui/system/help_tab.py` (`tests/test_help.py` fails otherwise), in plain words.
- Code that closes a browser tab or a window asks first: `close_tab`/`close_side_tab` (through `may_close`) or `confirm_close`, which let a page with unsaved changes ask Leave or Stay (`BrowserTab.confirm_leave`). Don't blank or delete a page before that.

### Threads and processes

- PDFium is not thread-safe. Within one process, all calls into `riemann_core` go through its single lock; don't add parallel PDFium access from threads. Parallelism comes from processes: each reader renders pages in a child process of its own (`core/render_process.py`, `PageRenderer`), so render through the tab's `page_renderer`, not `current_doc.render_page`. The child module must not import Qt or `core.log`. `RIEMANN_RENDER_PROCESS=0` turns children off.
- Stop every `QThread` before the app exits. Test harnesses must call `stop_all_render_workers` or emit `aboutToQuit`, or the process ends with "QThread: Destroyed while thread is still running" and a core dump.
- Never call `QThread.terminate`: a thread killed inside PDFium keeps the `riemann_core` lock for ever. Register a worker with `ui/threads.track` when it starts (`tests/test_source_rules.py` lists the few that aren't): `track` keeps it alive until it has finished, so its owner may drop it in the slot of its last signal, which arrives before `run` returns. When its tab closes, hand it to `ui/threads.release` with the signals that reach the tab; it finishes on its own and quitting waits for it. Workers that make network requests check `isInterruptionRequested` between requests.
- The Windows executable icon is `assets/icons/windows-icon.ico`; `Icon.ico` (really a PNG) is the Linux desktop icon. No two assets may differ only in letter case.
- Child processes get `core.processes.NO_WINDOW` (`**NO_WINDOW`), so the windowed Windows build never flashes a console.
- Long work (OCR, indexing, network) never runs on the UI thread.

### Testing

- `conftest.py` gives each test session its own `HOME` and XDG folders, removed at exit.
- pytest-qt is not installed here; `conftest.py` supplies a fallback `qtbot` whose `addWidget` stops the widget's timers at teardown. Pass every window a test creates to `qtbot.addWidget`, or its timers keep running into later tests.
- Every bug fix gets a test that fails without the fix. Prefer tests on behaviour (files on disk, real sockets, real PDFs in `benchmarks/corpus/`) over mocks of `open` or `json.dump`.
- Any script that launches Riemann must set `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `HOME` to throwaway folders, plus `QT_QPA_PLATFORM=offscreen`. Never touch the real `~/.config/Riemann` or `~/.local/share/Riemann`.
- `tests/test_user_sessions.py` drives the whole app in a child process through real keys, clicks and dialogs (scenarios in `tests/user_session.py`). Add a scenario there for a flow that spans several parts of the app.
- The Rust core's tests are unit tests inside `rust-core/src/lib.rs` (the crate is a `cdylib`); `just test-rust` runs them with PDFium from `libs/`.
- Performance-sensitive changes to rendering: run the scroll benchmark in `benchmarks/` with a temporary label so the stored baseline isn't overwritten.

### Git

- Commit only when the owner asks. One logical change per commit.
- Don't commit binaries, logs or scratch files.
