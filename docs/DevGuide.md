# **Riemann Complete Developer Reference Guide**

This guide serves as an exhaustive architectural map and modification manual for the entire Riemann codebase. Riemann is a complex, hybrid application that blends Python (PySide6) for UI orchestration, Rust for high-performance memory-safe computation, FastAPI for local AI, and modern web technologies for embedded browsing and audio DSP.

This document breaks down the responsibilities of each file, explains the hidden mechanics of how they interact, and provides explicit instructions on where to look when making changes or adding features to specific subsystems.

## **Table of Contents**

1. [Architectural Philosophy & Data Flow](#1-architectural-philosophy--data-flow)  
2. [Core Application Orchestration (python-app/)](#2-core-application-orchestration-python-app)  
3. [Reader Subsystem & Mixins (python-app/riemann/ui/reader/)](#3-reader-subsystem--mixins-python-appriemannuireader)  
4. [Browser Subsystem (python-app/riemann/ui/)](#4-browser-subsystem-python-appriemannui)  
5. [Web Assets & Javascript (python-app/riemann/assets/)](#5-web-assets--javascript-python-appriemannassets)  
6. [Core Managers & Data (python-app/riemann/core/)](#6-core-managers--data-python-appriemanncore)  
7. [Rust Native Backend (rust-core/ & rust-ocr-worker/)](#7-rust-native-backend-rust-core--rust-ocr-worker)  
8. [Local AI Sidecar (riemann-ai/)](#8-local-ai-sidecar-riemann-ai)  
9. [Build & Packaging Scripts (/ & scripts/)](#9-build--packaging-scripts---scripts)

## **1\. Architectural Philosophy & Data Flow**

Riemann is designed around a **Local-First, Hybrid Architecture**. Because Python's Global Interpreter Lock (GIL) makes it unsuitable for heavy CPU-bound tasks like rasterizing 500-page PDFs or running vector similarity searches, Riemann delegates heavily:

* **UI & Event Loop:** Handled entirely by Python/PySide6.  
* **Heavy Compute:** Pushed across the FFI (Foreign Function Interface) boundary to Rust extensions via PyO3.  
* **AI/ML:** Isolated into a local HTTP FastAPI sidecar to prevent dependency conflicts between PySide6 and PyTorch.

When modifying Riemann, always ask: *Is this a UI event, a heavy computation, or a model interaction?* Route your modifications to the appropriate language layer.

## **2\. Core Application Orchestration (python-app/)**

### **riemann/app.py**

* **What it is:** The global orchestrator and the largest file in the Python layer. It manages the RiemannWindow (a QMainWindow), the QSplitter for dual-pane views, global keyboard shortcuts, native menu bars, session serialization, and Chromium engine initialization.  
* **Deep Dive Mechanics:**  
  * **Chromium Flags:** The run() method explicitly sets environment variables like \--autoplay-policy=no-user-gesture-required (vital for the Web Audio engine) and \--disable-features=AudioServiceOutOfProcess to ensure stability across Linux environments.  
  * **IPC & Single-Instance:** Riemann uses a QLocalServer (RiemannSingleInstance). If a user opens a second PDF via their file manager, the secondary process detects the running server, pipes the file paths over a TCP socket as a | delimited string, and instantly terminates. The primary instance intercepts this in handle\_connection() and opens the new tabs.  
  * **The Media Kill-Switch:** The \_kill\_all\_media\_safely() method is a critical stability workaround. Chromium media threads (like YouTube) can outlive the Python Garbage Collector during a sudden window close, causing Segmentation Faults. This method forces all media tabs to navigate to their root domain (youtube.com instead of a video URL) to cleanly sever the audio/video stream before C++ teardown.  
* **What to modify here:**  
  * Adding new global hotkeys (def \_init\_shortcuts).  
  * Changing split-view routing logic (def toggle\_split\_view).  
  * Modifying the settings/preferences dialog (class SettingsDialog).  
  * Expanding session restoration logic (def \_restore\_session).

### **riemann/\_\_main\_\_.py & riemann/\_\_init\_\_.py**

* **What they are:** Standard Python package entry points. \_\_main\_\_.py simply imports and triggers app.run(). They rarely, if ever, require modification unless you are fundamentally changing how the package is invoked from the command line.

## **3\. Reader Subsystem & Mixins (python-app/riemann/ui/reader/)**

The PDF Reader avoids the "God Object" anti-pattern by utilizing a **mixin architecture**. ReaderTab is a minimal shell; all actual capabilities are inherited from specialized mixin classes.

### **tab.py (ReaderTab)**

* **What it is:** The master PDF tab class. It handles core UI assembly (attaching toolbars, sidebars, and the QStackedWidget for image vs. reflow modes) and routes basic events to the appropriate mixin.  
* **What to modify here:** High-level tab UI layout, save/export dialog logic, and basic tab-centric keyboard event filtering.

### **mixins/rendering.py**

* **What it is:** The heart of the visual PDF display. It interacts directly with the riemann\_core Rust module.  
* **Deep Dive Mechanics:**  
  * **Virtual vs. Standard Layouts:** To prevent massive memory consumption, documents over a certain size (e.g., 50 pages) trigger \_build\_virtual\_layout. In this mode, QScrollArea is populated with empty placeholder widgets based on an average page size (\_probe\_base\_page\_size). The actual QImage is only rasterized from Rust when the placeholder enters the viewport bounds (render\_visible\_pages()).  
  * **DPI Scaling:** Uses self.devicePixelRatio() to ensure text isn't blurry on 4K/HiDPI monitors.  
* **What to modify here:** Zoom algorithm constraints, scroll wheel event multipliers, and virtual layout threshold values.

### **mixins/annotations.py**

* **What it is:** Handles interactive overlays. It maps physical mouse clicks (screen coordinates) into PDF-space coordinates using the current zoom scale.  
* **What to modify here:** Adding new annotation shapes (e.g., arrows, polygons), modifying the Undo/Redo stack size, or changing the JSON serialization format for local annotation storage.

### **mixins/search.py**

* **What it is:** Exact-text searching. It passes a query to Rust, which returns a list of bounding boxes \[x, y, w, h\]. Python then overlays semi-transparent yellow QWidget highlights over the document.  
* **What to modify here:** Implementing case-insensitive vs. exact-match toggles, changing the highlight color, or altering the "Scroll to next result" centering logic.

### **mixins/ai.py**

* **What it is:** Bridges the UI with the riemann-ai sidecar.  
* **Deep Dive Mechanics:** \* For "Snip-to-AI", it captures a sub-rect of the current QImage, converts it to a Base64 PNG buffer, and POSTs it to the local AI server for vision tasks (like LaTeX extraction).  
* **What to modify here:** API payload structuring, adding new buttons to the AI toolbar, or parsing new response types from the LLM.

### **mixins/metadata.py & mixins/signatures.py**

* **What they are:** \* metadata.py runs Regex over the first 3 pages of text to find DOIs or ArXiv IDs, then asynchronously hits the Crossref/OpenAlex REST APIs to fetch rich metadata.  
  * signatures.py uses pyHanko to cryptographically verify PDF signers against a local trust store.  
* **What to modify here:** Adding new metadata fallback APIs (e.g., Semantic Scholar) or altering PKCS\#12 signing flows.

## **4\. Browser Subsystem (python-app/riemann/ui/)**

### **browser.py (BrowserTab)**

* **What it is:** An integrated Chromium tab using QWebEngineView.  
* **Deep Dive Mechanics:** WebEngine operates in separate OS processes. BrowserTab bridges Python to the DOM via runJavaScript and sets up DevTools windows. It distinguishes between persistent and incognito sessions by assigning either a shared or off-the-record QWebEngineProfile.  
* **What to modify here:** Handling custom downloads (e.g., routing YouTube URLs to yt-dlp), injecting custom context menus into the web view, or implementing print-to-PDF functions.

### **browser\_handlers.py**

* **What it is:** Houses the RequestInterceptor which evaluates every outgoing HTTP request.  
* **What to modify here:** The ad-block list (adding new trackers to ad\_domains), or overriding the User-Agent headers for sites that block embedded browsers (like WhatsApp Web).

### **components.py**

* **What it is:** Reusable QWidgets. Most importantly, DraggableTabWidget which heavily overrides native Qt drag-and-drop events to allow users to open files by dropping them directly onto the tab bar.  
* **What to modify here:** Customizing tab rendering, adding close-button icons, or modifying the central AnnotationToolbar floating widget.

## **5\. Web Assets & Javascript (python-app/riemann/assets/)**

Riemann utilizes standard web technologies for internal features, bypassing Qt's UI limitations.

### **homepage.html, homepage.css, homepage.js**

* **What it is:** The default file:// new tab interface.  
* **Deep Dive Mechanics:** Because local HTML files cannot natively write to Python's disk environment due to security boundaries, homepage.js communicates with Python via a URL intercept hack. When a user saves a new bookmark, JS navigates to riemann-save://\<base64\_payload\>. Python intercepts this pseudo-protocol, cancels the navigation, decodes the payload, and saves it to disk.  
* **What to modify here:** Changing the grid layout, updating the clock widget, or modifying how favicons are fetched and cached.

### **audio\_engine.js**

* **What it is:** A pure Web Audio API implementation that acts as Riemann's "Music Mode".  
* **Deep Dive Mechanics:** It constructs an extensive DSP (Digital Signal Processing) graph: Source \-\> PreAmp \-\> Saturation (WaveShaper) \-\> Mid/Side EQ \-\> High/Low Shelves \-\> Reverb (Convolver) \-\> Compressor \-\> Limiter \-\> Destination. The AnalyserNode drives the visual FFT equalizer in the UI.  
* **What to modify here:** Adding new audio nodes (like a low-pass filter for a "muffled" effect), tweaking the Q-factor of the EQ bands, or altering the saturation curve math.

### **injections/smart\_dark\_mode.js & injections/ad\_skipper.js**

* **What they are:** Scripts injected after loadFinished. smart\_dark\_mode applies a complex CSS filter: invert(1) hue-rotate(180deg) to the \<html\> tag, while explicitly un-inverting \<img\>, \<video\>, and \<canvas\> tags to preserve media colors.  
* **What to modify here:** Improving the dark mode heuristics (e.g., protecting background-image divs) or adding specific DOM selectors to the YouTube ad-skipper.

## **6\. Core Managers & Data (python-app/riemann/core/)**

These modules handle all disk I/O and state persistence outside of the Qt settings file.

### **managers.py**

* **What it is:** Singleton-style managers for application data.  
  * LibraryManager: Uses sqlite3 to maintain library.db. Stores file\_hash, title, authors, and year. Includes LIKE queries for local library search.  
  * HistoryManager: Maintains a capped list (e.g., 1000 items) of visited URLs and PDF paths in history.json.  
  * DownloadManager: Serializes active and completed downloads to track progress across sessions.  
* **What to modify here:** \* **Database Migrations:** If you add a new column to the Library (like tags), you must update the CREATE TABLE IF NOT EXISTS schema in LibraryManager.\_\_init\_\_.  
  * **Pruning Logic:** Altering how old history items are discarded.

### **constants.py**

* **What it is:** Global Enums. Keeps magic strings out of the codebase.  
* **What to modify here:** Adding new ZoomMode states or tracking new application-wide ViewMode identifiers.

## **7\. Rust Native Backend (rust-core/ & rust-ocr-worker/)**

The Rust backend represents Riemann's commitment to performance. It is exposed to Python via PyO3.

### **rust-core/src/lib.rs**

* **What it is:** The core PDF manipulation library utilizing pdfium-render (a safe wrapper around Google's C++ PDFium).  
* **Deep Dive Mechanics:**  
  * **Memory Management:** render\_page() asks PDFium for a bitmap, processes it into a flat Vec\<u8\> (BGRA format), and hands ownership to a Python bytes object. PySide6 then wraps this in a QImage. This prevents Python from having to do costly pixel-by-pixel manipulation.  
  * **Inversion:** If dark mode is requested, the Rust code iterates through the raw byte array and mathematically inverts the RGB channels (leaving Alpha intact) before passing it to Python. This is orders of magnitude faster than doing it in Python.  
* **What to modify here:** Exposing new PDFium features to Python (e.g., reading PDF Table of Contents/Outlines, extracting raw embedded JPEGs, or reading form fields).

### **rust-ocr-worker/src/lib.rs**

* **What it is:** An asynchronous text extraction worker.  
* **Deep Dive Mechanics:** To avoid complex C++ linking issues with Tesseract libraries across different OSs, this module takes a raw RGBA buffer from Python, writes it to a temporary png file in the OS temp directory, and executes the standard tesseract CLI binary via Rust's std::process::Command using \--psm 1 (Automatic page segmentation). It captures the stdout and returns the string to Python.  
* **What to modify here:** Adding support for different PSM modes (e.g., single block vs sparse text), passing language flags (-l eng+fra), or swapping Tesseract for a different engine.

## **8\. Local AI Sidecar (riemann-ai/)**

To ensure absolute privacy, Riemann ships with its own AI environment that runs entirely offline via FastAPI.

### **main.py**

* **What it is:** The REST API serving vector embeddings and semantic search.  
* **Deep Dive Mechanics:** \* **Initialization:** On boot, it loads the sentence-transformers/all-MiniLM-L6-v2 model into memory (a highly optimized, lightweight embedding model producing 384-dimensional vectors).  
  * **Indexing (/index):** Receives a raw text string, uses LangChain text splitters to break it into overlapping chunks (e.g., 500 characters with 50 overlap), generates vectors, and stores them in an in-memory FAISS (Facebook AI Similarity Search) index.  
  * **Searching (/search):** Embeds the user's query and performs a rapid L2-distance/Cosine similarity search against the FAISS index, returning the top N matching text chunks.  
* **What to modify here:** \* Upgrading the underlying embedding model (e.g., switching to bge-small).  
  * Integrating local LLMs (like llama.cpp or Ollama endpoints) for generative Q\&A based on the retrieved context.  
  * Modifying the chunking parameters (size and overlap) to improve retrieval accuracy.

## **9\. Build & Packaging Scripts (/ & scripts/)**

Riemann uses a multi-stage compilation pipeline to go from dynamic Python/Rust scripts to a portable, optimized binary.

### **scripts/build.sh & scripts/nbuild.sh**

* **What they are:** \* build.sh utilizes **PyInstaller**. It bundles the Python interpreter and scripts into a single folder. It is relatively fast and ideal for developer testing.  
  * nbuild.sh utilizes **Nuitka**. Nuitka translates Python code into C, compiles it with GCC/Clang, and links it against Python libraries. This results in significantly faster startup times, harder-to-reverse-engineer code, and better memory usage, making it the script of choice for Production Release binaries.  
* **What to modify here:** If you add new heavy pip dependencies (like scipy or transformers), you must often explicitly declare them as "hidden imports" in these scripts to ensure the compiler includes them. You must also update the \--include-data-dir flags if you add new asset folders.

### **Riemann.spec & build\_entry.py**

* **What they are:** Configuration files for PyInstaller. build\_entry.py is a shim that ensures environmental variables (like \_MEIPASS for temporary asset extraction) are respected when the compiled binary runs.

### **scripts/install\_icon.sh & scripts/generate\_white\_icons.py**

* **What they are:** Developer utilities.  
  * generate\_white\_icons.py parses all SVGs in the icons directory and programmatically generates \-white.svg variants for Dark Mode usage.  
  * install\_icon.sh is a convenience script for Linux developers to manually register the .desktop file and icon in \~/.local/share/applications for system menu integration.

### **scripts/create\_model\_pack.sh**

* **What it is:** A pre-build step. Because the AI sidecar is "offline-first", it cannot download the HuggingFace MiniLM model at runtime. This script downloads the model architecture and weights to the developer's machine and compresses them into a tarball. The build scripts then embed this tarball into the final executable so it is available locally immediately upon installation.
