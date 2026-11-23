# Riemann Complete Developer Reference Guide

This guide serves as a detailed architectural map and implementation reference for the Riemann codebase. Riemann is a hybrid desktop research environment combining Python (PySide6), Rust, FastAPI, Chromium, and JavaScript/WebAudio subsystems into a unified local-first research platform.

The purpose of this document is not merely to describe files, but to explain:

* subsystem responsibilities
* architectural boundaries
* rendering and memory strategies
* event flow patterns
* persistence systems
* browser integration layers
* performance-sensitive paths
* extension points for future development

When modifying Riemann, always determine whether the change belongs primarily to:

* UI orchestration
* native rendering
* browser-side execution
* AI inference
* persistent state management
* asynchronous worker infrastructure

Route changes to the appropriate subsystem instead of expanding unrelated modules.

---

# Table of Contents

1. Architectural Philosophy & Runtime Model
2. Application Lifecycle & Main Window
3. Core Reader Architecture
4. Rendering & Virtualization Systems
5. Annotation Pipeline
6. Search & Text Extraction Systems
7. Browser Architecture
8. JavaScript Injection Layer
9. Media & Audio Infrastructure
10. Live Captioning Pipeline
11. AI Infrastructure
12. Persistent Managers & State Systems
13. Document Conversion & Compression
14. Workspace & Tab Infrastructure
15. Explorer & Favorites Systems
16. Session Persistence & IPC
17. Build & Packaging Infrastructure
18. Performance & Memory Considerations
19. Common Modification Scenarios
20. Repository Structure Reference

---

# 1. Architectural Philosophy & Runtime Model

Riemann intentionally separates responsibilities across multiple runtime layers.

## Python Layer

Responsible for:

* UI orchestration
* event routing
* threading
* browser integration
* layout systems
* session persistence
* state management

Python should not perform large-scale rasterization or heavy synchronous compute.

---

## Rust Layer

Responsible for:

