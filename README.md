# Riemann

**An Integrated Research Environment (IRE) Designed for High-Performance Research Workflows**

Riemann is a hybrid desktop research environment designed for serious reading, analysis, and knowledge workflows. It combines a high-performance PDF engine, local-first AI tools, a full Chromium research browser, annotation systems, document utilities, and a deep-work-oriented workspace into a single cohesive application.

Unlike traditional PDF viewers or browser-based tools, Riemann is designed as a **complete research operating environment**.

The system uses a **hybrid architecture** combining:

* **Python (PySide6)** for UI orchestration
* **Rust** for performance-critical computation
* **FastAPI** for local AI services

All AI inference, document analysis, and indexing run **entirely on the user's machine**.

---

## Table of Contents

* [Philosophy](#philosophy)
* [Positioning](#positioning)
* [System Architecture](#system-architecture)
* [Runtime Architecture](#runtime-architecture)
* [Rust Core Engine](#rust-core-engine)
* [Reader System](#reader-system)
* [Annotation System](#annotationmixin)
* [AI Subsystem](#ai-subsystem)
* [OCR Pipeline](#ocr-pipeline)
* [Integrated Browser](#integrated-browser)
* [Audio Engine](#music-mode-audio-engine)
* [Library & Knowledge Management](#library-manager)
* [PDF Utilities](#pdf-utilities)
* [Viewing Modes](#viewing-modes)
* [Workspace & Tab Management](#workspace--tab-management)
* [Keyboard Shortcuts](#keyboard-shortcuts)
* [Repository Structure](#repository-structure)
* [Rendering Pipeline](#rendering-pipeline)
* [AI Pipeline](#ai-pipeline)
* [Installation](#installation)
* [Development Setup](#development-setup)
* [Build System](#build-system)
* [Testing Infrastructure](#testing-infrastructure)
* [Continuous Integration](#continuous-integration)
* [Performance Characteristics](#performance-characteristics)
* [Security Model](#security-model)
* [Local-First Philosophy](#local-first-architecture)
* [Contributing](#contributing)
* [License](#license)

---

## Philosophy

Modern research workflows are fragmented across many tools:

* PDF viewers
* reference managers
* browsers
* note-taking apps
* AI tools

Riemann integrates these into a **single research environment**.

Core principles:

* **Local-First Computing** – no external AI APIs required
* **Performance through Rust** – heavy computation bypasses Python limitations
* **Composable UI Architecture** – mixin-based UI composition
* **Deep Work Design** – minimize context switching
* **Data Sovereignty** – user documents never leave the device

---

## Positioning

Riemann is designed as a **research workspace for reading and analyzing technical documents**.

It is not intended to replace mature, specialized software ecosystems such as:

* Acrobat — enterprise document workflows and editing
* Okular — lightweight general-purpose document viewing
* Zotero — citation management and research library tooling

Instead, Riemann focuses on combining several research tasks into a single environment:

* deep reading of research papers
* local AI-assisted document exploration
* integrated research browsing
* structured annotation workflows
* lightweight document manipulation

The goal is not to compete directly with long-established PDF software, but to explore a **new workflow-oriented research environment** that integrates reading, browsing, and local AI analysis.

---

## System Architecture

```
+------------------------------------------------------------+
|                        USER INTERFACE                      |
|                        (PySide6 / Qt)                      |
|                                                            |
| ReaderTab | Browser | Managers | Tabs | Settings           |
+------------------------------------------------------------+
|                    Python Application Layer                |
|                                                            |
| Mixins: Rendering | Annotation | Metadata | Search | AI    |
| Workers: OCR | Model Loader | Installer | Inference        |
| Managers: Library | History | Downloads | Bookmarks        |
+------------------------------------------------------------+
|                     Rust Native Backend                    |
|                                                            |
| riemann_core (PDF engine bindings via PyO3)                |
| rust-ocr-worker                                            |
+------------------------------------------------------------+
|                    External Systems                        |
|                                                            |
| PDFium | Tesseract | Torch | Transformers | FAISS          |
+------------------------------------------------------------+
```

---

## Runtime Architecture

Riemann bridges Python and Rust using **PyO3**, allowing Rust code to compile into Python extensions.

The Rust backend compiles to:

```
riemann_core.abi3.so
```

(or `.pyd` on Windows).

Heavy operations run outside the Python Global Interpreter Lock.

Responsibilities of the Rust backend:

* PDF parsing
* rendering
* text extraction
* annotation embedding
* form handling
* search

Python remains responsible for:

* UI
* event orchestration
* threading
* AI service communication

---

## Rust Core Engine

The Rust core exposes several classes via PyO3.

### PdfEngine

Singleton responsible for initializing the **PDFium rendering engine**.

Responsibilities:

* loading pdfium
* managing runtime state

Must be initialized before documents open.

---

### RiemannDocument

Thread-safe representation of an open PDF document.

Methods:

```
render_page(page_index, scale, dark_mode_int)
get_page_text(page_index)
ocr_page(page_index, scale)
search_page(page_index, query)
get_text_segments(page_index)
create_markup_annotation(page_index, rects, subtype, color)
get_form_widgets(page_index)
```

Capabilities:

* page rasterization
* text extraction
* OCR delegation
* search
* annotation insertion
* form widget inspection

---

### RenderResult

Returned by rendering pipeline.

Contains:

```
width
height
pixel_buffer
```

The pixel buffer is returned as raw BGRA bytes.

---

## Reader System

The **ReaderTab** class is the central reading component.

Rather than a monolithic architecture, ReaderTab uses a **mixin composition model**.

Advantages:

* modular development
* feature isolation
* easier debugging

---

## Reader Mixins

### RenderingMixin

Responsible for:

* page rasterization
* QImage creation
* zoom scaling
* viewport layout

#### Virtualized Rendering

Documents larger than **300 pages** automatically switch to virtualized rendering.

Only visible pages are rendered.

---

### AnnotationMixin

Handles annotation features.

Supported tools:

* highlight
* underline
* strikeout
* rectangles
* ovals
* sticky notes
* freehand pen
* tick/cross stamps

Annotations stored locally in:

```
~/.local/share/riemann/annotations/
```

Undo/redo fully supported.

---

### SearchMixin

Implements document search.

Capabilities:

* keyword search
* result highlighting
* navigation between matches

---

### MetadataMixin

Handles metadata extraction.

Extracted fields:

* title
* authors
* publication year
* DOI
* arXiv ID

Sources:

* Crossref
* OpenAlex

---

### SignatureMixin

Handles cryptographic signatures using **pyHanko**.

Features:

* signature detection
* certificate inspection
* integrity validation
* local trust store
* PKCS#12 signing

---

### AiMixin

Provides AI-powered document analysis tools.

Capabilities:

* semantic search
* LaTeX extraction
* OCR assistance
* embedding generation

---

## AI Subsystem

Riemann includes a **local AI sidecar engine** implemented using **FastAPI**.

Capabilities:

* document chunk embeddings
* semantic search
* inference pipelines

Model used:

```
all-MiniLM-L6-v2
```

Vector index:

```
FAISS
```

---

## Snip-to-AI Mode

Users can draw a rectangle over document content.

Pipeline:

```
Selection
→ Rendered image
→ PNG buffer
→ Inference engine
```

Supported tasks:

* equation extraction
* LaTeX generation

---

## OCR Pipeline

OCR handled by Rust worker crate.

```
rust-ocr-worker
```

Pipeline:

```
Page Render
→ RGBA buffer
→ Rust worker
→ PNG encode
→ Tesseract
→ Text output
```

---

## Integrated Browser

Riemann embeds a Chromium-based browser using **Qt WebEngine**.

Capabilities:

* research browsing
* viewing supplementary material
* dataset downloads
* print webview to PDF (allowing users to instantly capture and convert live web research, articles, or documentation into local PDFs for offline reading and annotation)

---

## Browser Request Interceptor

Custom interceptor blocks telemetry and ads.

Blocked domains include:

* doubleclick
* googlesyndication
* googleadservices

---

## Homepage System

Browser homepage includes:

* quick search
* customizable link cards
* persistent shortcuts

Communication with Python occurs via:

```
riemann-save://
```

---

## Music Mode (Audio Engine)

Riemann includes a Web Audio DSP engine for focus music.

DSP Chain:

```
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

---

## Library Manager

Persistent research library stored in **SQLite**.

Indexed metadata:

* file hash
* file path
* title
* authors
* year
* DOI
* arXiv ID

---

## Bookmark Manager

Bookmarks stored in:

```
bookmarks.json
```

---

## History System

Tracks:

* opened PDFs
* visited websites

Categories:

```
pdf
web
```

This history state is directly integrated into the application menu via an **Open Recent** dropdown, allowing frictionless resumption of previously active documents without needing to open the full library.

---

## Download Manager

Non-modal download manager window.

Capabilities:

* download progress
* pause
* resume
* cancel

---

## PDF Utilities

Capabilities:

* PDF splitting
* page extraction
* document merging

---

## Markdown Reflow

Supports Markdown rendering including **KaTeX math blocks**.

---

## Viewing Modes

```
IMAGE
REFLOW
```

Zoom modes:

```
MANUAL
FIT_WIDTH
FIT_HEIGHT
```

### Intelligent Dark Mode

In addition to the standard light mode and naive dark mode, Riemann features an intelligent dark mode designed for late-night research. Instead of applying a simple global color inversion, the rendering engine smartly inverts document backgrounds and text while preserving the visual fidelity and original colors of images, charts, and figures.

---

## Workspace & Tab Management

Riemann provides advanced window and tab management to help organize complex research sessions:

* **Advanced Tab Controls:** Context menus on tabs allow users to quickly *Duplicate Tab*, *Close Tabs to the Right*, or *Close Other Tabs* to declutter the workspace.
* **Drag-and-Drop:** Documents can be opened instantly by dragging and dropping PDF files directly anywhere over the application's title bar.
* **Mute Tab Audio:** Individual web or document tabs can be muted directly from the tab bar, silencing noisy web pages without interrupting Riemann's dedicated focus audio engine or system volume.

---

## Keyboard Shortcuts

### Application

Ctrl+O — Open document

Ctrl+Q — Quit

Ctrl+, — Preferences

F — Fullscreen

Esc — Exit fullscreen

### Navigation

Up / Down — Scroll

Left / Right — Page navigation

Ctrl + + — Zoom in

Ctrl + - — Zoom out

Ctrl + 0 — Reset zoom

### Tabs

Ctrl + T — New tab

Ctrl + W — Close tab

### Browser

Backspace — Back

Alt + Left — Back

Alt + Right — Forward

F6 — Focus address bar

---

## Repository Structure

```
riemann/
├── .editorconfig
├── .github/
│   └── workflows/
│       └── release.yml
├── .gitignore
├── Cargo.lock
├── Cargo.toml
├── LICENSE
├── README.md
├── Riemann.spec
├── build.sh
├── build_entry.py
├── create_model_pack.sh
├── directory-tree.md
├── install_icon.sh
├── justfile
├── libs/
│   └── libpdfium.so
├── nbuild.sh
├── package-lock.json
├── package.json
├── pyproject.toml
├── python-app/
│   ├── riemann/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── app.py
│   │   ├── assets/
│   │   │   ├── __tests__/
│   │   │   │   ├── audio_engine.test.js
│   │   │   │   └── homepage.test.js
│   │   │   ├── audio_engine.js
│   │   │   ├── homepage.css
│   │   │   ├── homepage.html
│   │   │   ├── homepage.js
│   │   │   ├── Icon.png
│   │   │   └── icon.ico
│   │   ├── core/
│   │   │   ├── constants.py
│   │   │   └── managers.py
│   │   ├── ui/
│   │   │   ├── browser.py
│   │   │   ├── browser_handlers.py
│   │   │   ├── components.py
│   │   │   └── reader/
│   │   │       ├── __init__.py
│   │   │       ├── mixins/
│   │   │       │   ├── ai.py
│   │   │       │   ├── annotations.py
│   │   │       │   ├── metadata.py
│   │   │       │   ├── rendering.py
│   │   │       │   ├── search.py
│   │   │       │   └── signatures.py
│   │   │       ├── tab.py
│   │   │       ├── utils.py
│   │   │       ├── widgets.py
│   │   │       └── workers.py
│   │   ├── riemann_core.abi3.so
│   │   └── riemann_core.pyi
│   └── tests/
│       ├── test_ai.py
│       ├── test_annotations.py
│       ├── test_app.py
│       ├── test_browser.py
│       ├── test_browser_handlers.py
│       ├── test_components.py
│       ├── test_constants.py
│       ├── test_managers.py
│       ├── test_metadata.py
│       ├── test_rendering.py
│       ├── test_search.py
│       ├── test_signatures.py
│       ├── test_utils.py
│       ├── test_widgets.py
│       └── test_workers.py
├── requirements.txt
├── riemann-ai/
│   ├── build_ai.sh
│   ├── main.py
│   ├── requirements.txt
│   └── tests/
│       └── test_main.py
├── rust-core/
│   ├── Cargo.lock
│   ├── Cargo.toml
│   ├── src/
│   │   └── lib.rs
│   └── tests/
│       └── test_core.rs
├── rust-ocr-worker/
│   ├── Cargo.lock
│   ├── Cargo.toml
│   ├── src/
│   │   └── lib.rs
│   └── tests/
│       └── test_ocr.rs
└── test_runner.sh
```

---

## Rendering Pipeline

```
PDF
→ Rust PDFium
→ Rasterization
→ BGRA buffer
→ Python layer
→ QImage
→ Display
```

---

## AI Pipeline

```
PDF
→ Chunking
→ Embeddings
→ FAISS index
→ Semantic search
→ Result highlighting
```

---

## Installation

Requirements:

* Python 3.11
* Rust toolchain
* pdfium
* Tesseract

```bash
git clone https://github.com/shadow30812/riemann.git
cd riemann
pip install -r requirements.txt
just run
```

You may also choose to install the pre-compiled optimized binary of the app. Note however that it may not be stable on all systems directly, and you may have to run it with the terminal in case of any missing packages or errors. That being said, the latest binary at the time of writing this README is available at <https://github.com/shadow30812/riemann/releases/download/v3.2/Riemann>, compiled in an Ubuntu 24.04.02 LTS machine.

---

## Development Setup

Recommended tools:

* Python 3.11
* Rust stable
* just build tool

---

## Build System

Multi-stage build pipeline.

### Maturin

Builds Rust components.

### Nuitka

Compiles Python into optimized binaries.

### PyInstaller

Creates distributable executables.

---

## Testing Infrastructure

Riemann utilizes a comprehensive, multi-language testing suite to ensure stability across its hybrid architecture.

The test coverage includes:

* **Python UI & App Logic:** `pytest` suites covering the PySide6 components, managers, and ReaderTab mixins (located in `python-app/tests/`).
* **Rust Native Core:** `cargo test` suites validating PDFium bindings, concurrent memory safety, and the OCR worker crates.
* **AI Subsystem:** FastAPI endpoint testing for the local sidecar.
* **JavaScript/Web:** Jest tests for the internal Audio Engine and Homepage components.

Tests can be orchestrated locally using the provided `test_runner.sh` script.

---

## Continuous Integration

GitHub Actions build releases for:

* Linux
* Windows
* macOS

---

## Performance Characteristics

Optimizations include:

* Rust rendering pipeline
* virtualized scrolling
* asynchronous OCR
* FAISS indexing

---

## Security Model

Principles:

* local AI execution
* no external APIs
* local document storage

---

## Local-First Architecture

All analysis runs locally including:

* rendering
* OCR
* AI
* vector search

---

## Contributing

Contributions welcome in:

* rendering improvements
* AI integrations
* UI improvements
* performance optimization

---

## Versioning

V3.2 released on 09/03/2026

## License

See LICENSE file.
