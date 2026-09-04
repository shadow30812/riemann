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
* text extraction
* OCR delegation
* search indexing
* page geometry
* annotation embedding

The Rust backend exists primarily to bypass Python's GIL and improve long-session responsiveness.

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

## AI Sidecar

AI inference is intentionally isolated into a separate FastAPI subsystem.

Reasons:

* dependency isolation
* PyTorch compatibility stability
* process separation
* crash containment
* simplified model lifecycle management

---

# 2. Application Lifecycle & Main Window

## `riemann/app.py`

This file is the primary orchestration layer of the application.

Core responsibilities:

* constructing the main window
* initializing Chromium
* configuring global profiles
* split-view coordination
* tab routing
* session persistence
* global shortcuts
* single-instance handling
* media cleanup
* dialog orchestration

The application entrypoint eventually routes into `RiemannWindow`.

---

## Chromium Configuration

The application configures Chromium flags early during startup.

Important behaviors depend on these flags:

* autoplay handling
* audio routing
* WebAudio stability
* media playback compatibility

Be extremely careful when modifying startup flags because subtle browser regressions can occur on Linux distributions.

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

If an independent window instance is explicitly required, passing `--new-window` bypasses single-instance IPC forwarding and initiates a new process instance.

---

## Linux Desktop & Window Manager Integration

To ensure seamless desktop integration on Linux distributions (GNOME, KDE, Ubuntu Dock, Cinnamon):

* `RiemannWindow` registers `StartupWMClass=Riemann` on the top-level window.
* On startup, `install_desktop_file()` automatically generates and writes `~/.local/share/applications/Riemann.desktop` if absent.
* This ensures running instances are properly associated with the application launcher and dock icons rather than appearing as a generic executable or duplicate launcher icon.

---

## Fullscreen Architecture & Hover Controls

Fullscreen mode in `RiemannWindow` integrates auto-hiding controls to maximize reading canvas while keeping navigation accessible:

* **Trigger Threshold**: Moving the mouse within the top 4 pixels of the display reveals both the main menu bar and the primary navigation bar using smooth property animations.
* **Auto-Hide Delay**: When the mouse leaves the top control zone, a 1.5-second single-shot timer triggers an auto-hide transition unless a menu or combo-box dropdown is currently open.
* **System Clock Widget**: A live `SystemClockWidget` (`QLabel` updated via `QTimer`) is integrated directly into the navbar to maintain time awareness during distraction-free fullscreen research sessions.

---

## External Application Dispatcher

Riemann allows delegating document rendering to external viewers and browsers via `_populate_external_app_menu()`:

* **Dynamic Discovery**: Scans `$PATH` using `shutil.which()` for installed browsers (Google Chrome, Chromium, Mozilla Firefox, Brave Browser, Microsoft Edge) and the system default viewer (`xdg-open` on Linux, `start` on Windows).
* **Launch Architecture**: Dispatches external viewers asynchronously via `subprocess.Popen([binary, pdf_path])` or `QDesktopServices.openUrl(QUrl.fromLocalFile(pdf_path))`.
* **Custom Binary Selection**: A file chooser dialog (`QFileDialog`) allows launching any arbitrary executable binary against the active PDF path.

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

Settings are primarily persisted through `QSettings`.

Persistent categories include:

* browser preferences
* zoom settings
* homepage customization
* default directories
* autoscroll speed
* theme preferences (defaulting to Light Mode for initial installs)
* session state
* `app/ui_scale`: UI Display Scaling percentage (50% to 250%)

### UI Display Scaling Integration
Located in `SettingsDialog`:
* Configures `QT_SCALE_FACTOR` dynamically.
* Integrates a wide `QSlider` (220px, 1% single step) and an interactive numeric `QSpinBox` synchronized bidirectionally.
* Applies on restart, scaling all Qt widgets, fonts, and icons cleanly for high-DPI displays.

---

# 3. Core Reader Architecture

## `ui/reader/tab.py`

`ReaderTab` is the central document-viewing component.

It is intentionally thin in terms of direct business logic.

Most major functionality is delegated to mixins.

Responsibilities remaining inside `ReaderTab`:

* UI assembly
* toolbar creation
* mode switching
* scroll setup
* shortcut registration
* backend initialization
* high-level event routing
* page widget orchestration
* middle-click autoscroll navigation
* document reloading and external launcher integration

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

## Mixin Composition Strategy

Reader functionality is divided into:

* `RenderingMixin`
* `AnnotationsMixin`
* `SearchMixin`
* `AiMixin`
* `MetadataMixin`
* `SignaturesMixin`

This architecture prevents the reader from becoming a monolithic God object.

When adding features:

* rendering-related logic belongs in RenderingMixin
* persistent annotation logic belongs in AnnotationMixin
* AI flows belong in AiMixin
* search logic belongs in SearchMixin

Do not continuously expand `ReaderTab` itself.

---

## PageWidget Architecture

Each rendered page is wrapped inside a custom `PageWidget`.

Responsibilities:

* displaying rasterized page pixmaps
* temporary drawing overlays
* selection rectangles
* hyperlink overlays
* signature overlays
* shape previews
* transient interaction rendering

