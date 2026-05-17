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
* [Homepage System](#homepage-system)
* [Viewing Modes](#viewing-modes)
* [Virtualized Rendering](#virtualized-rendering)
* [Session Persistence](#session-persistence)
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
* smooth zoom interpolation
* reflow reading modes
* markdown rendering
* KaTeX rendering
* auto-scroll reading
* snip-to-AI workflows
* integrated print/export pipelines
* clickable embedded hyperlinks

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

Large documents automatically transition into virtualized rendering mode.

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

The engine operates directly within Chromium's audio graph.

---

## Mini Player

Riemann includes a compact native media controller.

The mini player continuously polls active BrowserTabs and bridges playback controls into HTML5 media elements using JavaScript execution.

Features:

* play/pause controls
* seek controls
* playback timeline display
* media detection
* browser tab polling
* dynamic icon theming
* adaptive visibility

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
* draggable tabs
* detachable windows
* file drag-and-drop
* dual-pane reading
* session restoration
* tab-aware keyboard cycling

Tabs can be:

* reordered
* moved between panes
* detached into standalone windows
* restored across sessions

---

## Viewing Modes

Supported viewing modes include:

* image mode
* markdown mode
* reflow text mode
* continuous scroll mode
* facing-page mode
* fullscreen mode
* reading mode

The reflow and markdown modes include KaTeX rendering support for mathematical expressions.

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

## Session Persistence

Riemann persists:

* active tabs
* split-view state
* browser zoom settings
* history
* downloads
* bookmarks
* custom homepage shortcuts
* dialog directories
* annotation state

The application also implements a single-instance IPC system using QLocalServer.

Secondary launches forward files into the active instance instead of spawning duplicate application windows.

---

## Keyboard Shortcuts

Examples include:

| Shortcut       | Action                   |
| -------------- | ------------------------ |
| Ctrl+F         | Toggle search            |
| Ctrl+I         | Toggle AI search         |
| Ctrl+P         | Print document           |
| Ctrl+A         | Select all text          |
| Ctrl+Shift+A   | Toggle annotations       |
| Ctrl+Z         | Undo annotation          |
| Ctrl+Shift+Z   | Redo annotation          |
| Ctrl+R         | Rotate clockwise         |
| Ctrl+Shift+R   | Rotate counter-clockwise |
| Ctrl+Shift+S   | Export secure PDF        |
| Ctrl+Tab       | Cycle tabs               |
| Ctrl+Shift+Tab | Reverse tab cycle        |

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
git clone <repo>
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

### Windows

Builds are distributed as packaged desktop binaries.

Native dependencies such as PDFium are bundled during release packaging.

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

V5.0.0 released on 18/05/2026

---

## License

See LICENSE file.
