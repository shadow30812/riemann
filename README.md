# **Riemann**

**An Integrated Research Environment (IRE) Designed for High-Performance Workflows.**

Riemann transcends the functionality of conventional PDF viewers, operating as a specialized workspace engineered to address the complexities inherent in contemporary research. In an environment characterized by information saturation, traditional software—predominantly designed for passive consumption or administrative tasks—often proves inadequate for rigorous inquiry.

Constructed upon an advanced hybrid architecture that leverages **Python (PySide6)** for interface flexibility and **Rust** for computational performance and memory safety, Riemann provides a unified environment. It integrates processing speed, absolute data sovereignty, and local-first Artificial Intelligence into a cohesive tool designed to respect both user attention and data integrity.

## **Key Features and Architectural Philosophy**

### **Hybrid Architecture**

Riemann represents a significant evolution in desktop application design. Rather than selecting between development velocity (Python) and runtime efficiency (Rust), Riemann synergizes these technologies. The Python frontend manages the user interface and high-level logic, facilitating rapid development cycles. Conversely, the Rust backend handles computationally intensive tasks—such as vector calculations, rendering pipelines, and memory management—ensuring optimal performance and stability.

### **Local-First AI Analysis**

While many AI-enhanced readers operate on a Software-as-a-Service model, Riemann eschews this approach to protect data sovereignty. The AI mixin interfaces exclusively with local model packages. Unpublished research and personal annotations remain confined to the local machine, ensuring the user retains complete control over the intelligence pipeline.

### **Active Reading System**

Riemann conceptualizes annotations as fundamental data structures rather than superficial visual layers. Users may classify highlights semantically (e.g., "Arguments," "Evidence," "Rebuttals"), effectively converting a document into a structured map. The application also implements a marginalia system for persistent notes that anchor to specific paragraphs without obscuring the text.

### **Integrated Research Browser**

To mitigate the cognitive load associated with context switching, Riemann integrates a fully functional web view embedded alongside the document. This feature allows for expedited citation retrieval and asset management (e.g., downloading datasets) without exiting the application environment.

### **Integrated Music Mode**

Riemann acknowledges that auditory inputs are as critical to concentration as visual ones. To support "Deep Work" methodologies, the application includes a native, high-performance Music Mode, activated via Ctrl + M.

#### Architectural Integration

Unlike standard media overlays, this feature is embedded directly into the application's core event loop. It utilizes the custom JavaScript audio bridge (audio_engine.js) to render soundscapes with minimal system resource overhead. This integration eliminates the need for external streaming applications, thereby reducing the temptation to interact with algorithmically distracting playlists or advertisements.

#### Flow State Induction

The Music Mode is engineered to induce flow states by providing a consistent, non-intrusive auditory environment. It supports local asset playback and utilizes a separate volume processing pipeline, allowing researchers to balance the audio mix against the Text-to-Speech engine. This ensures that users can listen to paper recitations (TTS) over a bed of ambient focus music without frequency clashing or auditory fatigue.

## **System Implementation and Codebase Structure**

The Riemann codebase utilizes a sophisticated separation of concerns, employing a hybrid language approach to optimize for both developer ergonomics and runtime speed.

### **1\. The Foreign Function Interface (FFI) Boundary**

The core communication between the Python frontend and the Rust backend is mediated through **PyO3**. This library facilitates the creation of Python extensions using Rust. The riemann-core library compiles into a shared object file (.so or .pyd), exposing high-performance functions directly to the Python interpreter. This allows the application to execute heavy computational tasks—such as parsing PDF object trees or calculating embeddings—without the overhead of the Python Global Interpreter Lock (GIL).

### **2\. UI Composition via the Mixin Pattern**

The user interface logic, located within python-app/riemann/ui, avoids the monolithic class structures common in legacy GUI applications. Instead, it employs a compositional Mixin pattern. The primary reading component, ReaderTab, inherits functionality from discrete modules:

* mixins/rendering.py: Manages the QImage generation and painting cycles.  
* mixins/ai.py: Handles the context menu triggers and asynchronous calls to the inference engine.  
* mixins/annotations.py: Manages the coordinate mapping between PDF points and screen pixels.  
  This modularity ensures that feature isolation is maintained, simplifying debugging and testing procedures.

### **3\. Asynchronous OCR Pipeline**

Optical Character Recognition is resource-intensive and prone to blocking the main UI thread. Riemann isolates this functionality within the rust-ocr-worker crate. This standalone Rust component operates independently of the main application loop. Image data is passed to the worker via thread-safe channels; the worker processes the pixel data and returns text layers asynchronously. This architecture ensures that the interface remains responsive even during heavy batch processing of scanned documents.

### **4\. Browser Integration and Sandbox**

The integrated browser (ui/browser.py) utilizes the QWebEngineView, which is based on the Chromium engine. Riemann configures a specific profile for this view to ensure it remains lightweight. The browser logic is decoupled from the document reader but shares the same application window, allowing for split-pane layouts. Signals and slots are used to bridge the document view (e.g., clicking a citation) with the browser view (e.g., navigating to the URL).

### **5\. State Management and Configuration**

Application state is managed centrally through core/managers.py. This module handles configuration persistence, loading user preferences (themes, model paths, keybindings) from disk at startup, and broadcasting state changes to relevant UI components. This centralization preventing "prop drilling" (passing data through multiple layers of components) and ensures that settings changes apply immediately across the application.

### **6\. Build System and Distribution**

