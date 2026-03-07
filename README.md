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

* Philosophy
* Positioning
* System Architecture
* Runtime Architecture
* Rust Core Engine
* Reader System
* Annotation System
* AI Subsystem
* OCR Pipeline
* Integrated Browser
* Audio Engine
* Library & Knowledge Management
* PDF Utilities
* Viewing Modes
* Keyboard Shortcuts
* Repository Structure
* Rendering Pipeline
* AI Pipeline
* Installation
* Development Setup
* Build System
* Continuous Integration
* Performance Characteristics
* Security Model
* Local-First Philosophy
* Contributing
* License

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

python-app/
 └─ riemann/

    core/
      constants.py
      managers.py

    ui/
      browser.py
      browser_handlers.py
      components.py

      reader/
        tab.py
        utils.py
        widgets.py
        workers.py

        mixins/
          ai.py
          annotations.py
          metadata.py
          rendering.py
          search.py
          signatures.py

    assets/
      audio_engine.js
      homepage.html
      homepage.css
      homepage.js

rust-core/
   src/lib.rs

rust-ocr-worker/
   src/lib.rs

riemann-ai/
   main.py

build scripts /
   build.sh
   nbuild.sh
   justfile
   create_model_pack.sh

CI /
   .github/workflows/release.yml
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
maturin develop --release
python -m riemann
```

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

V3.1 released on 07/03/2026

## License

See LICENSE file.
