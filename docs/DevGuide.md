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

## Settings Infrastructure

Settings are primarily persisted through `QSettings`.

Persistent categories include:

* browser preferences
* zoom settings
* homepage customization
* default directories
* autoscroll speed
* theme preferences
* session state

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

## Text Selection Evolution

The project historically transitioned from rectangular selection behavior toward more linear text-selection logic.

Many rendering edge cases originate from:

* multi-line selections
* transformed coordinate systems
* zoom scaling
* page rotation

Be extremely cautious when modifying selection geometry.

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

## Text Segment Cache

ReaderTab maintains a text segment cache.

This cache powers:

* text selection
* search highlights
* link interaction
* AI extraction

Avoid invalidating the cache unnecessarily.

---

## Hyperlink Interaction

Rendered pages maintain hyperlink rectangles.

Clicks are transformed from:

```text
Screen Space
→ Page Space
→ PDF Geometry
```

Incorrect coordinate transforms will break:

* links
* selection
* annotations
* AI snipping

simultaneously.

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

* EQ nodes
* saturation
* compressors
* analyzers
* stereo widening
* reverb

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

Also powers autocomplete suggestions.

The manager includes migration handling for older persistence formats.

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

## Session Restoration

The application persists:

* open tabs
* split layouts
* browser state
* zoom levels
* active documents

Session restoration intentionally attempts to recreate the previous workspace state.

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

* `core/` — persistence, downloads, captions, utilities
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
