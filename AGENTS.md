# AGENTS.md

Guide for anyone (person or AI agent) changing Riemann. Read it before editing. The open backlog lives in `docs/review-2026-11-14.md`; architecture detail lives in `docs/DevGuide.md`. Update this file whenever the layout, commands or rules below change.

## What Riemann is

A desktop PDF reader and web browser in one window, written in Python (PySide6 6.10, Qt 6, Python 3.11) with a Rust extension for PDF work and an optional AI sidecar.

| Part | Path | Notes |
| --- | --- | --- |
| Desktop app | `python-app/riemann/` | Entry `app.py` (`RiemannWindow`, `run()`). The window's behaviour is split into mixins in `window/` (documents, session, system, menus, panes, tabs, chrome, folders). |
| Reader tab | `python-app/riemann/ui/reader/` | `tab.py` (`ReaderTab`) plus behaviour split into `mixins/` (toolbar, navigation, selection, input, appearance, document, pages, read_aloud, forms, citations, rendering, annotations, ai, ...). State and widgets that more than one mixin uses are declared in `mixins/host.py` (`ReaderHost`); a test checks `ReaderTab` has them all. Rendering runs in `render_worker.py`. |
| Reader toolbar layouts | `python-app/riemann/ui/reader/toolbar_layout.py` | The buttons are created once in `mixins/toolbar.py`; `ToolbarLayout` only places them (top bar, side rail, ribbon, floating pill, menus, hidden) per the layout chosen in Settings. Add a new button to `COMMANDS` there, or it shows nowhere. |
| System tabs | `python-app/riemann/ui/system/` | Settings, history, bookmarks, downloads, library open as tabs (`window/system.py`). |
| Browser tab | `python-app/riemann/ui/browser.py` | Qt WebEngine. Start page is a local file in `assets/homepage/`. |
| Core services | `python-app/riemann/core/` | `ai_engine.py` (shared sidecar), `storage.py` (atomic JSON), `managers.py` (history, bookmarks, downloads, library), `pdf_annotations.py` (writes annotations into PDFs), `captions_server.py`. |
| Theme | `python-app/riemann/ui/palette.py`, `assets/theme/modern_dark.css`, `modern_light.css`, `assets/homepage/homepage.css` | Colour tokens are duplicated in all of these. Change them together. |
| Rust core | `rust-core/` | PyO3 0.30 module `riemann_core` on PDFium (pdfium-render 0.9, bindings for chromium/7543). Page indices are `u32` from Python. Typed in `python-app/riemann/riemann_core.pyi`. |
| OCR worker | `rust-ocr-worker/` | Separate crate. `recognize_pngs` reads many pages with one Tesseract run. |
| AI engine | `python-app/riemann/core/ai_server.py` | FastAPI on a random localhost port, per-run token, run as a child of the app (`python -m riemann.core.ai_server`, or the frozen exe with `--riemann-ai-sidecar`). ONNX MiniLM on onnxruntime; the model is downloaded on first use, pinned by commit and SHA-256. It never opens PDFs: the app sends page text. Client side: `core/ai_engine.py`. |
| PDFium | `libs/` | `libpdfium.so`, `pdfium.dll`, not in git: `just fetch-pdfium` (or `python scripts/fetch_pdfium.py`) downloads them, pinned by SHA-256. |

`python-app/riemann/assets/riemann_ai_engine/` may still exist on older checkouts: the 8 GB packed environment of the old AI sidecar, no longer used or built. It can be deleted; until then, exclude it from every search, lint and copy.

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

`just clean` deletes build output inside the project folders only. Never widen its paths. `.github/workflows/tests.yml` runs the same tests on GitHub for every push and pull request.

Single Python test file:

```bash
env -u LD_LIBRARY_PATH QT_QPA_PLATFORM=offscreen ./riemann/bin/python -m pytest python-app/tests/test_storage.py -q
```

## Environment traps on the main dev machine

- `LD_LIBRARY_PATH` points at another Qt build (`/opt/plecs`). Unset it (`env -u LD_LIBRARY_PATH`) for every Python command, or PySide6 fails to import.
- `grep` is ugrep and ignores `--include`. Use `/usr/bin/grep` with `--exclude-dir=riemann_ai_engine`.
- The repository sits on NTFS; `sed -i` warns about permissions. Edit with the editor tool or Python.
- `pytest --timeout` is not installed.
- Offscreen runs that start QtWebEngine need `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`, or the GPU process crashes now and then.
- Node is not installed, so the JavaScript tests in `assets/__tests__/` do not run here.

## Rules

### Code style

