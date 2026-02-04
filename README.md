# **Riemann**

**An Integrated Research Environment (IRE) Designed for High-Performance Workflows.**

Riemann transcends the functionality of conventional PDF viewers, operating as a specialized workspace engineered to address the complexities inherent in contemporary research. In an environment characterized by information saturation, traditional software—predominantly designed for passive consumption or administrative tasks—often proves inadequate for rigorous inquiry.

Constructed upon an advanced hybrid architecture that leverages **Python (PyQt6)** for interface flexibility and **Rust** for computational performance and memory safety, Riemann provides a unified environment. It integrates processing speed, absolute data sovereignty, and local-first Artificial Intelligence into a cohesive tool designed to respect both user attention and data integrity.

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

**Prerequisites:** Python 3.11, Rust Toolchain (Cargo), and Just.

1. **Repository Cloning:**  
   `git clone https://github.com/shadow30812/riemann.git`  
   `cd riemann`

2. **Environment Initialization:**  
   `python \-m venv .venv`  
   `source .venv/bin/activate`

3. **Dependency Installation:**  
   `pip install \-r requirements.txt`  

4. **Compilation & Application Launch:**  
   `maturin develop \--release`  
   `python \-m riemann`

or use `just run` for the last step.

### **Method 2: Executable Deployment**

A standalone executable is provided via the release pipeline.

1. Navigate to <https://github.com/shadow30812/riemann/releases/download/v2.0/Riemann>.  
2. Execute Riemann.

## **Keyboard Shortcuts**

Riemann prioritizes keyboard-centric workflows. The following mapping describes the default configuration.

### **Application Control**

| Shortcut | Action |
| :---- | :---- |
| Ctrl \+ O | Open PDF Document |
| Ctrl \+ Q | Quit Application |
| Ctrl \+ , | Open Settings / Preferences |
| F11 | Toggle Fullscreen Mode |

### **Navigation and Viewing**

| Shortcut | Action |
| :---- | :---- |
| Up Arrow | Scroll Up |
| Down Arrow | Scroll Down |
| Page Up | Previous Page Viewport |
| Page Down | Next Page Viewport |
| Ctrl \+ \+ | Zoom In |
| Ctrl \+ \- | Zoom Out |
| Ctrl \+ 0 | Reset Zoom to Fit |
| Home | Jump to First Page |
| End | Jump to Last Page |

### **Tools and Search**

| Shortcut | Action |
| :---- | :---- |
| Ctrl \+ F | Focus Search Bar |
| F3 or Enter | Find Next Occurrence |
| Shift \+ F3 | Find Previous Occurrence |
| Ctrl \+ Space | Trigger AI Context Menu |

### **Browser and Tabs**

| Shortcut | Action |
| :---- | :---- |
| Ctrl \+ T | Open New Tab |
| Ctrl \+ W | Close Current Tab |
| Ctrl \+ Tab | Switch to Next Tab |
| Ctrl \+ Shift \+ Tab | Switch to Previous Tab |
| Alt \+ Left Arrow | Browser Back |
| Alt \+ Right Arrow | Browser Forward |
| Ctrl \+ L | Focus Address Bar |

## **Licensing**

This project is open-source and licensed under the terms specified in the LICENSE file within this repository.
