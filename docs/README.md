# Riemann

## An Integrated Research Environment (IRE) Designed for High-Performance Research Workflows

Riemann is a hybrid desktop research environment designed for serious reading, analysis, and knowledge workflows. It combines a high-performance PDF engine, local-first AI tools, a full Chromium research browser, annotation systems, document utilities, live media tooling, and a deep-work-oriented workspace into a single cohesive application.

Unlike traditional PDF viewers or browser-based tools, Riemann is designed as a complete research operating environment focused on reducing workflow fragmentation across reading, browsing, annotation, AI-assisted analysis, and media consumption.

The system uses a hybrid architecture combining:

* Python (PySide6) for UI orchestration
* Rust for performance-critical computation
* FastAPI for local AI services
* JavaScript/WebAudio for browser-side DSP and interaction layers

All AI inference, document analysis, indexing, OCR pipelines, and media tooling run entirely on the user's machine.

---

## Table of Contents

* [Philosophy](#philosophy)
* [Positioning](#positioning)
* [System Architecture](#system-architecture)
* [Runtime Architecture](#runtime-architecture)
* [Rust Core Engine](#rust-core-engine)
* [Reader System](#reader-system)
* [Reader Mixins](#reader-mixins)
* [Rendering Pipeline](#rendering-pipeline)
* [Text Selection & Copying System](#text-selection--copying-system)
* [Hyperlinks & Document Navigation](#hyperlinks--document-navigation)
* [External Application Integration](#external-application-integration)
* [Annotation System](#annotation-system)
* [Search System](#search-system)
* [AI Subsystem](#ai-subsystem)
* [OCR Pipeline](#ocr-pipeline)
* [Live Captioning System](#live-captioning-system)
* [Integrated Browser](#integrated-browser)
* [Browser Injection Layer](#browser-injection-layer)
* [Music Mode (Audio Engine)](#music-mode-audio-engine)
* [Mini Player](#mini-player)
* [Library & Knowledge Management](#library--knowledge-management)
* [Document Conversion & Compression](#document-conversion--compression)
* [Workspace & Tab Management](#workspace--tab-management)
* [Linux Desktop & Dock Integration](#linux-desktop--dock-integration)
* [UI Display Scaling](#ui-display-scaling)
* [Homepage System](#homepage-system)
* [Viewing Modes](#viewing-modes)
* [Virtualized Rendering](#virtualized-rendering)
* [Session Persistence & Crash Recovery](#session-persistence--crash-recovery)
* [Keyboard Shortcuts](#keyboard-shortcuts)
* [Repository Structure](#repository-structure)
* [Build System](#build-system)
* [Development Setup](#development-setup)
* [Testing Infrastructure](#testing-infrastructure)
* [Continuous Integration](#continuous-integration)
* [Performance Characteristics](#performance-characteristics)
* [Security Model](#security-model)
* [Local-First Architecture](#local-first-architecture)
* [Installation](#installation)
* [Contributing](#contributing)
* [License](#license)

---

## Philosophy

Modern research workflows are fragmented across many tools:

* PDF viewers
* reference managers
* browsers
* note-taking applications
* AI assistants
* media tools
* conversion utilities

Riemann integrates these into a single research environment.

Core principles:

* Local-First Computing — no external AI APIs required
* Performance through Rust — heavy computation bypasses Python limitations
* Hybrid Runtime Architecture — separate responsibilities between UI, compute, and AI
* Composable UI Architecture — mixin-based feature composition
* Deep Work Design — minimize workflow interruption and context switching
* Data Sovereignty — user documents never leave the device
* Long-Session Stability — virtualization and cleanup systems designed for large workloads

---

## Positioning

Riemann is designed as a research workspace for reading and analyzing technical documents.

It is not intended to replace mature, specialized ecosystems such as:

* Acrobat — enterprise editing and publishing workflows
* Okular — lightweight document viewing
* Zotero — citation management and research libraries
* Obsidian — long-form knowledge graph workflows

Instead, Riemann focuses on integrating several research tasks into a unified environment:

* deep reading of technical papers
* local AI-assisted analysis
* semantic exploration
* integrated research browsing
* live annotation workflows
* OCR-assisted extraction
* browser-native media tooling
* lightweight conversion and compression utilities
* long-duration reading sessions across very large documents

The objective is not to recreate every feature from mature ecosystems, but to explore a workflow-oriented research environment that tightly integrates reading, browsing, AI analysis, media tooling, and document interaction.

---

## System Architecture

```text
+------------------------------------------------------------+
|                        USER INTERFACE                      |
|                        (PySide6 / Qt)                      |
|                                                            |
| ReaderTab | Browser | Managers | Explorer | Settings       |
+------------------------------------------------------------+
|                    Python Application Layer                |
|                                                            |
| Mixins: Rendering | Annotation | Metadata | Search | AI    |
| Workers: OCR | Captions | yt-dlp | Model Loader            |
| Managers: Library | History | Downloads | Bookmarks        |
+------------------------------------------------------------+
|                     Rust Native Backend                    |
|                                                            |
| riemann_core (PyO3 bindings)                               |
| rust-ocr-worker                                            |
+------------------------------------------------------------+
|                    Browser Runtime Layer                   |
|                                                            |
| Chromium | WebAudio DSP | JS Injection | Overlay Engines   |
+------------------------------------------------------------+
|                    External Systems                        |
|                                                            |
| PDFium | Tesseract | Torch | Transformers | FAISS          |
| faster-whisper | yt-dlp | FFmpeg                          |
+------------------------------------------------------------+
```

---

## Runtime Architecture

Riemann bridges Python and Rust using PyO3, allowing Rust code to compile into Python extensions.

The Rust backend compiles to:

```bash
riemann_core.abi3.so
```

(or `.pyd` on Windows).

Heavy operations execute outside the Python Global Interpreter Lock.

Responsibilities of the Rust backend:

* PDF parsing
* raster rendering
* text extraction
* OCR delegation
* annotation embedding
* form handling
* search indexing
* page geometry operations

Python remains responsible for:

* UI orchestration
* event handling
* session management
* browser integration
* threading
* AI sidecar communication
* JavaScript injection coordination
* workspace persistence

The AI stack is intentionally isolated into a local FastAPI sidecar to avoid dependency conflicts between Qt/PySide6 and large ML runtimes.

---

## Rust Core Engine

The Rust core exposes several classes through PyO3.

### PdfEngine

Singleton responsible for initializing the PDFium rendering engine.

Responsibilities:

* loading PDFium
* managing native runtime state
* constructing document instances

Must be initialized before any documents are opened.

---

### RiemannDocument

Thread-safe representation of an open PDF document.

Methods include:

```py
render_page(page_index, scale, dark_mode_int)
get_page_text(page_index)
ocr_page(page_index, scale)
search_page(page_index, query)
get_text_segments(page_index)
create_markup_annotation(page_index, rects, subtype, color)
get_form_widgets(page_index)
```

Capabilities:

* high-speed page rasterization
* text extraction
* OCR delegation
* annotation insertion
* hyperlink extraction
* search indexing
* form widget inspection
* structured text segmentation

---

### RenderResult

Returned by the rendering pipeline.

Contains:

```py
width
height
pixel_buffer
```

The pixel buffer is returned as raw BGRA bytes for direct QImage construction.

---

## Reader System

The `ReaderTab` class is the central document reading component.

Rather than relying on a monolithic architecture, ReaderTab uses a mixin composition model.

Advantages:

* modular feature development
* subsystem isolation
* simplified debugging
* reduced UI coupling
* targeted optimization

ReaderTab integrates:

* rendering
* annotations
* semantic search
* metadata extraction
* AI interaction
* signature validation
* OCR
* viewing modes
* export systems
* tab-aware navigation

The reader also supports:

* dual-page/facing layouts
* continuous scrolling
* smooth zoom interpolation with fractional scroll offset anchoring (preventing page jumping)
* high-speed middle-click autoscroll navigation with directional cursor indicators (SizeAll, SizeVer, SizeHor)
* Ctrl + mouse wheel smooth zooming
* keyboard navigation (Page Up / Page Down page scrolling)
* document refresh (F5) with disk modification detection and change prompts
* launch in external viewers (System Default, Chrome, Firefox, Brave, Edge, custom applications)
* reflow reading modes
* markdown rendering
* KaTeX rendering
* auto-scroll reading
* snip-to-AI workflows
* integrated print/export pipelines
* clickable embedded hyperlinks (internal TOC, footnotes, and external URLs)

---

## Reader Mixins

### RenderingMixin

Responsible for:

* page rasterization
* QImage generation
* viewport layout
* zoom scaling
* virtualized rendering
* facing-page coordination
* scroll reconstruction

The rendering system dynamically rebuilds layout trees depending on:

* viewing mode
* page count
* virtualization thresholds
* scroll state
* facing-page mode

---

### AnnotationMixin

Handles annotation systems and persistence.

Supported tools:

* highlight
* underline
* strikeout
* freehand pen
* rectangles
* ovals
* sticky notes
* tick/cross stamps
* eraser tools

Features:

* undo/redo stacks
* persistent JSON storage
* page-level redraw optimization
* coordinate-space mapping
* temporary overlay previews
* interactive drawing cursors

Annotations are stored locally inside:

```bash
~/.local/share/riemann/annotations/
```

---

### SearchMixin

Implements exact-text document search.

Capabilities:

* keyword search
* viewport highlights
* result navigation
* viewport centering
* text segment mapping

Search results returned by Rust are mapped into Qt overlay rectangles.

---

### MetadataMixin

Handles metadata extraction and enrichment.

Extracted fields include:

* title
* authors
* publication year
* DOI
* arXiv identifiers

External enrichment sources:

* Crossref
* OpenAlex

The system also supports metadata-assisted automatic PDF renaming.

---

### SignatureMixin

Handles cryptographic PDF signatures using pyHanko.

Features:

* signature detection
* integrity validation
* certificate inspection
* trust store integration
* PKCS#12 signing support

---

### AiMixin

Provides AI-powered document interaction.

Capabilities:

* semantic search
* OCR assistance
* LaTeX extraction
* embedding generation
* image-region inference
* markdown-oriented responses

The AI integration pipeline supports direct image snipping from rendered document pages.

---

## Rendering Pipeline

The rendering system is designed around minimizing RAM usage while preserving responsiveness on large documents.

Pipeline:

```text
PDF Page
→ Rust Rasterization
→ BGRA Buffer
→ QImage
→ QPixmap
→ PageWidget
→ Scroll Viewport
```

Features:

* debounced rebuild execution
* viewport-aware rendering
* cached page sizing
* devicePixelRatio-aware scaling
* dynamic page widget generation
* selective page invalidation
* dark-mode rendering support
* scale-aware pre-render margins (clamped to 1 page buffer at ≥200% zoom and 2 pages at ≥120% zoom to prevent memory cache blowouts)
* visible-first priority rendering queue (active visible page is rendered immediately within 50–100ms before surrounding pages)
* non-blocking event-loop processing (removal of synchronous event flushes prevents UI freezes on high-zoom completion)
* bounded character geometry cache (capped LRU prevents memory leaks on long reading sessions)

Large documents automatically transition into virtualized rendering mode.

---

## Text Selection & Copying System

Riemann features a precision text extraction and selection engine optimized for technical PDFs, LaTeX documents, scanned papers, and OCR output.

Features:

* **Proportional Font Metrics**: Character highlighting boxes use width weighting (`get_char_weight`) rather than uniform character distribution, preventing highlight bounding boxes from drifting across long lines.
* **Vertical Baseline Alignment**: Character selection geometry is shifted vertically by +10% of font height to align exactly with visual glyph baselines instead of font ascenders.
* **Reading-Order Clustering ($\ge 40\%$)**: Text segments are clustered into lines by vertical overlap and sorted left-to-right, ensuring clean copying order across multi-column layouts, math formulas, and OCR artifacts.
* **Click-to-Dismiss**: Single clicks without dragging dismiss active selections immediately.
* **Double-Click Word Selection**: Double-clicking selects the word under the cursor across segment boundaries.
* **Triple-Click Paragraph/Sentence Selection**: Triple-clicking selects the whole sentence or paragraph bounded by line breaks (`vert_gap > 0.5 × height`) or horizontal whitespace gaps (`horiz_gap > max(20px, 1.5 × height)`).
* **Multi-Click Release Shield**: Selection state is preserved when lifting the mouse button after double- or triple-clicking, preventing premature deselection.
* **Clipboard Fidelity**: Selection copy (via Ctrl+C or right-click context menu) preserves spaces, punctuation, and structural line breaks accurately.

---

## Hyperlinks & Document Navigation

Riemann provides full support for internal document destinations and external hyperlinks.

Features:

* **Internal Destinations**: Extracts Table of Contents links, footnote references, and named destinations (`#page=N`) directly via native PDFium C API bindings (`core/links.py`).
* **High-Performance Caching**: Document links are extracted once and cached per page (`_page_links_cache`), reducing lookup times to under 0.01ms per page. Caches are cleanly evicted on document close.
* **Interactive Tooltips & Cursors**: Hovering over internal references displays a `Jump to Page X ↗` tooltip and activates a pointing hand cursor. Clicking jumps directly to the target page.
* **External URLs**: External links display the destination URL tooltip and open in integrated browser tabs or the system default browser when clicked (or Ctrl+clicked).

---

## External Application Integration

Users can open the currently active PDF document in external desktop applications with a single click.

Access points:

* **Top Menu Bar**: `File → Open in External Application...` dynamically discovers installed viewers and browsers on the system.
* **Tab Context Menu**: Right-clicking any PDF tab provides the `Open in External Application ↗` option.
* **Reader Toolbar**: Dedicated launcher button on the PDF toolbar with descriptive tooltip.

Supported targets:

* System Default PDF Viewer (`xdg-open` on Linux, OS default on Windows)
* Web Browsers: Google Chrome, Chromium, Mozilla Firefox, Brave Browser, Microsoft Edge
* Custom Application: File picker dialog allowing selection of any executable binary.

---

## Annotation System

The annotation system operates directly on viewport geometry and PDF coordinate transformations.

Capabilities include:

* drag-based text markup
* linear text selection improvements
* overlay previews
* persistent annotation serialization
* page-local repainting
* highlight opacity management
* shape previews during drag operations
* **External Application Comments Support**: In addition to native Riemann annotations, the reader directly parses and renders annotations authored in external PDF software (such as Adobe Acrobat, Apple Preview, or Okular):
  * **Native Annotations Ingestion**: Inspects the document via `pypdf.PdfReader` to extract native annotation dictionaries (`/Text`, `/FreeText`, `/Highlight`, `/Underline`, etc.).
  * **Metadata Extraction**: Reads `/T` (author/creator), `/M` (modification date/time), `/Contents` (comment message body), and `/Rect` (bounding coordinate box).
  * **Coordinate Normalization**: Converts PDF point coordinates (origin at bottom-left) to rendered page pixel coordinates.
  * **Interactive Inspection**: Hovering over an external comment displays a formatted tooltip and activates a pointing hand cursor. Clicking on the comment opens a dedicated `CommentViewDialog` displaying the author name, date, and scrollable message content.

The rendering layer maintains temporary visual overlays independently from committed annotation state.

---

## Search System

Search operations are delegated to the Rust backend for speed.

Search results are returned as geometric rectangles which are then mapped into viewport coordinates by Qt.

Features:

* result highlighting
* page jumping
* viewport centering
* multi-result traversal
* integration with virtualized layouts

The search layer also powers browser-style inline navigation workflows.

---

## AI Subsystem

Riemann includes a local AI sidecar implemented using FastAPI.

Capabilities:

* semantic document search
* embedding generation
* OCR assistance
* image inference
* LaTeX extraction
* markdown-oriented outputs

Default embedding model:

```bash
all-MiniLM-L6-v2
```

Vector indexing:

```bash
FAISS
```

All inference remains local.

---

## Snip-to-AI Mode

Users can draw rectangular regions directly over rendered document pages.

Pipeline:

```text
Document Selection
→ Rendered QImage
→ PNG Buffer
→ Local Inference Engine
→ Structured Response
```

Supported tasks:

* equation extraction
* LaTeX generation
* OCR recovery
* semantic interpretation

---

## OCR Pipeline

OCR functionality is delegated to a Rust worker crate.

```rust
rust-ocr-worker
```

Pipeline:

```text
Page Render
→ RGBA Buffer
→ Rust Worker
→ PNG Encode
→ Tesseract
→ Text Output
```

This architecture avoids blocking the primary Qt event loop during OCR execution.

---

## Live Captioning System

Riemann includes a native live-captioning pipeline for browser media.

Architecture:

```text
HTML5 Video
→ Browser Audio Capture
→ WebSocket Streaming
→ Faster-Whisper
→ Translation/Transcription
→ Native Overlay Rendering
```

The browser-side system injects a draggable live-caption overlay into active pages.

Features:

* live translation
* browser-native overlays
* draggable subtitle positioning
* adjustable subtitle scaling
* rolling transcription context
* local transcription execution
* VAD-assisted inference

The captioning system utilizes:

* WebSocket audio streaming
* browser-side AudioContext capture
* Faster-Whisper inference
* rolling memory context buffers
* asynchronous worker threads

Offline caption generation utilities are also integrated into the desktop environment.

---

## Integrated Browser

Riemann embeds a Chromium-based browser using Qt WebEngine.

Capabilities:

* research browsing
* dataset downloads
* supplementary material viewing
* print-to-PDF support
* persistent profiles
* incognito profiles
* browser zoom persistence
* integrated media tooling
* ad blocking
* dark-mode injection
* homepage customization
* browser-side DSP injection
* media stream extraction

The browser subsystem heavily utilizes JavaScript injection layers to extend Qt WebEngine functionality.

---

## Browser Injection Layer

Several browser-side enhancement systems are injected dynamically into webpages.

Included systems:

* Smart dark mode
* Ad skipping
* Backspace navigation fixes
* Caption overlays
* Audio DSP engine
* Video playback controls
* Emoji fallbacks

Communication between browser content and Python occurs through custom protocol bridges such as:

```text
riemann-save://
```

---

## yt-dlp Integration

Riemann integrates yt-dlp directly through the Python API.

Capabilities:

* playlist downloads
* subtitle embedding
* audio-only extraction
* browser-cookie integration
* direct stream extraction
* format selection
* FFmpeg post-processing
* playlist slicing
* native download management
* **Automated Playlist Detection & Formatting**: Automatically detects playlist URLs containing `list=` or `/playlist` parameters, pre-checking the playlist download option in the settings dialog and applying a structured output template:
  ```text
  %(playlist_index&{:02d} - |)s%(title)s.%(ext)s
  ```
* **Cumulative Playlist Progress Tracking**: In multi-video playlist downloads, progress is computed globally across the entire playlist rather than resetting to 0% per item:
  $$\text{Total \%} = \frac{(p_{\text{idx}} - 1) + \frac{\text{video \%}}{100}}{p_{\text{count}}} \times 100$$
  This ensures continuous, accurate progress feedback across all queued videos.
* **Modern JavaScript Extraction & Deno Support**: Automatically integrates `"remote_components": ["ejs:github"]` and discovers local Deno installations on `PATH` to resolve modern JavaScript challenges during stream extraction.

The application includes:

* a dedicated yt-dlp settings dialog
* asynchronous worker threads
* download cancellation
* cleanup of partial artifacts
* direct streaming into native media players

Unsupported browser codecs can be bypassed by extracting direct playback streams into:

* MPV
* VLC
* QtMultimedia

---

## Music Mode (Audio Engine)

Riemann includes a browser-side WebAudio DSP engine for long-duration focus sessions.

DSP Chain:

```text
Media Source
→ PreAmp
→ Saturation
→ Mid/Side Split
→ Low Shelf EQ
→ High Shelf EQ
→ Reverb
→ Compressor
→ Limiter
→ FFT Analyzer
→ Output
```

Adjustable controls include:

* Gain
* Warmth
* Width
* Bass
* Treble
* Air
* Settle Window (3s–30s slider, default 12s)

**Gain Stability & Settle Window**:
The audio normalization engine incorporates a dynamic settle window to ensure volume consistency throughout a song without distracting volume fluctuations:
* **Settling Period**: During the configurable settle window at the beginning of playback (default 12s), the gain smoothly converges toward an optimal target to normalize the overall track volume.
* **Baseline Gain Lock**: Once settled, the gain locks onto the track's baseline level and resists changing during quiet interludes, pauses, or spoken segments—preventing unnatural volume swelling.
* **Downward Peak Limiter**: A fast-acting peak limiter runs continuously to prevent digital clipping, crackling, or harsh transients during sudden crescendos or bass drops.

The engine operates directly within Chromium's audio graph.

---

## Mini Player

Riemann includes a compact native media controller embedded directly in the application menu bar.

The mini player continuously polls active BrowserTabs and bridges playback controls into HTML5 media elements using JavaScript execution.

Features:

* **App-Wide Cross-Window Media Discovery**: Media discovery spans all application windows (`RiemannWindow._all_open_windows` and `QApplication.topLevelWidgets()`), enabling audio controls regardless of which window or workspace pane hosts the media tab.
* **Audible Tab Prioritization**: Automatically locates and prioritizes browser tabs that are actively playing sound (`recentlyAudible()`).
* **Active Browser Session Retention**: Retains the active media playback connection across tab switches (e.g. switching to a PDF tab or non-browser tab keeps playback controls active and accessible).
* **Playback & Timeline Controls**: Provides native play/pause toggles, continuous seek slider, and dynamic track timestamp display (`currentTime / duration`).
* **Theme-Aware Iconography**: Automatically updates control icon assets (`play.svg`, `pause.svg`) to match light or dark application themes.
* **Adaptive Visibility**: Gracefully hides from the menu bar when no active media streams are present, eliminating visual clutter.

The mini player operates independently from browser UI controls.

---

## Library & Knowledge Management

Riemann includes multiple persistent management systems.

### LibraryManager

Backed by SQLite.

Stores:

* file metadata
* DOI information
* authors
* publication years
* arXiv identifiers

Supports:

* keyword search
* author filtering
* year filtering
* metadata persistence

---

### BookmarksManager

Persistent JSON-backed bookmark management.

Features:

* duplicate prevention
* bookmark persistence
* quick-access integration

---

### HistoryManager

Maintains categorized browsing and document history.

Tracks:

* PDF history
* web history
* folder history

Supports:

* autocomplete suggestions
* persistent history
* category separation
* legacy migration handling

---

### DownloadManager

Native non-modal download manager.

Features:

* active download tracking
* pause/resume support
* persistent history
* completed download access
* failed download handling
* progress visualization

---

## Document Conversion & Compression

Riemann includes local conversion and compression utilities.

Supported conversions:

* PDF → Images
* PDF → Markdown
* PDF → Text
* PDF → HTML
* Images → PDF
* CSV → HTML
* EPUB → HTML

Compression capabilities:

* image compression
* PDF cleanup
* embedded image recompression
* JPEG transcoding
* stream deflation

The conversion layer utilizes:

* PyMuPDF
* Qt imaging systems
* EPUB parsing utilities
* HTML generation pipelines

---

## Homepage System

Riemann includes separate homepage systems for both the PDF reader and browser subsystems.

Browser homepage capabilities:

* customizable shortcuts
* quick search
* persistent cards
* greeting customization
* favicon retrieval
* shortcut management dialogs

Reader homepage capabilities:

* recently opened files
* folder shortcuts
* quick-open utilities

---

## Workspace & Tab Management

Riemann includes a highly customized tab and workspace system.

Capabilities:

* split-view workspaces
* draggable tabs with cross-pane transfer and window detachment
* semi-transparent tinted tab hover tooltips (showing full file name and absolute file path for PDFs; page title and top-level domain/subdomain for web pages)
* drag-and-drop drop zone overlay (prominent "Drop PDF here" prompt when all tabs are closed)
* dual-pane reading
* session restoration
* tab-aware keyboard cycling
* fullscreen auto-reveal controls (moving cursor to top 4px reveals navbar and menu bar; auto-hides after 1.5s delay)
* live system clock widget in fullscreen navbar

Tabs can be:

* reordered
* moved between panes
* detached into standalone windows
* restored across sessions

---

## Linux Desktop & Dock Integration

Riemann provides first-class Linux desktop and window manager integration:

* **Dock / Taskbar Pinning**: Installs `Riemann.desktop` into `~/.local/share/applications/` with `StartupWMClass=Riemann` and desktop icon bindings, ensuring window managers (GNOME, KDE, Cinnamon, Ubuntu Dock) group and pin running instances cleanly without duplicate generic icons.
* **Command-Line Multi-Window Flag (`--new-window`)**: By default, Riemann uses single-instance IPC (`QLocalServer`) to route files into the existing window. Passing `--new-window` forces the launch of a new, fully independent application window with its own process state.

---

## UI Display Scaling

Users on high-DPI displays or custom desktop environments can adjust interface scale directly inside the application:

* **Continuous Display Scale Slider**: Settings dialog provides a wide, continuous slider spanning 50% to 250% scale in 1% single steps.
* **Direct Numeric Input (`QSpinBox`)**: An interactive text input box directly beside the slider allows users to type an exact percentage (e.g. `125%`). Slider and text box are bidirectionally synchronized.
* **Persistent Factor**: Configures `QT_SCALE_FACTOR` and persists the preference under `app/ui_scale` across application restarts.

---

## Viewing Modes

Supported viewing modes include:

* image mode
* markdown mode
* reflow text mode
* continuous scroll mode
* facing-page mode
* fullscreen mode (with top hover reveal and system clock)
* reading mode
* document preview mode (lightweight alternative to extended PDF mode)

The reflow and markdown modes include KaTeX rendering support for mathematical expressions.

### Universal Page Indicator Popup

A floating pill widget (`Page X / Y`) displays dynamically near the vertical scrollbar whenever the viewport moves (scroll updates or page jumps):

* **All Display Settings**: Active across all three display modes: Normal (windowed), Reading Mode (with toolbar), and Pure Fullscreen (toolbar hidden).
* **Smooth 2-Second Fade**: Uses `QGraphicsOpacityEffect` and `QPropertyAnimation` with cubic easing, holding for 1.2 seconds before executing a 0.8-second smooth opacity fade-out.
* **Constrained Geometry**: Positioned near the vertical scrollbar (`x = scroll.width() - popup.width() - 25`), bounds-checked to prevent viewport clipping.

### Dual-Corner Fullscreen Toggle

When operating in fullscreen mode, moving the mouse cursor within 150×100px of either the **top-right** or **top-left** corner dynamically reveals the floating exit fullscreen button, positioning the button near the triggered corner (`(20, 20)` for top-left, `(width - 56, 20)` for top-right).

### Document Open Mode Setting & Live Tab Replacement

Users can configure whether new documents open in full Extended PDF Mode (`ReaderTab`) or lightweight Preview Mode (`PreviewReaderTab`) via `Settings → Reader → Open documents in preview mode by default`:

* **Live In-Place Hot-Swap**: When changing the mode while PDF documents are currently open, Riemann displays a prompt with explicit actions: **"Switch Open Documents"** or **"Keep Current Layout"**.
* **State Preservation**: In-place replacement preserves file paths, tab titles, current page index, and vertical scroll offsets.

---

## Virtualized Rendering

Large documents automatically switch into virtualization mode.

Instead of instantiating every page simultaneously, Riemann constructs:

* top spacers
* bottom spacers
* viewport-local widgets

Only visible pages remain actively rendered.

Benefits:

* reduced RAM usage
* smoother scrolling
* fewer Qt widget allocations
* improved responsiveness on large documents

The virtualization layer also supports:

* facing-page layouts
* scroll restoration
* dynamic rebuilds
* viewport-aware invalidation

---

## Session Persistence & Crash Recovery

Riemann provides resilient state management and crash recovery:

* **Immediate History Saving**: Open PDFs, web tabs, and external file drops are committed to history immediately upon opening rather than deferring to application shutdown.
* **Multi-Window Session Restoration**:
  * Tracks all open non-incognito application windows in `RiemannWindow._all_open_windows`.
  * Serializes window geometries, main tabs, side tabs, active tab indices, and split-view configurations across all open windows under `sessions/multi_window`.
  * On launch with session restoration enabled, restores the primary window and spawns separate `RiemannWindow` instances for each additional saved window.
  * Incorporates sequential close window protection (20-second grace threshold) so sequentially closing windows before application exit does not discard prior window sessions.
  * Provides `File → Exit` (`exit_application()`) to persist all windows simultaneously and cleanly shut down.
* **Crash Recovery & Unclean Exit Detection**: Tracks exit state via persistent clean-shutdown flags. If an abnormal termination occurs (crash, system reboot, power cut), Riemann detects it on subsequent launch and prompts the user with a **"Restore last opened tabs"** recovery dialog.
* **Default Theme**: Defaults to Light Mode on fresh installations.
* **Comprehensive Persistence**: Persists active tabs, split-view orientations, per-domain browser zoom, history, bookmarks, downloads, homepage shortcuts, and annotation databases.
* **Single-Instance IPC**: Single-instance routing via `QLocalServer` forwards external documents to the active window unless `--new-window` is passed.

---

## Keyboard Shortcuts

Examples include:

| Shortcut       | Action                                                          |
| -------------- | --------------------------------------------------------------- |
| Ctrl+F         | Toggle search                                                   |
| Ctrl+I         | Toggle AI search                                                |
| Ctrl+P         | Print document                                                  |
| Ctrl+A         | Select all text                                                 |
| Ctrl+Shift+A   | Toggle annotations                                              |
| Ctrl+Z         | Undo annotation                                                 |
| Ctrl+Shift+Z   | Redo annotation                                                 |
| Ctrl+R         | Rotate clockwise                                                |
| Ctrl+Shift+R   | Rotate counter-clockwise                                        |
| Ctrl+Shift+S   | Export secure PDF                                               |
| F5             | Refresh active PDF (checks disk modifications & prompts reload) |
| Page Down      | Scroll down by one page height                                  |
| Page Up        | Scroll up by one page height                                    |
| Ctrl + Wheel   | Smooth document zoom                                            |
| Middle-Click   | Toggle smooth autoscroll navigation (SizeAll/Ver/Hor cursors)   |
| Ctrl+Tab       | Cycle tabs                                                      |
| Ctrl+Shift+Tab | Reverse tab cycle                                               |
| F11            | Toggle reader fullscreen mode                                   |
| Escape         | Exit fullscreen / cancel autoscroll                             |

Additional browser-specific and media-specific shortcuts are also implemented.

---

## Repository Structure

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

Primary subsystems:

* `core/` — persistent managers and desktop infrastructure
* `ui/` — browser and reader UI systems
* `mixins/` — modular reader functionality
* `assets/` — browser injections, themes, and web assets
* `rust-core/` — performance-critical backend
* `riemann-ai/` — local inference sidecar

---

## Build System

The project includes:

* Nuitka packaging
* PyInstaller support
* Rust extension compilation
* GitHub Actions release automation
* external library bundling

Bundled native dependencies include:

* PDFium
* Rust extensions
* Qt runtime components

The application also supports exclusion of heavyweight optional dependencies.

---

## Development Setup

Typical setup:

```bash
git clone https://github.com/shadow30812/riemann.git
cd riemann
pip install -e .
```

Rust components require:

```bash
cargo build --release
```

Optional dependencies include:

* faster-whisper
* torch
* torchvision
* transformers
* pix2tex
* scipy
* pandas
* yt-dlp

A dedicated dependency manager UI is included for runtime installation/removal of heavy modules.

---

## Testing Infrastructure

The repository includes test coverage for:

* annotations
* rendering
* browser systems
* managers
* metadata
* search
* application infrastructure
* browser handlers
* components

JavaScript browser assets also include dedicated tests.

---

## Continuous Integration

GitHub Actions workflows automate:

* builds
* release packaging
* artifact generation
* dependency preparation

The project includes a structured release pipeline and versioned builds.

---

## Performance Characteristics

The architecture prioritizes long-session responsiveness.

Key optimizations include:

* Rust rasterization
* virtualized rendering
* debounced layout rebuilds
* selective repainting
* asynchronous workers
* lazy page instantiation
* browser-side processing
* background yt-dlp execution
* native OCR workers
* memory leak mitigation systems

The rendering subsystem is specifically optimized for:

* high-resolution PDFs
* multi-hundred-page documents
* continuous scrolling
* dual-page layouts

---

## Security Model

Riemann is designed around local execution.

Principles:

* no mandatory cloud services
* local inference
* local indexing
* local OCR
* local annotation storage
* local metadata persistence

The browser subsystem additionally supports:

* incognito profiles
* cookie clearing
* per-site cookie management
* local cache cleanup

---

## Local-First Architecture

All major functionality operates offline after installation.

This includes:

* rendering
* OCR
* AI inference
* vector indexing
* annotations
* history
* bookmarks
* downloads
* captioning
* DSP processing

External APIs are optional and primarily used for metadata enrichment.

---

## Installation

### Linux

```bash
pip install .
```

Latest pre-compiled build is available at <https://github.com/shadow30812/riemann/releases/latest>

### Windows

Builds are sometimes distributed as packaged desktop binaries along with the Linux releases.

Native dependencies such as PDFium are bundled during release packaging.

Launch Riemann.exe after decompressing the zip file to open the application.

---

## Contributing

Contributions are welcome across:

* rendering systems
* browser tooling
* Rust backend development
* AI pipelines
* annotation tooling
* accessibility
* performance optimization
* UI refinement
* documentation

Given the hybrid architecture, contributors should generally separate:

* UI concerns (PySide6)
* compute-heavy workloads (Rust)
* AI systems (FastAPI sidecar)
* browser-side enhancements (JavaScript/WebAudio)

---

## Versioning

V5.1.0 released on 04/09/2026

---

## License

See LICENSE file.