* PDF rendering
* text extraction, including per-character glyph boxes (`get_char_boxes`) and, for Replace text, each character's style (`get_char_styles`: box, scaled font size, font name, serif, fixed-pitch, bold and italic flags, and fill colour as `#RRGGBB`)
* the document outline (`get_outline`)
* OCR delegation: `ocr_page` for one page; `ocr_pages` renders a list of pages under the PDFium lock, encodes them as PNG, and reads them all with one Tesseract run outside the lock (`recognize_pngs` in `rust-ocr-worker`, a list file in a private temporary folder; pages come back separated by form feeds). The reader's "Recognise text in pages…" entry in the Pages menu runs it eight pages at a time in `OcrPagesThread`, with progress and Cancel, and shows the text in a window with "Save as text…".
* search indexing
* page geometry
* annotation embedding
* reading third-party comments (`get_comments`, which skips Riemann's own `riemann:` annotations) and counting signature fields (`signature_count`)

The Rust backend exists primarily to bypass Python's GIL and improve long-session responsiveness.

Every box the core returns (search hits, text segments, glyph boxes, links, form widgets) is in points on the page as displayed: the crop box with the page's `/Rotate` applied, origin bottom-left (`DisplayFrame` in `lib.rs`). PDFium reports them in unrotated user space, which put selection, search highlights and links in the wrong place on rotated pages (scanned documents and landscape certificates often use `/Rotate 90`).

---

## Browser Runtime Layer

Chromium-based functionality runs inside Qt WebEngine processes.

This layer handles:

* WebAudio DSP
* injected overlays
* live media processing
* browser-side enhancement scripts
* caption overlays
* playback augmentation

This layer is isolated from the Qt widget hierarchy.

---

## AI Engine

Semantic search runs in a separate FastAPI process started by the app (`core/ai_server.py`, ONNX on onnxruntime), for process separation and crash containment. See section 11.

---

# 2. Application Lifecycle & Main Window

## `riemann/app.py` and `riemann/window/`

`app.py` holds `run()` (Chromium flags, profiles, the theme, the tooltip skin, logging) and `RiemannWindow.__init__`. The window's behaviour is split into mixins in `window/`, combined as `RiemannWindow(DocumentsMixin, SessionMixin, SystemMixin, MenusMixin, PanesMixin, TabsMixin, ChromeMixin, FoldersMixin, QMainWindow)`:

* `documents.py`: opening PDFs and other files, preview versus extended tabs, the signature dock.
* `session.py`: saving and restoring tabs, crash recovery, the clean-exit flag.
* `system.py`: System tabs (Settings, History, Bookmarks, Downloads, Library, Help). `open_system_page(page, section)` opens each page at most once and focuses it if it is already open.
* `menus.py`: the menu bar, the command palette (Ctrl+Shift+P), the mini player in the menu corner.
* `panes.py`: split view and `open_url_beside`.
* `tabs.py`: tab creation, closing, the closed-tab stack.
* `chrome.py`: full screen and the auto-hiding bars.
* `folders.py`: workspaces and the file explorer.

Helpers with no window state live beside them: `instance.py` (single-instance handoff), `desktop.py` (Linux desktop integration), `dialogs.py`, `common.py` (`get_resource_path`, `window_class()`). `app.py` re-exports the old names in `__all__`, so `from riemann.app import ...` keeps working.

**Collapse Tabs.** `set_tabs_collapsed()` hides both tab bars (the main strip, or the column with vertical tabs, and the side pane's strip) and saves the state under `app/tabs_collapsed`. The View menu action, the `btn_collapse_tabs` button in the menu corner (icon `list-collapse`, `list-collapse-white` in dark mode) and Ctrl+Shift+B all route through it and are kept in sync with signals blocked. Fullscreen hides the tab bars on its own; on the way out, `_restore_tab_bars()` respects the collapsed state.

---

## Chromium Configuration

The application configures Chromium flags early during startup.

Important behaviors depend on these flags:

* autoplay handling
* audio routing
* WebAudio stability
* media playback compatibility

Be extremely careful when modifying startup flags because subtle browser regressions can occur on Linux distributions.

The flags are `CHROMIUM_FLAGS` in `app.py`. Both Chromium sandboxes stay on, and renderers are not capped: `--process-per-site` gives each site one renderer, shared by its tabs.

---

## Single-Instance IPC

Riemann uses `QLocalServer` and `QLocalSocket`.

Flow:

```text
Secondary Launch
→ Detect Existing Server
→ Forward File Paths
→ Existing Instance Opens Files
→ Secondary Process Exits
```

This system prevents duplicate application instances during OS-level "Open With" actions.

If an independent window instance is explicitly required, passing `--new-window` bypasses single-instance IPC forwarding and initiates a new process instance. That process does not take the socket from the running one: `listen_for_instances` removes a socket file only when nobody answers on it. The socket is limited to the same user account, and the running instance opens only existing folders and PDF, Markdown or HTML files sent to it.

---

## Linux Desktop & Window Manager Integration

To ensure seamless desktop integration on Linux distributions (GNOME, KDE, Ubuntu Dock, Cinnamon):

* `RiemannWindow` registers `StartupWMClass=Riemann` on the top-level window.
* On startup, `install_desktop_file()` automatically generates and writes `~/.local/share/applications/Riemann.desktop` if absent.
* This ensures running instances are properly associated with the application launcher and dock icons rather than appearing as a generic executable or duplicate launcher icon.

---

## Multi-Window Lifecycle & State Tracking

`RiemannWindow` tracks all open non-incognito window instances globally via `_all_open_windows`:

* **Instance Tracking**: When a non-incognito `RiemannWindow` is instantiated, it registers itself into `RiemannWindow._all_open_windows`. On `closeEvent()`, it unregisters cleanly.
* **Multi-Window Session Serialization (`_save_all_windows_session()`)**: Iterates through all open windows in `_all_open_windows`, capturing window geometry, main tab paths/types, side tab paths/types, active tab indices, and split-view orientation states into a list serialized under the `sessions/multi_window` key in `QSettings`.
* **Sequential Close Grace Window**: When multiple windows are open, closing one window sets a 20-second timestamp threshold (`_multi_window_save_time`). Subsequent windows closed within this window avoid overwriting the multi-window session with a single-window state, protecting session restoration if the user closes windows sequentially prior to exiting.
* **Asking Before Closing (`confirm_close()`)**: Before a window closes, each reader with page edits waiting for Save asks what to do, and each browser tab lets its page ask about unsaved changes (`BrowserTab.confirm_leave`, see Leaving Web Pages). Cancelling either keeps the window open.
* **Clean Application Exit (`exit_application()`)**: Runs `confirm_close()` in every window, then serializes all of them and issues `QApplication.quit()`. In Qt 6, `quit()` sends each window a close event, so `exit_application` sets `close_confirmed` on every window first and `closeEvent` doesn't ask a second time.
* **Multi-Window Restoration**: On startup with `restore_session=True`, if `sessions/multi_window` exists and contains multiple window records, the primary window restores its own layout and dynamically spawns subsequent `RiemannWindow(restore_session=False)` instances, populating their tabs and geometries.

---

## Fullscreen Architecture & Hover Controls

Fullscreen mode in `RiemannWindow` integrates auto-hiding controls to maximize reading canvas while keeping navigation accessible:

* **Trigger Threshold**: Moving the mouse within the top 4 pixels of the display reveals both the main menu bar and the primary navigation bar using smooth property animations.
* **Dual-Corner Exit Fullscreen Toggle (`_track_mouse_for_fs`)**: Moving the mouse cursor within 150×100px of either the **top-right** (`x > width - 150, y < 100`) or **top-left** (`x < 150, y < 100`) corner triggers the floating exit fullscreen button (`btn_exit_fs`). The button dynamically repositions adjacent to the active hover corner (`(20, 20)` for top-left, `(width - 56, 20)` for top-right).
* **Auto-Hide Delay**: When the mouse leaves the top control zone, a 1.5-second single-shot timer triggers an auto-hide transition unless a menu or combo-box dropdown is currently open.
* **Tab Strip Restore (`_restore_tab_bars()`)**: Revealing the controls and leaving fullscreen both go through `_restore_tab_bars()`, which shows the main and side tab bars only when tabs are not collapsed.
* **System Clock Widget**: A live `SystemClockWidget` (`QLabel` updated via `QTimer`) is integrated directly into the navbar to maintain time awareness during distraction-free fullscreen research sessions.

---

## External Application Dispatcher

`populate_open_with_menu()` and `build_open_with_menu()` in `ui/components.py` build every "Open in External Application" menu (File menu, tab context menu, the reader's toolbar button and the folder tree):

* **Targets**: a local file, a folder, or a web address. `RiemannWindow._external_target()` picks the right one for a tab: a document's `current_path`, or a browser tab's URL (http, https, ftp, file).
* **Discovery**: `shutil.which()` finds installed browsers (Chrome, Firefox, Chromium, Brave, Edge) for files and URLs, and file managers or editors (Nautilus, Dolphin, Nemo, Thunar, VS Code) for folders. The system default handler is always offered through `QDesktopServices.openUrl()`.
* **Launching**: `subprocess.Popen([binary, target])`; "Choose Other Application..." runs any executable picked in a file dialog.

---

## Sections and Thumbnails Sidebar

`PdfSidebarWidget` (`ui/reader/sidebar.py`) sits at the left of a full-mode `ReaderTab`, hidden until toggled (Ctrl+Alt+S or `btn_toggle_sidebar`). Its visibility and mode are remembered in `reader/sidebar_visible` and `reader/sidebar_mode`.

* **Outline**: read on a worker thread through the core's `get_outline()`, which walks PDFium's bookmark tree depth-first and returns `(depth, title, page_index)`; pypdf is the fallback for older core builds. Short outlines open expanded. As the reader scrolls, the last section starting at or before the current page is selected.
* **Thumbnails**: one `_ThumbRenderer` thread renders only the rows in view (plus two either side), producing `QImage`s that become `QPixmap`s on the UI thread. At most 160 thumbnails are kept; the oldest revert to a placeholder. A generation counter drops results for a previous document or page theme.

---

## Reader Toolbar Layouts

`ToolbarMixin._setup_toolbar_buttons` creates every reader button once. `ToolbarLayout` (`ui/reader/toolbar_layout.py`) decides where each one sits, from the layout chosen in Settings, Appearance, Reader toolbar (`reader/toolbar_layout`): Default, A (grouped menus), B (ribbon), C (side rail), D (floating pill) or Custom (`reader/toolbar_custom`, JSON of command to place).

* Places: the top bar, a side rail, a ribbon row, a floating pill, a menu, or hidden. `COMMANDS` lists every command with its widgets, label, shortcut, group and fold priority. A button missing from `COMMANDS` shows nowhere.
* `OverflowBox` folds the lowest-priority items into a "⋯" menu when the bar is too narrow, and unfolds them when there is room again.
* Default is layout C plus the floating page controls in pure full screen (state 2). The rail can be hidden from its context menu and brought back from the bar's "⋯" menu.
* Moving a layout reparents widgets into a hidden `parking` widget before the old containers are deleted, so no button is ever destroyed.
* Menu entries mirror the real button (icon, checked, enabled) and click it, so behaviour stays in one place. The command palette lists folded and hidden buttons through their `riemannCommand` property.
* Changing the setting emits `notifier().changed`; every open reader re-applies its layout.

---

## Media Kill-Switch

One of the most important stability workarounds inside the application.

QtWebEngine media threads can outlive Qt widget destruction during application shutdown.

To prevent segmentation faults:

* active media tabs are redirected away from live media URLs
* browser streams are forcefully severed
* Chromium cleanup occurs before Qt teardown

Do not remove this logic unless replacing it with another shutdown-safe media strategy.

---

## Settings Infrastructure & UI Scaling

Settings are persisted through `QSettings`, always obtained from `core.settings.app_settings()` (the `"Riemann", "PDFReader"` store kept from the first releases). The same module holds `get_dialog_directory` / `save_last_directory`, which every file dialog uses, and list helpers (`string_list`, `set_string_list`). The `Favorites`, `BrowserSettings` and `FaviconCache` stores stay separate.

Persistent categories include:

* browser preferences
* zoom settings
* homepage customization
* default directories
* autoscroll speed
* theme preferences (defaulting to Light Mode for initial installs)
* session state
* `app/ui_scale`: UI Display Scaling percentage (50% to 250%)
* `reader/open_in_preview_mode`: Default document opening mode (Extended vs Preview)

The settings UI is the Settings System tab (`ui/system/settings_tab.py`), with sections for appearance, documents, browser, permissions, AI, privacy and about. Changes apply live; settings that need a restart say so inline.

### UI Display Scaling Integration
Located in the Settings tab, Appearance:
* Configures `QT_SCALE_FACTOR` dynamically.
* Integrates a wide `QSlider` (220px, 1% single step) and an interactive numeric `QSpinBox` synchronized bidirectionally.
* Applies on restart, scaling all Qt widgets, fonts, and icons cleanly for high-DPI displays.

### Document Open Mode Setting & Tab Replacement Infrastructure
The Settings tab's Documents section has a checkbox controlling `reader/open_in_preview_mode`:
* **Live Change Detection**: If the user toggles this setting when PDF documents are currently open in the main or side panes, `RiemannWindow` prompts the user with explicit action buttons:
  * **"Switch Open Documents"** (`AcceptRole`): executes in-place tab hot-swapping via `_switch_open_documents_mode(new_preview_mode)`.
  * **"Keep Current Layout"** (`RejectRole`): saves the setting for future documents while leaving active documents in their existing layout.
* **In-Place Tab Conversion (`_switch_open_documents_mode`)**:
  * Iterates across `tabs_main` and `tabs_side`.
  * For each open document, instantiates the target tab type (`PreviewReaderTab` if converting to preview mode, or `ReaderTab` if converting to extended mode).
  * Safely reads the old widget's scroll state (`hasattr(old_widget.scroll, "verticalScrollBar")`), page index, tab title, and file path.
  * Inserts the new tab at the exact same index, transfers focus, calls `cleanup()` on the old tab, and schedules it for deferred deletion.


### Opening From Inside a Reader
A reader tab can also open files itself: the start page's recent list, path box and Browse button, a file dropped on the tab, and the reader's Open dialog. All of them call `open_here(path)` (`mixins/document.py`), which hands the path to the window's `open_from_reader(reader, path)` (`window/documents.py`):

* A reader still on its start page is replaced, at the same tab index, by the tab `_open_document` makes. The file therefore opens in the mode `reader/open_in_preview_mode` asks for, with that mode's tab icon (the eye for previews), and goes into history.
* A reader that already shows a document loads the file in place, after recording it in history.
* Outside a window (tests, a bare `ReaderTab`), `open_here` just calls `load_document`.

New ways of opening a file from inside a reader should go through `open_here`, or they skip history and ignore preview mode.

---

## Theme Palette

`ui/palette.py` holds the colour tokens for both interface themes (`DARK`, `LIGHT`, read through `tokens(dark)`): canvas, surface, raised, border, text, muted, faint, accent and danger colours, `system` (the teal of System tabs) and `private` (incognito address bars, Python only). Shared small stylesheets are functions there too: `toast_style`, `link_tip_style`, `incognito_url_style`. New stylesheets take their colours from these tokens; `tests/test_toolbar_layout.py` checks the reader's bars use no other colour. The same values are written out in three places, which must change together:

* `ui/palette.py`, used by the PDF start page (`ReaderTab._style_home_page`), the Workspaces dashboard (`FolderHomeTab._apply_style`), the file tree and the browser toolbar.
* `assets/theme/modern_dark.css` and `modern_light.css`, the window stylesheets. Both come from one template, so their selectors are identical and only colours and icon names differ.
* `assets/homepage/homepage.css`, as CSS variables under `:root[data-theme=...]`.

Other pieces of the theming:

* `GridCanvas` paints the start pages' background: the canvas colour, a faint 32 px grid that echoes the app icon, and a soft accent glow at the top. The browser homepage draws the same grid in CSS.
* `tinted_pixmap(name, colour, size)` renders a Lucide SVG from `assets/icons` in any colour, so home page icons can use the accent without separate `-white` files.
* The browser homepage loads as `homepage.html?theme=dark|light`. An inline script sets `data-theme` before first paint, and `BrowserTab.apply_theme` calls `window.setTheme()` when the tab's theme changes. `smart_dark_mode.js` skips the homepage so it is never inverted.
* `RiemannWindow.toggle_ui_theme()` calls `apply_ui_theme(dark)` on every tab that has it (empty reader tabs and the Workspaces dashboard).
* Dialog buttons are outlined; the default button in a `QDialogButtonBox` uses the accent. Give other buttons a role instead of inline colours: `btn.setProperty("role", "primary" | "danger" | "success")`.
* `ButtonIconFreeStyle` (a `QProxyStyle` set in `run()`) turns off `SH_DialogButtonBox_ButtonsHaveIcons`, which removes the stock icons some desktop themes put on OK, Cancel, Yes and No.

---

# 3. Core Reader Architecture

## `ui/reader/tab.py`

`ReaderTab` is the central document-viewing component. `tab.py` holds only its state (`__init__`), the backend, shortcuts and a few event hooks; everything else is in `ui/reader/mixins/` (see Mixin Composition Strategy). The toolbar is built in `mixins/toolbar.py` and arranged by `ToolbarLayout` (see Reader Toolbar Layouts).

---

## Middle-Click Autoscroll Architecture

`ReaderTab` implements a smooth autoscrolling engine:

* **Activation**: Middle-clicking inside the viewport calls `start_middle_click_scroll()`, recording the global mouse anchor position and starting `self.autoscroll_timer` (30ms interval).
* **Deadzone & Velocity Scaling**: An 8px deadzone prevents accidental drift. Outside the deadzone, velocity is calculated via non-linear acceleration:
  ```python
  dy_eff = dy - 8 if dy > 8 else (dy + 8 if dy < -8 else 0)
  speed_y = (dy_eff * 0.12) * (1.0 + abs(dy_eff) * 0.003)
  ```
* **Global Override Cursors**: Uses `QApplication.setOverrideCursor()` and `changeOverrideCursor()` to dynamically display:
  * `Qt.CursorShape.SizeAllCursor` inside deadzone or multi-directional drift.
  * `Qt.CursorShape.SizeVerCursor` when vertical movement dominates.
  * `Qt.CursorShape.SizeHorCursor` when horizontal movement dominates.
* **Caret Suppression**: The event filter intercepts `MouseMove` and prevents child `PageWidget` instances from resetting the cursor to an `IBeamCursor`.
* **Exit Conditions**: Autoscroll terminates cleanly on a second middle-click, left-click, or Escape key press, restoring all override cursors in a loop.

---

## Keyboard Navigation & Document Reload

* **Page Scrolling**: `Page Up` and `Page Down` trigger `scroll_page_length(-1)` and `scroll_page_length(1)` to jump viewport by one full visible page height.
* **F5 Document Refresh**: Calls `reload_document()`. Compares file modification times and content hashes. If modified or deleted on disk, prompts the user with an option dialog to reload the new document or keep the active cached view.

---

## Shortcuts in Split View

Reader and browser tabs register their shortcuts at window scope (`QShortcut` parented to the tab, `WindowShortcut` context), so they work wherever the focus is in the window. In split view two tabs are visible, and both own keys such as Ctrl+F, F5 and Alt+Left. Qt counts such a key as ambiguous: it emits `activatedAmbiguously` and runs neither. `ui/shortcuts.py` handles this. Every widget that owns shortcuts (the window, `ReaderTab`, `BrowserTab`, the AI panel) calls `share_keys(self)` after creating them. An ambiguous press then runs the copy whose widget holds the focus. When a window-wide shortcut shares the keys, the innermost copy wins: the AI panel's Ctrl+L beats Library while the panel has the focus. A widget that registers new shortcuts must call `share_keys` too. `tests/test_shortcuts.py` also checks that the window registers each of its own key sequences once.

---

## Tab Focus Isolation

In standard Qt focus traversal hierarchies, Tab-key navigation or switching between tab widgets can propagate focus events down to internal `QScrollArea` viewports or child widgets, inadvertently advancing the scrollbar by one line or page.

To eliminate unwanted viewport jumping when cycling tabs:
* `ReaderTab` explicitly overrides `focusNextPrevChild(next: bool) -> bool` and returns `False`.
* This prevents background focus traversal from mutating scroll position while keeping explicit user keyboard navigation (arrow keys, Page Up/Down, Space, autoscroll) intact.

---

## Universal Page Indicator Popup

`ReaderTab` features a transient page indicator pill (`page_indicator_popup`) positioned near the vertical scrollbar:

* **Triggering Pipeline**: Invoked inside `defer_scroll_update()` whenever the scrollbar position moves or programmatic page jumps occur.
* **Display Mode Coverage**: Operates universally across all three display modes:
  * Normal / Windowed Mode (`_fullscreen_state == 0`)
  * Reading Mode with visible toolbar (`_fullscreen_state == 1`)
  * Pure Fullscreen with hidden toolbar (`_fullscreen_state == 2`)
* **Geometry Calculation**: Positions the widget dynamically adjacent to the vertical scrollbar:
  ```python
  x = max(10, self.width() - sb_width - self.page_indicator_popup.width() - 15)
  ratio = sb_val / sb_max if sb_max > 0 else 0.5
  y = int(40 + ratio * (self.height() - self.page_indicator_popup.height() - 80))
  ```
  This bounds `y` within the visible viewport so it never collides with top navigation toolbars or bottom status bars.
* **Opacity Fade Lifecycle**: Uses a `QGraphicsOpacityEffect` and `QPropertyAnimation` with cubic easing. Upon triggering, the popup instantly resets to 100% opacity and restarts a 1200ms single-shot hold timer (`_page_indicator_timer`), followed by an 800ms smooth fade to 0.0 opacity, hiding the widget on completion.

---

## Mixin Composition Strategy

Reader functionality is divided into mixins in `ui/reader/mixins/`:

* `ToolbarMixin` (`toolbar.py`): `setup_ui`, the buttons, search bars, notes panel and citation card.
* `NavigationMixin`: paging, zoom, back and forward history (Alt+Left, Alt+Right).
* `SelectionMixin`, `InputMixin`: text selection, mouse, keyboard and wheel input.
* `AppearanceMixin`: themes, the start page, toasts, full screen, presentation.
* `DocumentMixin`: loading, reloading, saving copies, cleanup.
* `PagesMixin`: delete, rotate, move, extract and insert pages (`core/page_edit.py`).
* `RenderingMixin`, `AnnotationsMixin`, `AiMixin`, `SignaturesMixin`.

Mixins that override Qt event handlers come before `QWidget` in `ReaderTab`'s bases. Every mixin derives from `ReaderHostBase` (`mixins/host.py`): for type checkers it is the `ReaderHost` Protocol, which declares the state and widgets the mixins share; at run time it is `object`. A test checks that a fresh `ReaderTab` has every name `ReaderHost` declares, so add an attribute there when a second mixin starts using it.

Inside a mixin, `self` is the whole tab, so any mixin can read or change any other's state. The parts at the edges are therefore ordinary classes the tab owns instead ("the tab has a ruler", not "the tab is a ruler"). Each gets the few things it needs through its constructor, usually as functions such as `page_chars(page)` or `document()`, and reports back through Qt signals; `ReaderTab.__init__` connects those signals to the widgets. Each can be built in a test without a `ReaderTab`:

* `ReadingRuler` (`mixins/ruler.py`, `tab.ruler`): the reading ruler.
* `ReadAloud` (`mixins/read_aloud.py`, `tab.read_aloud`): reading aloud and its highlights.
* `CitationHover` (`mixins/citations.py`, `tab.citations`): citation hover cards.
* `FormSaver` (`mixins/forms.py`, `tab.forms`): writing form entries into the PDF.
* `DocumentMetadata` (`mixins/metadata.py`, `tab.metadata`): bibliographic details, renaming, citations.
* `PageSearch` (`mixins/search.py`, `tab.search`): finding text; the search bar itself stays in `ReaderTab`.

When adding features:

* rendering-related logic belongs in RenderingMixin
* persistent annotation logic belongs in AnnotationMixin
* AI flows belong in AiMixin
* a feature that needs only a little of the reader's state is a class of its own, built like the parts above

Do not continuously expand `ReaderTab` itself.


## Document Types and the Text View

`core/doctypes.py` decides what a file is, for the whole app. `document_kind(path)` returns `pdf`, `markdown`, `html`, `ebook`, `office`, `image`, `table`, `text` or `binary`, and `office_type(path)` says which kind of Office file (document, presentation, spreadsheet, drawing). Names come first (`.pdf`, `.md`, `.docx` and so on), then content: a renamed Office file is recognised by its ZIP parts or `{\rtf` header, a picture by its signature. Any other file is text when its first 8 KB hold no NUL byte and decode as UTF-8, so source files, logs and extension-less files such as `Makefile` all count. Any other file is binary and opens read-only in the hex view (`ui/viewers/hex_tab.py`), which reads it a block at a time and never runs it. `core/binary.py` says which files are programs, by signature (ELF, MZ, Mach-O, WebAssembly, Windows shortcuts, `#!`) or name (`.exe`, `.msi`, `.bat`, `.AppImage` and so on): those get a warning banner, and `populate_open_with_menu` offers no way to open them, since a system's default application for a program runs it. Where reading a file is too slow or not trusted, the name lists decide alone: `TEXT_SUFFIXES` and `TEXT_NAMES` (the union of `PROSE_*` and `CODE_*`) drive the explorer's icons, the open dialogs' filter (`dialog_filter`) and the single-instance handoff, which never sniffs content.

PDFs, Markdown and text open in the reader; HTML opens in the browser. In full mode a reader shows Markdown and text as one HTML page in its web view (`_show_text_document`): Markdown rendered through `generate_markdown_html`, other text highlighted by Pygments when it is installed (`generate_text_html`, up to `HIGHLIGHT_LIMIT`, 1 MB). Files over `TEXT_VIEW_LIMIT` (20 MB) are cut off with a note. `QWebEngineView.setHtml` refuses pages over 2 MB, so a bigger page is written to a per-tab file in the cache folder and loaded from there; such a page can't load KaTeX or the emoji font. In preview mode text and Markdown use the lightweight `PreviewTextTab` instead.

## Converters in a Child Process

EPUB books and the basic views of Office files come from files anyone could have made, so they are parsed in a child process with a time limit (`core/convert_process.py`). `CONVERTERS` maps a kind (`ebook`, `office`, `spreadsheet`) to a function; `Conversion(kind, path).run()` starts `python -m riemann.core.convert_process` (the frozen executable with `--riemann-convert`, checked in `build_entry.py` before Qt loads), waits up to `TIMEOUT` and reads the answer from a JSON file, so nothing a library prints can corrupt it. A crash, a memory error or a hang only ends the child; `cancel()` kills it when the tab closes first. The children import no Qt.

Books and documents come back as `{title, html, sections, note}`; the HTML goes through `core/html_clean.py` (an allowlist of tags and attributes, no scripts, styles or remote URLs, pictures only as `data:` URIs) and the reader shows it as one page with the sections in the sidebar (`show_sections`, `scroll_to_anchor`). Spreadsheets come back as one JSON-lines file per visible sheet, read lazily by the table view.

## Office Files and LibreOffice

`core/office.py` finds LibreOffice (`find_office`: the override in Settings, `PATH`, the usual install folders, the Windows registry, Flatpak, snap) and converts with `OfficeConversion(path, target)`, which runs `soffice --headless --convert-to` with a profile of Riemann's own. That profile's `registrymodifications.xcu` is rewritten before every run (macros off, remote links blocked, no update check), and the environment leaves out Riemann's Qt and Python paths. One conversion runs at a time. Results are cached under the cache folder, keyed by the file's path, size, modification time, the target and the LibreOffice build, and pruned to 500 MB; incognito windows convert into a folder of the tab's own that is deleted with it.

A converted reader keeps `current_path` on the Office file and sets `converted_pdf`; `pdf_path()` returns the PDF, and every action that would write to it (annotations, forms, page edits, signatures, metadata) is gated off. Save (Ctrl+S) becomes Save as PDF, which copies the PDF where the person chooses and opens that. "Edit in LibreOffice" opens the file in the person's own LibreOffice (`open_in_office`); the reader polls the file's modification time and reconverts once it has been stable for two checks, keeping the page.

Without LibreOffice, `window/documents.py` (`_office_ready`) asks once per kind of Office file per session (`ui/office_install.py`): install LibreOffice, use the basic view, or cancel. `core/office_install.py` builds the install commands (the distribution's package manager through pkexec, then Flatpak, or winget), and they run in a `QProcess` only after the person clicks Install. Restored tabs never ask. The basic view is `core/office_basic.py`: mammoth for Word, python-pptx for PowerPoint, striprtf for RTF and a reader of its own for OpenDocument (lxml over `content.xml`, with headings found through style inheritance). Its packages are bundled; a source install without them is offered the hashed lock `office-viewers.txt`.

## Pictures, Tables and Spreadsheets

These open in tabs of `ui/viewers/` rather than the reader. Each has `current_path`, `is_preview`, `cleanup()` and `toggle_theme()`, so sessions, previews and the tab tooltip treat it as a document (`VIEWER_TABS`).

`ImageTab` decodes on a worker thread through `core/images.py` (Pillow, with only the formats in `PILLOW_FORMATS`, then `QImageReader` for SVG), with the EXIF orientation applied and frames as pages; it fits, zooms, steps through the folder and saves as PDF. `TableTab` shows CSV and TSV through `core/tables.py` (encoding, delimiter and header detected from the first 64 KB), loading `CHUNK_ROWS` at a time as the view scrolls. Cells can be edited in full mode for files up to `EDIT_LIMIT_BYTES`. Changes stay in the loaded rows, with an undo stack, until Ctrl+S asks Save, Save As or Cancel. Saving writes every row in the file's order through `write_delimited`: same encoding, delimiter and line endings, through a temporary file renamed into place. Closing a changed table asks through `resolve_edits`, from `may_close` and `confirm_close`.

`SpreadsheetTab` is a `TableTab` whose sheets arrive from a worker: LibreOffice, when installed, saves the workbook again as `.xlsx` so the formulas are recalculated, then the `spreadsheet` converter reads values with their number formats, merged cells and formulas. Spreadsheets are read-only. "Show as pages" asks the window for a reader tab next to it, which converts the workbook to PDF.

## Page Editing and the Scratch Copy

Page edits (rotate, delete, move, extract, insert, OCR of a range, redaction, Remove password) never touch the file on disk until Save. The first edit writes pending annotations into the PDF, records Riemann's annotation copy so Discard can restore it, and copies the file to a private cache folder (`edit_scratch`). From then on `pdf_path()` is the scratch copy, which the reader loads and renders and into which annotations, form entries and further edits go; `current_path` stays the document's identity for notes, the session, history and the library.

Every edit runs through `_edit_pages(operation, *args, done=None)`, which calls `operation(path, *args, password=self.document_password)` from `core/page_edit.py`. Each operation rewrites the whole file into a temporary file next to it and renames it over the target, so a failure leaves the target untouched. It returns a page mapping that `remap_pages` uses to move Riemann's per-page annotation copy. Save writes the scratch copy over the original in one rename, keeping its permissions, after asking if the original changed on disk meanwhile. Discard deletes the scratch copy, restores the annotations and reopens the original. Closing the tab, or loading another file into it, asks which (`resolve_page_edits`).

## Encrypted and Signed PDFs

`page_edit.document_state(path, password)` reports whether a file is encrypted and whether it is signed; `DocumentLoadWorker` emits it with the loaded document, and `_on_document_loaded` stores it in `document_encrypted` and `document_signed`.

* **Encrypted files** are edited with the password the reader opened them with and saved encrypted again (pikepdf drops encryption unless `save(encryption=True)`). `page_edit._open` tries without a password first, because pikepdf warns when given one for a file that has none.
* **Remove password**: while an encrypted PDF is shown, the Protect button (Ctrl+Shift+S) turns from `file-lock.svg` into `file-key.svg` and offers Remove password instead of a protected copy (`_update_protect_button`). "Remove from this file" runs `page_edit.remove_password` through the scratch copy, so the file stays locked until Save. "Save an unlocked copy…" writes `save_unlocked_copy` at once and refuses to write over the source. The button's menu and ribbon labels come from its `riemannLabel` and `riemannShort` properties, and the toolbar is laid out again when they change (`toolbar_layout.py`).
* **Signed files** can be edited, but rewriting one invalidates its signatures. The first edit asks (`_confirm_signed_edit`, "Edit anyway" or Cancel); the answer holds until another file is loaded. Annotation writing still leaves signed and encrypted files alone (see Annotation Storage).

---

## PageWidget Architecture

Each rendered page is wrapped inside a custom `PageWidget`.

Responsibilities:

* displaying rasterized page pixmaps
* temporary drawing overlays
* selection rectangles
* hyperlink overlays
* shape previews
* transient interaction rendering

The widget intentionally separates temporary state from committed document state.

`PageWidget` paints the page pixmap itself instead of relying on `QLabel.setPixmap` with `scaledContents`. `setPixmap()` stores the pixmap in `_page_pixmap` and clears the label's own pixmap; `paintEvent()` draws it at `(0, 0)` when its size is within 2 px of the widget, and smooth-scales it to `rect()` otherwise. With `scaledContents`, a pixmap one pixel off the widget size was rescaled on its first paint, which cost a full-page resample on the UI thread. `setText()` and `clear()` drop the stored pixmap, so placeholder text and a page image never show at once.

---

# 4. Rendering & Virtualization Systems

## `mixins/rendering.py`

This file contains the most performance-sensitive UI code in the application.

Core responsibilities:

* viewport construction
* page widget generation
* virtualized rendering
* facing-page layouts
* scroll restoration
* zoom handling
* rebuild scheduling
* high-zoom memory clamping and priority rendering

---

## Rendering Flow

```text
Rust render into an uninitialised PyBytes (BGRA, no copy)
→ QImage (Format_RGB32, shares the bytes)
→ QPixmap (shares the image data)
→ PAGE_CACHE
→ PageWidget.paintEvent
→ Scroll Viewport
```

* `render_page` reads the page size with `pages().page_size()` under the PDFium lock, allocates the output with `PyBytes_FromStringAndSize(NULL, len)`, then releases the GIL and renders through `PdfBitmap::from_bytes` straight into that buffer. `render_config()` sets a fixed size, native byte order (`set_reverse_byte_order(false)`), a white clear colour and annotation rendering.
* `convert_pixels()` applies the page theme in place: mode 0 does nothing, mode 1 inverts the colour channels, mode 2 runs `smart_dark` per pixel.
* `PAGE_IMAGE_FORMAT` in `render_worker.py` is `Format_RGB32`. PDFium's BGRA in memory is Qt's RGB32 layout on little-endian machines, so `QPixmap.fromImage` shares the buffer. `Format_ARGB32` made Qt scan the alpha channel and convert the image on every page.
* Overlays (annotations, search hits) are drawn on a copy of the cached pixmap. `_render_overlays` returns early when a page has neither, because opening a `QPainter` on a shared pixmap detaches it and copies the whole image.
* While the sharp render is pending, `_display_stand_in` scales a cached low-resolution or older render once to the widget size times the device pixel ratio, so paint events don't rescale it.

---

## Zoom Page Number Stability & Fractional Anchoring

During visual zoom operations, relative page offsets can drift if viewport positions are mapped only to raw scroll values.

To ensure the page number indicator in the toolbar never jumps erratically during zoom:
* Riemann computes the fractional page offset `(scrollbar_value - current_page_top) / current_page_height` prior to scaling.
* Post-scaling, the viewport scroll position is recalculated using the scaled dimensions and restored to the exact fractional page position.

---

## High-Zoom Performance Optimizations

At higher zoom levels (200%–400%), single page bitmaps expand to 50MB–70MB each. Synchronously pre-rendering offscreen buffers can overwhelm `QPixmapCache` (150MB ceiling) and block the Qt event loop for seconds.

Riemann applies three coordinated optimizations:
1. **Scale-Aware Pre-Render Margins**: In `render_visible_pages`:
   * Zoom $\ge 200\%$: `margin = 1` (limits pre-rendering to only the immediately adjacent page).
   * Zoom $\ge 120\%$: `margin = 2`.
   * Standard zoom: default margin.
2. **Visible-First Priority Queue**: Pages scheduled for rendering are sorted by distance to `current_page_index`:
   ```python
   sorted_indices = sorted(target_indices, key=lambda i: abs(i - self.current_page_index))
   ```
   The active visible page renders first within 50ms–100ms, immediately providing visual clarity without waiting for buffer pages.
3. **Re-entrancy Elimination**: Removed synchronous `QApplication.processEvents()` calls within zoom completion callbacks, avoiding recursive paint events and layout freezes.

---

## Virtualized Rendering

Large PDFs automatically transition into virtualization mode.

Instead of creating every page widget simultaneously:

* spacer widgets simulate offscreen page space
* only nearby pages exist as active widgets
* rendering occurs lazily

Benefits:

* lower RAM usage
* fewer QWidget allocations
* smoother continuous scrolling
* reduced Qt layout overhead

---

## Facing-Page Logic

Facing-page mode dynamically pairs widgets into row containers.

Important detail:

* odd/even alignment adjustments are required to avoid broken page pairing

Most virtualization bugs historically originated from page pairing edge cases.

---

## Rebuild Debouncing

Full layout rebuilds are expensive.

The rendering system therefore:

* debounces rebuild execution
* delays viewport refreshes
* batches scroll-triggered updates

Do not directly trigger aggressive rebuild loops from wheel events.

---

## Scroll Preservation

Layout rebuilds attempt to preserve:

* logical page position
* relative scrollbar ratio
* viewport continuity

This is especially important when switching:

* zoom modes
* facing mode
* continuous mode
* virtualization state

`_set_scroll_value_silently()` moves the scrollbar while `_ignore_scroll` is set, so the scroll handler skips that change. Do not use `blockSignals()` on the scrollbar for this: `QScrollArea` moves its content in response to the scrollbar's `valueChanged`, so a blocked signal leaves the scrollbar and the content out of step and pages render off screen. `rebuild_layout()` unblocks signals before restoring the position for the same reason.

---

## Device Pixel Ratio Handling

Rendering uses Qt device-pixel-ratio-aware scaling.

Without this:

* HiDPI rendering becomes blurry
* selection rectangles desynchronize
* annotation overlays drift

---

# 5. Annotation Pipeline

## `mixins/annotations.py`

Handles all interactive annotation behavior.

Core responsibilities:

* annotation persistence
* undo/redo stacks
* drawing tool selection
* coordinate-space conversion
* annotation serialization
* visual refresh invalidation

---

## Annotation Storage

Annotations live in two places:

* **Riemann's JSON copy**, in `<app data>/annotations/`, written on every change: `{"pages": {page: [annotation, ...]}, "pdf": [size, mtime_ns], "written": ...}`. `core/annotation_store.py` names each copy by the PDF's `/ID`, so it survives renames and moves; files without an `/ID` fall back to a hash of the path. Copies from older versions (`~/.local/share/riemann/annotations/<sha256 of the path>.json`) move there the first time their file opens. Every annotation has an `id`.
* **The PDF itself** (`core/pdf_annotations.py`), about 1.2 s after the last change, on a worker thread, and synchronously when the tab closes. Each annotation becomes a standard PDF annotation: notes `/Text`, text labels `/FreeText` (with an appearance stream), markup `/Highlight`, `/Underline` or `/StrikeOut` with `/QuadPoints`, pen and highlighter strokes and stamps `/Ink`, rectangles `/Square`, ovals `/Circle`. Each is named `riemann:<id>` in `/NM` and carries the original dict as JSON in `/RiemannData`.

The PDF is changed with an **incremental update**: pikepdf parses the file, Riemann's previous annotations are dropped from each page's `/Annots`, the new objects and changed page dictionaries are serialised, and an xref section (a classic table or an xref stream, matching the file) plus trailer are appended. The original bytes stay untouched, so existing signatures stay valid and the copy PDFium has open is unaffected. The append is skipped if the file changed size while being parsed. Encrypted files, files with signature fields (`/SigFlags`) and read-only files are not written; their annotations stay in the JSON copy.

**Reading back**: on load, if the file's size and mtime differ from the ones recorded after Riemann's last write, another program changed it. If the file then holds Riemann annotations, they are merged with the JSON copy by annotation `id` (`merge_annotations`), taking `/Contents`, `/C` and position edits made elsewhere (`/RiemannRect` records where Riemann put each one). If it holds none (for example a regenerated PDF), the JSON copy is written back into it.

**Growth**: every incremental save appends the changed pages. After `COMPACT_AFTER_UPDATES` (10) appended updates, a flush rewrites the file in full on a worker thread, unless it is signed.

**Rendering**: Riemann draws its annotations itself, so the core hides `riemann:` annotations from PDFium's renderer (in memory only) and leaves them out of `get_comments()`, which lists other applications' comments.

---

## Signature Appearances

Acrobat's older signing tools store a layered signature appearance: `n0` background, `n1` a "validity unknown" icon (a yellow question mark), `n2` the signer text, `n3` an "invalid" layer and `n4` status text such as "Signature Not Verified". Acrobat never shows `n1`/`n4` as stored; it draws its own validation result in their place. PDFium renders the stored appearance as is, so without help the "?" stays after a successful check.

After `SignatureValidationWorker` finishes, `core/signature_appearance.py` builds, for each field whose result is VALID (intact and trusted) or INVALID, a one-page PDF: the page cropped to the field, without annotations, with a copy of the field's appearance on top in which `n1` is a green tick or red cross and `n4` is empty. The reader renders it with PDFium (`_signature_appearance_image`, cached per size and theme) and paints it over the field in `_render_overlays`, so the signer text, fonts and whatever lies behind the field stay exactly as stored. Fields whose signer is not trusted keep their stored look, which is accurate. The signed file is never modified. Appearances without `n1`/`n4` layers (modern signers) are left alone.

---

## Overlay Rendering

Temporary overlays are drawn separately from committed state.

This enables:

* live previews
* drag feedback
* translucent markup previews
* smoother freehand rendering

---

## Undo/Redo Model

Undo stacks store:

* page index
* annotation references
* operation type

Redraws are localized to the affected page whenever possible.

---

## Precision Text Selection Engine

The text selection system (`mixins/selection.py`) was overhauled to handle LaTeX, math notations, scanned papers, and OCR output with high visual and clipboard accuracy:

* **Glyph Boxes from PDFium**: `get_char_boxes()` in the core returns every character with its loose bounding box. `_runs_from_char_boxes()` splits them into runs on one line (at generated line breaks and vertical jumps) and gives generated spaces the gap up to the next glyph. Older core builds fall back to estimating widths inside each text segment from font advances.
* **Caret Semantics**: a drag selects between caret positions, so a letter is included only once the pointer passes its middle, as in text editors.
* **Vertical Baseline Alignment**: Selection rectangles are shifted down by +10% of font height to align strictly with rendered glyph baselines rather than ascender ceilings.
* **Reading-Order Overlap Clustering ($\ge 40\%$)**: Text segments extracted from PDFium or OCR are clustered into reading lines using a 40% vertical overlap threshold and sorted left-to-right. This prevents multi-column text, marginalia, and inline formulas from interleaving when selected.
* **Double-Click Word Selection (`_get_word_at_pos`)**: Double-clicking expands backward and forward across characters on the same line until whitespace or punctuation boundaries, unifying the word into a single highlight rectangle.
* **Triple-Click Paragraph/Sentence Selection (`_get_paragraph_at_pos`)**: Triple-clicking expands to select the current line, sentence, or paragraph:
  * Backward and forward expansion stops at line breaks (`vert_gap > 0.5 × height`) or significant horizontal whitespace gaps (`horiz_gap > max(20px, 1.5 × height)`).
  * Constructs the selection rectangles directly from character indices without redundant distance searches.
* **Multi-Click Release Shield (`_just_selected_multi_click`)**:
  * On mouse press/double-click, `_just_selected_multi_click` is set to `True`.
  * When `MouseButtonRelease` fires after clicking without dragging, this shield prevents the single-click dismissal branch from wiping out the newly selected word or paragraph.
  * Subsequent single clicks after 0.55s dismiss the selection as expected.
* **Bounded Geometry Cache**: `_char_geometry_cache` is capped at 8 entries via FIFO eviction to eliminate unbounded memory growth during continuous reading.

---

## External PDF Comments & Annotations Ingestion

Beyond native Riemann annotation overlays, `AnnotationsMixin` supports discovering, parsing, and interacting with annotations embedded by third-party PDF readers (e.g. Adobe Acrobat, Apple Preview, Okular):

* **Ingestion Layer (`_load_external_comments_async()`)**:
  * `ReaderTab._on_document_loaded` starts it on a daemon thread. The result comes back through `_CommentsBridge.loaded`, a queued signal, and `_on_external_comments_loaded` drops it if the tab has moved on to another file.
  * When the document has `get_comments` (the Rust core), `read_comments_from_core()` reads every page through PDFium. The core skips links, widgets, popups and annotations with empty contents, and the GIL is released while it runs.
  * Otherwise `read_external_comments()` falls back to `pypdf.PdfReader`, traversing `/Annots` arrays on each page without altering original file binaries. The pypdf path parses the whole file in Python and took about 2 s on the 756-page PDF specification, which is why it no longer runs on the UI thread.
  * `_load_external_comments()` is the synchronous pypdf version, kept for callers that need the result immediately.
  * Filters for interactive annotation subtypes: `/Text` (sticky notes), `/FreeText`, `/Highlight`, `/Underline`, `/StrikeOut`, `/Squiggly`, `/Square`, `/Circle`.
  * Extracts metadata dictionaries containing:
    * `/T`: Author / creator name.
    * `/M`: Modification timestamp.
    * `/Contents`: Textual comment body.
    * `/Rect`: Bounding coordinate rectangle in PDF points.
* **Coordinate Space Normalization**:
  * Native PDF geometry uses points ($72 \text{ pt/inch}$) with the origin `(0, 0)` located at the **bottom-left** of the media box.
  * Riemann converts coordinates to image pixel space (origin at **top-left**):
    ```python
    scale_x = img_width / page_width
    scale_y = img_height / page_height
    norm_x = x1 * scale_x
    norm_y = (page_height - y2) * scale_y
    norm_w = (x2 - x1) * scale_x
    norm_h = (y2 - y1) * scale_y
    ```
* **Interaction & Hit Testing (`_get_comment_at_pos`)**:
  * Employs an expanded 10px hit-test margin to ensure small note icons or thin highlight strokes are easy to click.
  * In `ReaderTab.eventFilter`, mouse hover events set `Qt.CursorShape.PointingHandCursor` and show the comment preview as a tooltip.
  * Clicking on the comment opens `CommentViewDialog`, displaying author, timestamp, and the complete scrollable comment text.


## Redaction

Redaction marks are annotations of type `redact`: the Redact tool on the annotation toolbar drags a box (a click without a drag marks nothing), and "Mark for Redaction" in the context menu marks the selected text. They are drawn as a red outline with a faint fill (`CONTENT["redact_mark"]`) and written to the PDF as standard `/Redact` annotations with QuadPoints, so other editors see them as pending redactions too.

"Apply redactions" (`AnnotationsMixin.apply_redactions`) confirms first, listing what is and isn't covered, then runs `core/redaction.apply_redactions(path, marks, keep, password)` through `_edit_pages`, so it works on the scratch copy and the file changes only on Save. The engine is Python on pikepdf, because PDFium's editing API can set a text object's character codes but never read them, and rebuilding text from Unicode breaks subset fonts.

* It interprets each marked page's content stream (graphics and text state, fonts, Form XObjects) to find every glyph. A glyph whose box overlaps a mark is cut out of its string and replaced by the TJ spacing it took up, so the other glyphs keep their exact position, codes, font, size and colour. Invisible OCR text goes the same way.
* Images are blacked out pixel by pixel under the mark (an image entirely inside a mark is dropped); vector paths are dropped only when they lie entirely inside one. Annotations, links and form fields touching a mark are removed, except the ids in `keep` (a Replace text overlay over its own mark). A black box is painted over each mark, and `/Thumb` is dropped.
* A Form XObject or image that needs changing is copied for this page under a new name, and the page's XObject dictionary is pruned to what its content still draws; a stale entry would keep the unredacted original in the file.
* Where removal can't be exact (unusual CMaps, vertical writing, missing widths, text used as a clip, partly covered inline images or image masks, nesting deeper than `MAX_DEPTH`), the whole Apply stops with a `RedactionError` naming the page, and nothing changes.
* The result is saved in full, never as an incremental update, so old streams don't survive. Before it replaces the source, `verify_redaction` checks it with PDFium: no character may remain mostly inside a mark, and every character clear of the marks must still be there within `POSITION_TOLERANCE` (0.01 pt).

Not covered, as the dialog says: XMP metadata, bookmarks, attachments, `/ActualText` in the structure tree, and font subsets (which keep the outlines of removed glyphs). Tests: `tests/test_redaction.py`, with fixtures from `tests/text_fixtures.py`.

## Replace Text

"Replace Text…" in the context menu (`replace_selected_text`, `ui/reader/replace_text.py`) draws new words over a selection as a `replace` annotation: a FreeText box whose appearance stream fills the background first and then writes the text in a PDF standard font (`/RmF`).

* The box comes from the selection's line boxes, widened or deepened if the new text needs more room; nothing reflows.
* The font is the standard font closest to the original (`standard_font`): Helvetica, Times or Courier, bold or italic to match, from the PDFium flags and, because those are often wrong for built-in fonts, the font name. Size, colour, baseline and line pitch come from the original characters (`get_char_styles`).
* The background is the most common colour in a thin ring around the selection (`ring_colour`), sampled from the page rendered in the light theme, so tinted paper doesn't get a white patch.
* On rotated pages the appearance gets a `/Matrix` turn so the text stays upright on screen. In the dark page themes the overlay's colours are mapped the way the Rust core maps the page (`themed`).

The old text stays in the file under the box. The dialog's "Remove the original text too" adds a redaction mark under the same selection, which Apply redactions then removes while keeping the overlay. Tests: `tests/test_replace_text.py`.

---

# 6. Search & Text Extraction Systems

## Rust Search Delegation

Search operations are delegated into the Rust backend.

Rust returns:

```text
[x, y, width, height]
```

bounding rectangles.

Qt then converts these into viewport-space overlays.

---

## Links and Background Rendering

**Links.** `RiemannDocument.get_links(page)` (Rust) resolves every link annotation on a page. Internal links, whether explicit destinations, named destinations or GoTo actions, come back as `#page=N`, plus `&top=Y` when the destination names a vertical position (PDF points, origin at the page bottom). External links come back as their URI. `RenderingMixin._render_links` caches the raw tuples per page in `_raw_links_cache` and scales them to widget coordinates with a couple of pixels of slack. `ReaderTab._parse_internal_link` decodes the target, and `jump_to_page(idx, top_pts)` scrolls so that the destination sits at the top of the viewport. A press that lands on a link tolerates 14 px of jitter before it turns into a text selection.

**Rendering.** Pages are rasterised off the UI thread by `PageRenderWorker` (`ui/reader/render_worker.py`):

* The Rust core releases the GIL while PDFium renders. A PDFium lock per process serialises every call into the library, including document loads and closes, because PDFium is not thread-safe.
* Each reader renders in a child process of its own (`core/render_process.py`), so a slow page in one tab doesn't hold up the others. When a document loads, the tab wraps it in a `PageRenderer`, which starts `python -m riemann.core.render_process` (in a frozen build, the executable with `--riemann-render-process`, checked in `build_entry.py` before Qt is imported). The child opens the same file with its own PDFium and answers render requests over stdin and stdout: a pickled header, then the raw pixels. The render worker, the sidebar and printing all get the `PageRenderer` in place of the document. Its `render_page` goes to the child once the child has the file open; every other attribute (text, links, forms, outline) is the main process's document. Until the child is ready, or if it fails to start or dies, pages render in-process as before, and the failure is logged once. The child imports no Qt and exits when its stdin closes. Page edits close it before rewriting the file, since on Windows an open file can't be replaced. `RIEMANN_RENDER_PROCESS=0` turns children off. Tests: `tests/test_render_process.py`.
* `render_visible_pages()` queues jobs for the pages around the reading position. A page with nothing on screen first gets a cheap thumbnail at a quarter of the target resolution, then the full-resolution render. The worker takes jobs in order of distance from the current page.
* Finished images land in `PAGE_CACHE`, a byte-budgeted LRU (384 MB) shared by all tabs and keyed by `(tab, document generation, page, render scale, theme)`. Rebuilding the virtual layout, scrolling back, or switching tabs re-displays cached pages instantly. Annotations and search highlights are composited onto a copy at display time, so `rendered_pages.remove(idx)` stays cheap.
* The worker also fetches each page's links and form fields while it already holds the PDFium lock, so the UI thread never waits on that lock.
* Scroll handling is throttled (40 ms) rather than debounced, so pages keep loading during continuous scrolling, including middle-click autoscroll.
* Fit-to-width ignores whether the vertical scrollbar is visible (`_scrollbar_allowance`). Otherwise the scale would change every time the scrollbar appears, and renders already in flight would be thrown away.
* `apply_visual_zoom()` anchors the point at the centre of the viewport, flushes the layout so the scrollbar range matches the new page sizes, and only then repositions. Before this change, switching from Fit Height to Fit Width clamped the position to the old, smaller range.

---

## Text Segment Cache

ReaderTab maintains a text segment cache.

This cache powers:

* text selection
* search highlights
* link interaction
* AI extraction

Avoid invalidating the cache unnecessarily.

---

# 7. Browser Architecture

## `ui/browser/`

This package contains the primary Chromium integration layer. `tab.py` is `BrowserTab`; `chrome.py` is its one mixin (toolbar, find bar, shortcuts, theme, zoom, bookmarks). The rest are classes the tab owns, each given what it works on (the web view, the captions server, the widget its dialogs open over) and reporting through signals, so each can be built in a test without a `BrowserTab`: `MediaControls` (`media.py`: captions, music and video modes, mute), `StreamLauncher` and `VideoPlayer` (`stream.py`: outside players and the built-in one) and `Downloads` (`downloads.py`: files, videos, saving a page as PDF). The tab turns its buttons into calls on them and their signals into toasts, the loading line and calls on the window. `interceptor.py` decides what pages may load, `page.py` is the `WebPage`, `ytdlp.py` holds the yt-dlp workers and `preview.py` the `PreviewTextTab`.

Responsibilities:

* QWebEngineView management
* profile handling
* download orchestration
* media integration
* JavaScript injection
* yt-dlp workflows
* caption integration
* WebAudio integration
* browser homepage logic

---

## Leaving Web Pages

A site that wants to warn before it is left (unsaved form text, a draft) registers a `beforeunload` handler. Chromium then asks through `QWebEnginePage.javaScriptConfirm` with Qt's fixed `LEAVE_PROMPT` text, and `WebPage` shows that question as Leave or Stay (`ask_to_leave`) on the tab it belongs to, brought to the front. Other `confirm()` calls go to Qt's default dialog. Navigating away, reloading and closing all go through this.

Closing a tab, a window or the app calls `BrowserTab.confirm_leave()` first. It sends the page `WebAction.RequestClose` and waits in a local event loop for Chromium's answer: `windowCloseRequested` (no handler, or Leave), or `WebPage.leave_refused` (Stay). A page without a handler answers within a millisecond or two. A page whose renderer never answers is let go after `LEAVE_WAIT_MS`. Chromium asks only on pages the person has interacted with. Closing a tab no longer blanks the page with `setHtml("")`, which was a navigation of its own.

---

## Persistent vs Incognito Profiles

Persistent browsing uses shared `QWebEngineProfile` instances.

Incognito sessions use off-the-record profiles.

Changing profile behavior can affect:

* cookies
* cache
* downloads
* login persistence
* injected scripts

---

## Browser Zoom Persistence

Zoom levels are persisted per domain/subdomain.

This logic intentionally avoids global zoom state.

The zoom system was redesigned to reduce aggressive setting writes and zoom jitter.

---

## Download Infrastructure

Qt-native downloads and yt-dlp downloads coexist.

Qt downloads:

* standard browser downloads
* PDFs
* browser assets

yt-dlp downloads:

* streaming platforms
* playlists
* subtitle embedding
* media extraction

Do not mix both systems carelessly.

---

## yt-dlp Video & Playlist Architecture

The yt-dlp integration in `ui/browser/ytdlp.py` manages asynchronous video and playlist retrieval:

* **Challenge Execution & Deno Environment**:
  * YouTube's challenges are solved by the pinned `yt-dlp-ejs` package in the pinned Deno (`js_runtimes`); yt-dlp is never given `remote_components`.
  * Checks and appends `~/.deno/bin` to `os.environ["PATH"]` if present, for a Deno installed outside the optional packages.
* **Errors and Cancelling**:
  * `YtDlpWorker` is yt-dlp's logger. With `ignoreerrors` a failed video is only logged, so the worker keeps the errors and reports the last one instead of "Download complete!".
  * Stop is checked on every log line as well as on every progress tick, so it works during extraction and retries too.
* **Automatic Playlist Detection**:
  * In `BrowserTab.download_video()`, checks if the target URL contains `list=` or `/playlist`.
  * If detected, passes `is_playlist=True` to `YtDlpSettingsDialog`, auto-checking the "Download playlist" option.
* **Output Template Formatting**:
  * Single video: `%(title)s.%(ext)s`.
  * Playlist: `%(playlist_index&{:02d} - |)s%(title)s.%(ext)s`, cleanly ordering downloaded items by their track index.
* **Cumulative Progress Tracking (`core/managers.py`)**:
  * In `YtDlpDownloadManager.update_progress()`, progress is normalized across all items in the playlist:
    $$\text{Total \%} = \frac{(p_{\text{idx}} - 1) + \frac{\text{video \%}}{100}}{p_{\text{count}}} \times 100$$
  * Prevents UI progress bars from resetting back to 0% as each successive video begins downloading.

---

# 8. JavaScript Injection Layer

## Browser-Side Injection Strategy

Riemann injects several enhancement scripts directly into webpages.

Injection targets include:

* dark mode adaptation
* media overlays
* caption rendering
* playback augmentation
* ad skipping
* keyboard behavior fixes

These scripts execute inside Chromium renderer processes.

---

## `caption_engine.js`

Implements the browser-side live caption overlay.

Core responsibilities:

* capturing media streams
* opening WebSocket connections
* streaming audio chunks
* draggable overlay rendering
* dynamic font scaling
* overlay positioning

The overlay intentionally operates independently from webpage DOM structure.

---

## Audio Streaming

Audio is captured through:

```javascript
video.captureStream()
```

The stream is piped into:

* AudioContext
* ScriptProcessor
* Float32 buffers
* WebSocket transport

---

## `audio_engine.js`

Implements browser-side WebAudio DSP.

This system dynamically inserts:

* EQ nodes (Low Shelf, High Shelf)
* saturation
* compressors
* downward peak limiter
* analyzers
* stereo widening
* reverb

### Settle Window & Gain Stability Algorithm
Dynamic gain normalization across diverse tracks previously suffered from volume pumping during quiet intros, acoustic breakdowns, or speech pauses.

To achieve studio-quality stability without manual volume tweaking:
* **Settling Duration**: A configurable settle window (3s–30s, default 12s, exposed via the audio overlay slider) allows the automatic gain normalization loop to converge on the track's average loudness early in playback.
* **Baseline Gain Lock**: After the settle window elapses, the engine locks onto the calculated baseline gain for the duration of the track, ignoring quiet passages and resisting artificial volume inflation.
* **Downward Limiter**: A brickwall lookahead limiter catches sudden transients, loud climaxes, or dynamic shifts without distorting the audio or causing crackling.

Modifying the audio graph incorrectly can easily introduce:

* clipping
* latency
* desynchronization
* CPU spikes

---

# 9. Media & Audio Infrastructure

## MiniAudioPlayer Architecture & Cross-Window Polling

Located in:

```text
core/mini_player.py
```

`MiniAudioPlayer` is a compact native controller hosted directly within the main window's menu bar (`QMenuBar`). It bridges native Qt controls to in-browser HTML5 media playback via asynchronous JavaScript execution.

Features & Subsystem Design:

* **App-Wide Cross-Window Discovery (`_get_all_app_browsers()`)**:
  * Gathers all active `BrowserTab` instances across all application windows via `RiemannWindow._all_open_windows` and `QApplication.topLevelWidgets()`.
  * Allows media playback in any window or split-view pane to be monitored and controlled from the active menu bar.
* **C++ Pointer Validity Safety (`_is_browser_valid()`)**:
  * Tab widgets can be closed, detached, or garbage-collected while polling timers are running.
  * Uses `shiboken6.isValid(browser)` and verifies the existence of `browser.web` and `browser.web.page()` to prevent dangling C++ pointer access and segmentation faults.
* **Audible Prioritization & Active Session Retention (`find_active_browser()`)**:
  1. Checks if the currently focused tab in `tabs_main` or `tabs_side` is a valid browser tab. If it is audible (`page.recentlyAudible()`), it is selected.
  2. Traverses all discovered browser tabs across all open windows for any tab actively playing sound (`recentlyAudible()`).
  3. If currently controlling a valid browser tab (`self.active_browser`), retains tracking even when the user switches to a PDF reader tab, another window, or a non-browser tab.
  4. Falls back to the current tab of `tabs_main` or any available browser tab.
* **Native Playback Controls**:
  * Play/Pause: Dispatches JavaScript to locate `<audio>` or `<video>` elements and invoke `.play()` or `.pause()`.
  * Seek: Synchronizes `QSlider` releases with `media.currentTime = seek_val`.
  * Timeline: Formats current playback time and total track duration (`MM:SS / MM:SS`).
* **Adaptive Visibility & Layout Recomputation**:
  * Hides cleanly when no valid media is playing (`self.active_browser is None`).
  * Calls `adjustSize()` and `updateGeometry()` on parent widgets and the menu bar when visibility toggles to ensure seamless navbar layout recalculation.

---

## Polling Strategy

The player intentionally uses periodic polling (500ms `QTimer` interval) instead of complex event synchronization.

Reasons:

* browser isolation boundaries
* renderer process unpredictability
* seamless recovery if a media page navigates or refreshes

---

## Native Stream Extraction

yt-dlp stream extraction allows playback through:

* MPV
* VLC
* QtMultimedia

This bypasses unsupported Chromium codecs.

`YtDlpStreamWorker` (`ui/browser/ytdlp.py`) finds the direct URL without downloading. It passes the pinned Deno (`js_runtimes`) whichever cookie source the person picks, a browser's cookies or a cookies.txt file. It reports the URL through `found`, not `finished`: a signal of that name would hide `QThread.finished`, which `ui/threads.track` listens for to let the worker go. No worker in the app names a signal `finished` (`tests/test_source_rules.py` checks this); the others report through `done` or `succeeded`.

---

# 10. Live Captioning Pipeline

## `core/captions.py`

Contains the Faster-Whisper integration.

Features:

* rolling transcription memory
* translation mode
* VAD filtering
* asynchronous chunk processing

The rolling context buffer improves transcription continuity.

---

## `core/captions_server.py`

Implements the Qt-native WebSocket caption server.

Responsibilities:

* WebSocket hosting
* client management
* worker thread routing
* chunk dispatching
* response delivery

---

## Worker Thread Model

Audio transcription executes in background worker threads.

Never run transcription synchronously inside the Qt UI thread.

Doing so will immediately freeze:

* scrolling
* browser rendering
* animations
* repaint events

---

# 11. AI Infrastructure

## AI Engine Process

Semantic search runs in a child process of the app (`core/ai_server.py`), so a crash or a slow embedding never touches the UI, and the process only exists for people who use AI search. It embeds with all-MiniLM-L6-v2 exported to ONNX, on onnxruntime with the model's own tokenizer: no PyTorch, no faiss, no PDF library. The model (about 90 MB) is downloaded on first use from Hugging Face at a fixed commit and checked against pinned SHA-256s (`MODEL_COMMIT`, `MODEL_FILES`); the embeddings match sentence-transformers to 1e-7.

---

## Semantic Search Engine

`core/ai_engine.py` owns the one engine process the whole app shares. `ensure_started()` launches it on first use (`python -m riemann.core.ai_server` from a checkout, the frozen executable with `--riemann-ai-sidecar` in a build; `build_entry.py` checks that flag before Qt loads) with environment variables for the model folder (`RIEMANN_AI_MODELS`), the vector cache (`RIEMANN_AI_CACHE`) and two more: `RIEMANN_AI_PORT`, a free localhost port it picked, and `RIEMANN_AI_TOKEN`, a random secret. The sidecar refuses any request without that token (`X-Riemann-Token` header), so web pages and other local programs cannot use it to read PDFs. `stop()` runs at exit; closing a tab never stops it. Its log is `~/.cache/Riemann/ai_engine.log` (`%LOCALAPPDATA%\Riemann` on Windows).

Reader tabs (`mixins/ai.py`) talk to it over HTTP: `POST /index` and `POST /search`, one request at a time per tab so a search never overtakes the indexing of its PDF. The server listens at once and answers 503 with a "loading ..." detail (download progress included) until the model is ready; the tab retries every second and shows the detail. `/index` goes first without text; unless the engine has cached vectors it answers `needs_text`, and the tab reads every page's text on a `PageTextThread` from its open document (password-protected files work) and sends it. A PDF is indexed when the AI search bar opens or on the first query, never on open. The sidecar keeps one index per PDF path (up to 16) and reuses it while the file's modification time is unchanged, so tabs showing different documents get their own results.

`POST /search_library` searches every paper with vectors at once (the Library tab's "Search inside papers") and lists the others in `needs_text`; `ai_engine.search_library` reads those through the Rust core, indexes them and asks again, and reports papers it can't read as skipped. Embeddings are cached on disk under `RIEMANN_AI_CACHE`, keyed by path, size, modification time, chunking and model. Tests: `tests/test_ai_server.py` (offline; set `RIEMANN_TEST_AI_MODELS` to a folder, empty or holding the model, to also run the real model and a real child process: the tests download the pinned model into it when it is missing. `tests.yml` does this with the folder kept in the Actions cache).

---

## Snip-to-AI

Flow:

```text
Selection Rectangle
→ Page Capture
→ PNG Encoding
→ Local API Request
→ Response Rendering
```

Coordinate transforms are shared with:

* annotation geometry
* search highlighting
* text selection

Modifying one system may affect all three.

---

## Optional Dependency Strategy

Heavy AI dependencies are dynamically managed.

This prevents the default build from becoming excessively large.

Managed packages include:

* torch
* torchvision
* transformers
* pix2tex
* faster-whisper
* scipy
* pandas

---

# 12. Persistent Managers & State Systems

## `core/managers.py`

Contains several major persistence managers.

---

## LibraryManager

Backed by SQLite.

Stores:

* file metadata
* DOI data
* arXiv IDs
* author information

Search supports:

* keyword matching
* `author:` filters
* `year:` filters

---

## HistoryManager

Stores:

* PDF history
* web history
* folder history

Features:

* **Timestamps**: entries are stored as `{"path": ..., "timestamp": ISO 8601}`; bare paths from older versions load with an empty time. `get_list()` returns paths, `get_entries()` the full entries, which the History dialog shows with names, locations and times.
* **Immediate Persistence**: History entries are written immediately upon opening a document or web page, rather than deferring writes until application exit. This guarantees that recently opened documents are preserved even if the application is killed abruptly.
* **Crash Recovery State**: Tracks session exit state using a `_session_cleanly_closed` flag. If an unclean shutdown is detected on startup (e.g. system power outage or crash), the application prompts the user with a recovery dialog to restore all previously open tabs.
* **Autocomplete Engine**: Powers address bar and file path suggestions across all viewports.
* **Format Migration**: Automatically upgrades legacy JSON history formats.
* **Document classes**: PDFs, Markdown, code and text files are all stored under the `pdf` key (installs have it on disk). The History tab's Documents list shows a Type column and a filter by class, from `core.doctypes.document_class` (PDF, Markdown, Code, Text), which goes by name and reads only a file whose name says nothing. HTML files are web history.

---

## DownloadManager

Qt-native download management dialog.

Features:

* progress visualization
* pause/resume
* persistent download history
* cleanup workflows
* open-file integration

---

## BookmarkManager

JSON-backed lightweight bookmark persistence.

Intentionally simple by design.

---

# 13. Document Conversion & Compression

## `core/features.py`

Contains:

* conversion utilities
* compression utilities
* export dialogs
* media helpers

---

## Conversion Infrastructure

Supported conversions:

* PDF → Images
* PDF → Markdown
* PDF → HTML
* Images → PDF
* CSV → HTML
* EPUB → HTML

PDFs are read through the Rust core: PDF → Images renders each page at the chosen DPI, and text and HTML come from the same text extraction the reader uses (HTML places each text segment where it sits on the page). Images → PDF uses Pillow, one page per image at 72 dpi, flattening transparency onto white.

---

## Compression Strategy

PDF compression uses pikepdf. Lossless mode removes unreferenced resources, packs objects into object streams and recompresses Flate streams. Lossy mode also re-encodes each image XObject once (pages may share one) as JPEG at the chosen quality, keeping the original wherever the JPEG would not be smaller. Stencil masks and images under 8 bits per channel are left alone, and a soft mask stays attached, so transparency survives. Tests: `tests/test_compression.py`.

PyMuPDF (AGPL-3.0) is not used anywhere in the app or the AI engine.

---

# 14. Workspace & Tab Infrastructure

## `ui/components.py`

Contains:

* draggable tab systems
* annotation toolbars
* reusable widgets

---

## Draggable Tabs

Tabs support:

* reordering within the strip (Qt's movable tabs)
* split-view transfer
* window detachment
* external file drops

`DraggableTabWidget` sets `setMovable(True)` after installing its `DraggableTabBar`; the flag belongs to the bar, and setting it first left the custom bar unmovable. A tab becomes a drag (MIME `application/x-riemann-tab`) only once it is pulled 30 px off the strip (40 px sideways for vertical tabs); Qt's in-strip move is ended first. A drop on a strip lands before the first tab whose middle is past the pointer (`drop_index()`); a drop on a pane's content goes back to its original place in the same pane, or after the current tab in the other one.

Titles are set in full; the bar elides them (`ElideRight`) only when the tabs no longer fit.

---

## Split View

In split view each pane's `QTabWidget::pane` gets a rounded 2 px outline: the accent colour for the pane in use (tracked through `QApplication.focusChanged`) and a neutral one for the other. The other pane also shows a `SplitPaneBadge` chip naming its tab; clicking it focuses that pane and its close button ends the split. Splitter handles are a transparent gap that lights up on hover.

---

## Help Page

`ui/system/help_tab.py` is the Help System tab (F1, the Help menu, or the command palette). Its text is data: `TOPICS` (sections of titled cards) and `SHORTCUTS` (key tables per place: everywhere, in a document, on a web page, in presentation mode, in the AI panel). It is written for someone opening Riemann for the first time, so keep new entries in plain words. `tests/test_help.py` fails when the window, a reader, a browser tab or the AI panel registers a `QShortcut` that `SHORTCUTS` doesn't list. Keys read in a `keyPressEvent` (A, W, H, R, C, D, F, the presentation keys) aren't checked and must be added by hand.

---

## AI Assistant

`AiSidecarPanel` (`ui/sidecar.py`) docks at the right of `main_splitter`. Providers: Anthropic, OpenAI, Google Gemini, OpenRouter and Ollama. Model lists are fetched from each provider (`list_models()`), so there is no hard-coded list to go stale; only Anthropic has a fallback list for when the request fails. Requests stream through `urllib` (`stream_chat()`: SSE for the hosted APIs, NDJSON for Ollama) on a worker thread. The system prompt carries the document text with `[Page N]` markers (`document_context()`, at most 150,000 characters, nearest pages first) and asks for `[p. N]` citations, which `link_citations()` turns into `page:N` links that jump the reader.

Keys come from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `OPENROUTER_API_KEY`) or, when those are unset, from `ai.env` in Riemann's config folder (`$XDG_CONFIG_HOME/Riemann/ai.env`, usually `~/.config/Riemann/ai.env`; `%APPDATA%\Riemann\ai.env` on Windows; `RIEMANN_AI_ENV` overrides the path). It holds `NAME=value` lines with the same variable names, so it can also be sourced by a shell. Handling:

* the folder is created `0700` and the file `0600`, written to a temporary file opened with mode `0600` and then renamed over the old one, so the keys are never readable by other users, even mid-write;
* if the permissions are found looser on read (someone ran `chmod 644`), they are reset to `0600` before the keys are used;
* on Windows `%APPDATA%` is already private to the user account through its ACL;
* keys saved by earlier builds under `ai/keys/<provider>` in the plain settings file are moved into `ai.env` and deleted from the settings when the panel opens;
* keys are never logged or shown; the dialog uses password fields.

Google keys go in the `x-goog-api-key` header, never the URL. The Ollama address (not a secret) stays in the settings as `ai/ollama_url`.

---

## Scrollbars

Scrollbars are 16 px wide with a 2 px margin around the handle (`modern_dark.css`, `modern_light.css`), so the handle is 12 px; the Workspaces dashboard and the PDF start page use 14 px.

---

## Tab Hover Tooltips

`DraggableTabBar` overrides `eventFilter` to intercept mouse hover events (`QEvent.Type.ToolTip`):

* **Styling**: Renders a floating tinted tooltip with subtle drop shadows and translucent background (`rgba(26, 26, 46, 0.95)`).
* **PDF Tabs**: Resolves the tab's underlying `current_path`, formatting the full filename and absolute filesystem directory path.
* **Browser Tabs**: Resolves the page title and parses the URL via `urllib.parse` to extract the root domain or top-level subdomain (e.g. `docs.python.org`, `github.com`), avoiding raw unreadable URL clutter.

---

## Empty State Drop Zone Overlay

When all document tabs are closed, `RiemannWindow` displays a centered drag-and-drop placeholder:

* Visual target: prominent "Drop PDF here" banner styled to match the active theme.
* Event Routing: a file dropped on an empty reader goes through `open_here` (see Opening From Inside a Reader), so it opens in the current open mode and is recorded in history; dropped on a reader showing a document, it opens in a new tab.

---

## Detached Windows

Dragging tabs outside the window creates new top-level windows.

This logic is heavily geometry-dependent.

Be cautious when modifying:

* drag thresholds
* cursor handling
* tab removal timing

---

# 15. Explorer & Favorites Systems

## `ui/explorer.py`

Implements the file explorer panel.

Features:

* themed icons
* file previews
* proxy filtering
* custom context menus
* adaptive icon handling

---

## Icon Proxy Model

Custom proxy models dynamically swap icons depending on:

* file type
* theme mode
* dark/light state

---

## Favorites

Implemented in:

```text
ui/favourites.py
```

Uses QSettings persistence.

Supports:

* favorite folders
* persistent quick access
* startup restoration

---

# 16. Session Persistence & IPC

## Session Restoration & Crash Recovery

The application persists:

* open tabs
* split layouts
* browser state
* zoom levels
* active documents
* clean shutdown flag (`_session_cleanly_closed`)
* multi-window layout schemas (`sessions/multi_window`)

### Immediate History & Crash Recovery Flow
* **Immediate Persistence**: Opened documents and web pages are recorded into `HistoryManager` as soon as they are launched rather than waiting for `closeEvent()`.
* **Clean vs Unclean Exit**: On graceful exit, `RiemannWindow.closeEvent()` records a clean shutdown state. If the process is terminated abruptly (SIGKILL, power failure, system reboot), the subsequent startup detects the unclean exit and presents a modal prompt offering to **"Restore last opened tabs"**.
* **Default Theme**: Initial theme state defaults to Light Mode for new installations.

---

## Multi-Window Session Persistence Architecture

Riemann provides seamless restoration across multiple concurrent desktop windows:

* **Session Schema (`sessions/multi_window`)**:
  A serialized list of window dictionaries, each containing:
  * `geometry`: Window dimensions and position on screen.
  * `main_tabs`: List of tab serialization dicts (`{"type": "pdf"|"web", "data": path|url}`) for `tabs_main`.
  * `side_tabs`: List of tab serialization dicts for `tabs_side`.
  * `active_main_idx`: Active tab index in the main pane.
  * `active_side_idx`: Active tab index in the side pane.
  * `is_split`: Boolean indicating if split-view was visible.
* **Sequential Close Coordination**:
  * Users frequently close windows one by one before fully quitting the application.
  * When any window closes with multiple windows remaining, `_save_all_windows_session()` records the current time into `_multi_window_save_time`.
  * If the final window closes within 20 seconds, it refrains from overwriting the multi-window snapshot, allowing the full multi-window layout to be restored on the next startup.
* **Restoration Lifecycle**:
  * On application boot, `RiemannWindow.__init__(restore_session=True)` checks for `sessions/multi_window`.
  * If found, the current window restores entry 0.
  * For entries `1..N`, the application instantiates new `RiemannWindow(restore_session=False)` objects, populating their tabs and geometries, and displays them on screen.

---

## Directory Persistence

File dialogs preserve:

* last-opened directory
* default directory overrides

This behavior is implemented repeatedly across multiple subsystems.

Keep helper behavior consistent.

---

# 17. Build & Packaging Infrastructure

## Packaging

Release executables are built with Nuitka (`scripts/nbuild.sh` on Linux, `scripts/nbuild.ps1` and `.github/workflows/release.yml` on Windows); `Riemann.spec` builds the same with PyInstaller. All four read the modules to leave out from `scripts/build-excludes.txt`, and leave out the JavaScript tests and log files under `assets/`. The Nuitka version is pinned in `scripts/build-requirements.txt`: the release workflow installs it, and both build scripts stop when another version is installed. Nuitka's PySide6 plugin works around PySide6 releasing compiled methods once too often; in 4.2 it covered only the first argument of `QTimer.singleShot`, so the callback in `singleShot(ms, widget, fn)` was freed while still in use and builds crashed at random. 4.3 covers the last argument. The Rust core is built into the environment with `maturin develop --release` (`just build`).

The Windows builds use `--windows-console-mode=attach` (no console of its own, but output shows when started from a terminal) and `assets/icons/windows-icon.ico` (a real ICO; `Icon.ico` is the PNG the Linux desktop uses, and two names that differ only in case would collide on Windows). Child processes started by the app pass `core.processes.NO_WINDOW`, so ffmpeg, Tesseract, pip and the render and AI children never flash a console window.

`build_entry.py` is the entry point of the frozen app. It routes `--riemann-render-process` and `--riemann-ai-sidecar` to the child processes before Qt loads, writes startup crashes to `crash.log`, and on Linux drops `LD_LIBRARY_PATH` folders that hold another Qt 6.

---

## AI Engine Process

The AI engine (`core/ai_server.py`) is the app itself, started again as a child: `python -m riemann.core.ai_server` from source, the frozen executable with `--riemann-ai-sidecar` in a build. It needs no bundled environment of its own. It embeds text with all-MiniLM-L6-v2 on onnxruntime; the model is downloaded on first use, pinned by commit and SHA-256. The app passes a free loopback port and a random token (`RIEMANN_AI_PORT`, `RIEMANN_AI_TOKEN`); without both the server refuses to start.

---

## Optional Dependencies

Heavy features (Snip-to-LaTeX, local captions, video streaming) install their packages on demand into a venv in the app's data folder (`core/dependencies.py`). Each feature installs from a hashed lock in `assets/requirements/` with `pip install --require-hashes --no-deps`, so every package, dependencies included, is pinned by version and SHA-256. `scripts/lock_optional.py` writes the locks from the `bundled` and `extra` groups of `pyproject.toml`; run it after changing a version there. The venv is created only when something is installed; startup only mounts one that already exists. A packaged build cannot run pip itself, so it runs any Python 3 with pip found on the computer and installs into `packages/py311` (named for the bundled Python) with `--target` and `--python-version`; the lock is first filtered to the lines whose markers hold for the bundled Python, since pip judges markers by the Python running it.

yt-dlp comes with `yt-dlp-ejs`, the JavaScript that solves YouTube's challenges, so yt-dlp never downloads solver code at run time. It needs a JavaScript runtime such as Deno on `PATH`.

---

## Frozen Runtime Handling

Bundled files are found through `core/assets.py` (`asset_path`, `icon_path`), and `is_frozen()` says whether this is a built executable. No other module checks `sys.frozen`, `sys._MEIPASS` or `__compiled__`; `tests/test_assets.py` fails if one does.

---

# 18. Performance & Memory Considerations

## Most Sensitive Systems

The most performance-sensitive areas are:

* rendering rebuilds
* virtualization
* browser media
* OCR
* caption inference
* PDF rasterization
* WebAudio DSP

---

## Historical Stability Problems

Historically difficult areas include:

* fullscreen transitions
* reading-mode toggles
* virtualization rendering gaps
* text selection geometry
* PDF memory leaks
* Chromium media teardown

When modifying these systems:

* test long sessions
* test large PDFs
* test media-heavy tabs
* test split-view interactions

---

## Avoiding UI Freezes

Never run synchronously in the Qt UI thread:

* OCR
* AI inference
* yt-dlp extraction
* Faster-Whisper inference
* PDF-heavy transforms
* whole-file parsing with pypdf or pyhanko (signature detection uses `doc.signature_count()` and falls back to pyhanko only when the core lacks it)

A pure-Python parser on a worker thread still holds the GIL most of the time and starves the UI thread, so prefer a Rust core method that releases the GIL.

Always use:

* QThread
* worker objects
* background subprocesses

---

## Page Buffer Memory on Linux

A page rendered at Fit Width is about 10 MB. glibc serves allocations that size with `mmap` until the first one is freed, then raises its dynamic threshold and serves later ones from per-thread arenas, which rarely hand memory back to the OS. `_return_page_buffers_to_os()` in `render_worker.py` runs once at import on Linux and calls `mallopt(M_MMAP_THRESHOLD, 1 MB)`, which fixes the threshold so page buffers always come from `mmap` and are released when freed. With ten PDF tabs open this halved resident memory (3.5 GB to 1.8 GB in `benchmarks/bench_memory.py`).

## Whole-App Session Tests

`python-app/tests/test_user_sessions.py` starts Riemann the way the desktop launcher does (`riemann.app.run`), in a child process with throwaway home and XDG folders. It passes `--new-window`, so files never reach a Riemann the developer has open. Each test plays a scenario from `tests/user_session.py`: reading and copying, redacting and saving, opening files through the file dialog, quitting with page edits waiting, leaving pages with unsaved changes, and a tour that opens one file of every kind and two web pages (served from a local HTTP server) twice, with every tab closed in between. With `RIEMANN_SESSION_DRIVER` set to a binary built by `scripts/nbuild.sh --sessions` (`python-app/tests/nuitka_session.py`, compiled with the release flags), the same scenarios run inside a Nuitka build, where crashes that plain Python never shows can appear. A scenario is a generator that acts through real input (QTest keys and clicks, menus, dialog buttons) and yields the conditions it waits for. Anything that opens a modal dialog or menu starts with `later()`, because the dialog runs its own event loop. Two offscreen limits shape the scenarios. QTest mouse moves never arrive, so text is selected by double-clicking a word. A window may be left inactive after a dialog closes, so `key()` activates the window before pressing. If the app hangs, the driver's watchdog writes the error and prints every thread's stack.

The Rust core has unit tests of its own in `rust-core/src/lib.rs` (`just test-rust`). They write small PDFs with a correct cross-reference table and check rendering, the page themes, text and character boxes, links, the outline, comments and signatures.

---

## Benchmarks

`benchmarks/` holds the scripts behind the numbers in the README: `bench_core.py` (Rust core against PyMuPDF), `bench_scroll.py`, `bench_startup.py` and `bench_memory.py`. They run against the checkout by default; `--app-path` and `--core-path` point them at another checkout or core build for before/after comparisons. Run the relevant one before and after changing the render path, the page cache or startup. See `benchmarks/README.md`.

---

# 19. Common Modification Scenarios

## Adding a New Annotation Tool

Primary files:

* `annotations.py`
* `widgets.py`
* `components.py`

Likely required:

* new drawing logic
* preview rendering
* serialization support
* undo integration

---

## Adding a New Browser Injection

Primary files:

* `ui/browser/`
* `browser_handlers.py`
* `assets/injections/`

Keep browser-side logic isolated from Qt logic whenever possible.

---

## Adding a New AI Workflow

Primary files:

* `mixins/ai.py`
* AI sidecar routes
* inference workers

Avoid embedding large inference logic directly into Qt widgets.

---

## Adding New Persistent Settings

Read and write through the shared store:

```python
from riemann.core.settings import app_settings

app_settings().value("reader/my_option", False, type=bool)
```

Give the option a control in the Settings tab (`ui/system/settings_tab.py`), in the section it belongs to. Tests patch `app_settings` in the module that calls it.

Use namespaced keys:

```text
browser/...
app/...
homepage/...
```

---

## Adding New Conversion Features

Primary file:

```text
core/features.py
```

Ensure:

* optional dependency handling
* progress reporting
* background execution
* proper cleanup

---

# 20. Repository Structure Reference

```text
riemann/
├── docs/
├── libs/
├── logs/
├── python-app/
│   ├── riemann/
│   │   ├── assets/
│   │   │   ├── injections/
│   │   │   ├── icons/
│   │   │   ├── theme/
│   │   │   └── __tests__/
│   │   │   └── fonts/
│   │   ├── core/
│   │   │   ├── managers.py
│   │   │   ├── features.py
│   │   │   ├── constants.py
│   │   │   ├── dependencies.py
│   │   │   ├── captions.py
│   │   │   ├── mini_player.py
│   │   │   ├── settings.py
│   │   │   ├── storage.py
│   │   │   ├── log.py
│   │   │   ├── permissions.py
│   │   │   ├── annotation_store.py
│   │   │   ├── pdf_annotations.py
│   │   │   ├── pdf_forms.py
│   │   │   ├── page_edit.py
│   │   │   ├── redaction.py
│   │   │   ├── doctypes.py
│   │   │   ├── citations.py
│   │   │   ├── bibliography.py
│   │   │   └── ai_engine.py
│   │   ├── window/
│   │   ├── ui/
│   │   │   ├── system/
│   │   │   └── reader/
│   │   │       └── mixins/
│   └── tests/
├── rust-core/
├── rust-ocr-worker/
├── benchmarks/
│   └── results/
└── scripts/
```

Subsystem overview:

* `core/` — persistence, settings, logging, site permissions, document types, PDF writing (annotations, forms, page edits, redaction), citations, the AI engine client
* `window/` — the main window's mixins and helpers
* `ui/` — browser and reader UI systems, System tabs (`ui/system/`), tooltips, the command palette
* `mixins/` — modular reader logic
* `assets/` — browser-side enhancements and themes
* `rust-core/` — native rendering backend
* `benchmarks/` — performance scripts and their JSON results
* `tests/` — unit and subsystem testing

---

# Final Notes

Riemann is not a conventional Qt application.

Several systems intentionally cross runtime boundaries:

* Python ↔ Rust
* Qt ↔ Chromium
* Browser JS ↔ Qt
* Qt ↔ AI sidecar

Most complex bugs originate at those boundaries.

When debugging:

1. Determine which runtime owns the failing behavior.
2. Identify whether the issue is synchronous or asynchronous.
3. Verify coordinate-space assumptions.
4. Verify lifecycle ordering.
5. Test under large-document and long-session conditions.

In general:

* UI belongs in PySide6.
* Heavy compute belongs in Rust.
* Browser augmentation belongs in JavaScript.
* AI belongs in the sidecar.
* Persistence belongs in managers.

Maintaining those boundaries is critical to preserving long-term maintainability.