The project utilizes a dual-stage build process. **Maturin** is employed to compile the Rust crates and package them as Python wheels. Subsequently, **PyInstaller** bundles the Python interpreter, the Qt binaries, and the compiled Rust extensions into a single standalone executable. The Riemann.spec file defines the precise dependency tree and resource inclusion rules required to produce a portable binary for Linux and Windows.

## **Installation and Execution**

Riemann supports two primary operational modes: execution from source code for developers requiring modification capabilities, and execution via compiled binary for standard users.

### **Method 1: Source Code Execution**

**Prerequisites:** Python 3.11, Rust Toolchain (Cargo), Just, and `libpdfium`.

1. **Repository Cloning:**

   ```bash
   git clone https://github.com/shadow30812/riemann.git
   cd riemann
   ```

2. **External Library Setup:** Riemann requires the pdfium binary to be placed manually.

   * Download **pdfium-linux-x64.tgz** (Version >= 7643) from [pdfium-binaries releases](https://github.com/bblanchon/pdfium-binaries/releases).
   * Extract the archive.
   * Copy lib/libpdfium.so into the libs/ directory in the project root:

   ```bash
   mkdir -p libs
   cp path_to_extracted_lib/libpdfium.so libs/
   ```

3. **Environment Initialization:**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Compilation & Application Launch:**

   ```bash
   maturin develop --release
   python -m riemann
   ```

5. **Developer Workflow (Using Just):**  
   We use just to manage build tasks and environment hygiene.  
   Step 4 can be replaced by `just run` after installing the required dependencies.  
   * **Run Application:** Compiles Rust changes and launches the Python app.  

   ```bash
   just run
   ```

   * **Build Extension:** Only compiles the Rust backend (useful for debugging).  

   ```bash
   just build
   ```

   * **Clean Project:** Removes all compiled artifacts (Python cache and Rust target).  

   ```bash
   just clean
   ```

### **Method 2: Building a Standalone Binary**

Riemann offers two build pipelines. The **Nuitka** build is recommended for performance and smaller binary size.

#### **Option A: Optimized Build (Nuitka) — Recommended**

This method compiles Python code into C instructions, resulting in faster startup. It automatically handles icon integration for Linux environments.

##### Ensure nuitka and patchelf are installed

   ```bash
   pip install nuitka patchelf
   ```

##### Run the optimized build script

   ```bash
   ./nbuild.sh
   ```

*The resulting binary will be located at dist/Riemann.*

#### **Option B: Standard Build (PyInstaller)**

The legacy build method is available for compatibility.  
Some features like audio engine or an app icon may be missing in this build.

   ```bash
   pip install pyinstaller  
   ./build.sh
   ```

### **Method 3: Running the pre-compiled binary**

A pre-compiled and optimised version of the binary is available in the releases section of the GitHub page. You may choose to use it for the sake of convenience, but it may not run equally well on all distributions of Linux. It is compiled and tested on Ubuntu 24.04.02 LTS with Linux Kernel 6.8.0-94-generic on an x86_64 architecture machine.

At the time of writing this README, the latest pre-compiled binary is available on [this](https://github.com/shadow30812/riemann/releases/download/v2.1/Riemann) page of the GitHub repository.

## **AI & OCR Configuration**

Riemann's AI features are strictly local-first and modular.

### **Dynamic Model Loading**

To keep the application lightweight, heavy Machine Learning libraries (Torch, Transformers) are **not** bundled directly into the core executable. Instead, they are loaded dynamically at runtime.

* **Source Users:** Necessary libraries are in requirements.txt. If you do not use AI/OCR, you can remove torch and related ML packages to save space.  
* **Binary Users:** The application loads models from the local environment or cache when the OCR worker is started. If not found, it downloads the models from the source repository. Download is triggered by clicking the OCR button (eye icon in the toolbar of the PDF Viewer), so take care to do it ahead of time in case of urgent use.

### **Initializing Model Packs**

Before using AI features, you must generate the local model package structure (if you are running from source) to ensure the inference engine has access to weights locally.

./create\_model\_pack.sh

## **Keyboard Shortcuts**

Riemann prioritizes keyboard-centric workflows. The following mapping describes the default configuration.

### **Application Control**

| Shortcut | Action |
| :---- | :---- |
| Ctrl \+ O | Open PDF Document |
| Ctrl \+ Q | Quit Application |
| Ctrl \+ , | Open Settings / Preferences |
| F | Toggle Fullscreen Mode |
| Esc | Exit Fullscreen Mode |

### **Navigation and Viewing**

| Shortcut | Action |
| :---- | :---- |
| Up Arrow | Scroll Up |
| Down Arrow | Scroll Down |
| Left Arrow | Previous Page |
| Right Arrow | Next Page |
| Ctrl \+ \+ | Zoom In |
| Ctrl \+ \- | Zoom Out |
| Ctrl \+ 0 | Reset Zoom to Fit |

### **Tools and Search**

| Shortcut | Action |
| :---- | :---- |
| Ctrl \+ F | Focus Search Bar |
| Enter | Find Next Occurrence |
| Home | Go to the first page |
| End | Go to the last page |

*Home and End might not work well for larger documents or e-books (>40 pages).*

### **Browser and Tabs**

| Shortcut | Action |
| :---- | :---- |
| Ctrl \+ T | Open New Tab |
| Ctrl \+ W | Close Current Tab |
| Ctrl \+ Tab | Switch to Next Tab |
| Ctrl \+ Shift \+ Tab | Switch to Previous Tab |
| Backspace | Browser Back (if not in text field) |
| Alt \+ Left Arrow | Browser Back |
| Alt \+ Right Arrow | Browser Forward |
| F6 | Focus Address Bar |

## **Licensing**

This project is open-source and licensed under the terms specified in the LICENSE file within this repository.