The widget intentionally separates temporary state from committed document state.

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
Rust Render
→ BGRA Buffer
→ QImage
→ QPixmap
→ PageWidget
→ Scroll Viewport
```

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

Annotations are stored separately from PDFs using JSON.

Storage path:

```text
~/.local/share/riemann/annotations/
```

The filename is generated using a SHA-256 hash of the PDF path.

This allows:

* persistent local overlays
* non-destructive workflows
* lightweight state recovery

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

The text selection system (`tab.py`) was overhauled to handle LaTeX, math notations, scanned papers, and OCR output with high visual and clipboard accuracy:

* **Font-Weighted Character Metrics**: Character bounding boxes are mapped via `get_char_weight()` rather than uniform division, accounting for narrow (e.g. 'i', 'l') and wide (e.g. 'w', 'm') glyphs and preventing rightward highlight drift.
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

## PDFium Link Extraction & Caching (`core/links.py`)

Hyperlink detection and destination routing are handled by `PdfLinkExtractor`:

* **C API ctypes Integration**: Directly interfaces with PDFium C libraries (`FPDFLink_Enumerate`, `FPDFLink_GetDest`, `FPDFLink_GetAction`, `FPDFAction_GetURIPath`) to extract internal document targets (`#page=N`) and external web URIs.
* **Performance Cache (`_page_links_cache`)**: Link extraction across dense technical documents involves expensive C-level traversal. Riemann caches extracted link rectangles per page path, reducing repeated lookup latency from ~19ms down to **0.0057ms**.
* **Cache Invalidation**: `PdfLinkExtractor.close_document(doc_path)` safely purges cached link geometry when a document tab is closed.
* **Viewport Navigation**: In `ReaderTab.eventFilter`, hovering over an internal link displays `Jump to Page X ↗` and sets `Qt.CursorShape.PointingHandCursor`. Clicking jumps directly to `target_page = int(url.split("page=")[-1]) - 1`. External URLs open in browser tabs.

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

## `ui/browser.py`

This file contains the primary Chromium integration layer.

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

## MiniAudioPlayer

Located in:

```text
core/mini_player.py
```

The mini player polls active browser tabs looking for HTML5 media elements.

It bridges native Qt controls into webpage JavaScript.

Features:

* play/pause
* seeking
* timeline polling
* adaptive visibility
* theme-aware icons

---

## Polling Strategy

The player intentionally uses periodic polling instead of complex event synchronization.

Reasons:

* browser isolation boundaries
* renderer unpredictability
* simpler failure recovery

---

## Native Stream Extraction

yt-dlp stream extraction allows playback through:

* MPV
* VLC
* QtMultimedia

This bypasses unsupported Chromium codecs.

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

## AI Sidecar Philosophy

The AI stack is intentionally isolated.

Reasons:

* Torch dependency containment
* simplified crashes
* smaller UI runtime
* optional dependency loading

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

* **Immediate Persistence**: History entries are written immediately upon opening a document or web page, rather than deferring writes until application exit. This guarantees that recently opened documents are preserved even if the application is killed abruptly.
* **Crash Recovery State**: Tracks session exit state using a `_session_cleanly_closed` flag. If an unclean shutdown is detected on startup (e.g. system power outage or crash), the application prompts the user with a recovery dialog to restore all previously open tabs.
* **Autocomplete Engine**: Powers address bar and file path suggestions across all viewports.
* **Format Migration**: Automatically upgrades legacy JSON history formats.

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

---

## Compression Strategy

PDF compression works by:

* traversing embedded images
* recompressing images
* replacing streams
* deflating internal objects

Lossless and lossy paths are both supported.

---

## PyMuPDF Usage

Several conversion features depend on PyMuPDF.

The application intentionally treats this dependency as optional.

Always preserve ImportError handling.

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

* internal movement
* split-view transfer
* window detachment
* external file drops

The implementation uses custom MIME payloads:

```text
application/x-riemann-tab
```

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
* Event Routing: intercepts `QDragEnterEvent`, `QDragMoveEvent`, and `QDropEvent` globally, opening dropped PDF files immediately into new reader tabs and persisting them to history.

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

### Immediate History & Crash Recovery Flow
* **Immediate Persistence**: Opened documents and web pages are recorded into `HistoryManager` as soon as they are launched rather than waiting for `closeEvent()`.
* **Clean vs Unclean Exit**: On graceful exit, `RiemannWindow.closeEvent()` records a clean shutdown state. If the process is terminated abruptly (SIGKILL, power failure, system reboot), the subsequent startup detects the unclean exit and presents a modal prompt offering to **"Restore last opened tabs"**.
* **Default Theme**: Initial theme state defaults to Light Mode for new installations.

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

The project supports:

* Nuitka
* PyInstaller
* Rust extension packaging

---

## Optional Dependencies

Heavy dependencies are intentionally separable.

This significantly reduces:

* binary size
* startup cost
* packaging complexity

---

## Frozen Runtime Handling

Many modules check:

```python
sys.frozen
```

and:

```python
sys._MEIPASS
```

This logic is required for packaged builds.

Never assume development-path resource loading.

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

Always use:

* QThread
* worker objects
* background subprocesses

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

* `browser.py`
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

Primary storage:

```python
QSettings
```

Keep naming conventions consistent.

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
│   │   ├── core/
│   │   │   ├── links.py
│   │   │   ├── managers.py
│   │   │   ├── features.py
│   │   │   ├── constants.py
│   │   │   ├── dependencies.py
│   │   │   ├── captions.py
│   │   │   └── mini_player.py
│   │   ├── ui/
│   │   │   └── reader/
│   │   │       └── mixins/
│   └── tests/
├── rust-core/
├── rust-ocr-worker/
├── riemann-ai/
└── scripts/
```

Subsystem overview:

* `core/` — persistence, downloads, captions, link extraction, utilities
* `ui/` — browser and reader UI systems
* `mixins/` — modular reader logic
* `assets/` — browser-side enhancements and themes
* `rust-core/` — native rendering backend
* `riemann-ai/` — local inference sidecar
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