- No comments inside function or method bodies. Explanations go in the module docstring (or file header comment) at the top of the file. Docstrings on functions and classes only where the name and signature don't already say it. The same holds for Rust (`//!` module docs, `///` item docs) and JavaScript.
- Match the surrounding code. Don't write filler docstrings.
- Use palette tokens from `ui/palette.py`, never hard-coded colours. Colours that belong to the document rather than the theme (laser, pen, search hits, the presentation stage) are in its `CONTENT` table. `tests/test_palette.py` fails on any colour literal outside `palette.py` and `tooltip.py`.
- Read and write settings through `core.settings.app_settings()` (the `"Riemann", "PDFReader"` store), and start file dialogs with `get_dialog_directory` / `save_last_directory` from the same module. The `Favorites`, `BrowserSettings` and `FaviconCache` stores are separate on purpose.

### Data safety

- Write Riemann's own JSON files with `core.storage.write_json_atomic`; get the data folder with `core.storage.app_data_dir()`.
- Never overwrite a user's file without asking (PDF, `.srt`, video).
- Annotation writing appends an incremental update with pikepdf. New object numbers start after the highest existing object, not at the trailer `/Size`; see the module docstring of `core/pdf_annotations.py`.

### Security

- Local services (AI sidecar, captions server) listen on `127.0.0.1`, on a random port, and require a per-run token compared with `hmac.compare_digest`. Keep it that way for any new service.
- The start page is a local file with file-access rights. Treat anything that reaches it from a web page as untrusted: validate with `sanitize_homepage_links`, build DOM with `textContent`, allow only http and https links. Use `is_local_homepage(url)` for "is this the start page" checks.
- Downloads of code or models must be pinned by version and SHA-256.

### Qt pitfalls

- Never install an application-wide Python event filter (`app.installEventFilter`): even an empty one crashes QtWebEngine at startup. Filter specific widgets instead (see `ui/tooltip.py`).
- Don't call `QAction.menu()` on menu-bar actions from Python and let the result go out of scope: it invalidates the menu's actions. Use `findChildren(QMenu)`.
- Mixins that override Qt event handlers must come before `QWidget`/`QMainWindow` in the class bases.
- Tie delayed callbacks to the widget they touch (`QTimer.singleShot(ms, widget, fn)`), so they are dropped if the widget is deleted first.

### Threads and processes

- PDFium is not thread-safe. Within one process, all calls into `riemann_core` go through its single lock; don't add parallel PDFium access from threads. Parallelism comes from processes: each reader renders pages in a child process of its own (`core/render_process.py`, `PageRenderer`), so render through the tab's `page_renderer`, not `current_doc.render_page`. The child module must not import Qt or `core.log`. `RIEMANN_RENDER_PROCESS=0` turns children off.
- Stop every `QThread` before the app exits. Test harnesses must call `stop_all_render_workers` or emit `aboutToQuit`, or the process ends with "QThread: Destroyed while thread is still running" and a core dump.
- Long work (OCR, indexing, network) never runs on the UI thread.

### Testing

- pytest-qt is not installed here; `conftest.py` supplies a fallback `qtbot` whose `addWidget` stops the widget's timers at teardown. Pass every window a test creates to `qtbot.addWidget`, or its timers keep running into later tests.
- Every bug fix gets a test that fails without the fix. Prefer tests on behaviour (files on disk, real sockets, real PDFs in `benchmarks/corpus/`) over mocks of `open` or `json.dump`.
- Any script that launches Riemann must set `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `HOME` to throwaway folders, plus `QT_QPA_PLATFORM=offscreen`. Never touch the real `~/.config/Riemann` or `~/.local/share/Riemann`.
- Performance-sensitive changes to rendering: run the scroll benchmark in `benchmarks/` with a temporary label so the stored baseline isn't overwritten.

### Git

- Commit only when the owner asks. One logical change per commit.
- Don't commit binaries, logs or scratch files.

## Planned work

Tracked in `docs/review-2026-11-14.md`. Agreed with the owner on 2026-11-14:

- All items under security, data integrity, reliability and code quality.
- All ten feature ideas.
- Split `app.py` and `ui/reader/tab.py` into smaller modules, and remove in-body comments everywhere.
- A `System` tab type: Settings, history, downloads, bookmarks and site permissions open as tabs in the same window (one tab per page, re-opening focuses it), with its own tab colour.
- Per-site browser permissions in Settings (camera, microphone, screen sharing, location, notifications, clipboard read and write, autoplay, pop-ups), with safe defaults: ask for capture devices, location, notifications and clipboard read; allow clipboard write; block pop-ups.
- Redesigned tooltips: keycap style, done (`ui/tooltip.py`).
- Reader toolbar layouts (2026-11-15): Default (side rail, floating page controls in pure full screen), A (grouped menus), B (ribbon), C (side rail), D (floating pill), and Custom, chosen in Settings → Appearance.
