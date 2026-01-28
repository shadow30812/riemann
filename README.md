# Riemann

**Riemann** is a high-performance technical PDF reader for Linux built using a hybrid **Rust** (backend) and **Python/Qt** (frontend) architecture. It is intended for students, researchers, and engineers who work extensively with complex technical documents.

The application combines the performance of **Pdfium** and **Rust** for rendering and document processing with the flexibility of **PySide6** for the user interface. Core features include text reflow, integrated OCR, annotation support, and a distraction-free fullscreen reading mode.

---

## Features

### Architecture

* Hybrid design with performance-critical components implemented in Rust and the user interface implemented in Python (PySide6).
* Rust backend exposed to Python via PyO3.

### Viewing Modes

* **Standard View**: High-fidelity raster rendering of PDF pages.
* **Text Reflow**: Extracts and renders text as HTML for improved readability, particularly on smaller screens (toggle with `R`).
* **Facing Pages**: Displays two pages side-by-side for book-style layouts (toggle with `D`).

### Navigation and Layout

* **Split View**: Tabs can be dragged to create side-by-side views of documents or browser tabs.
* **Continuous Scroll**: Toggle between discrete page navigation and smooth continuous scrolling.
* **Persistent Sessions**: Open documents and tabs are restored across application restarts.

### Integrated Tools

* **Embedded Web Browser**: Enables quick reference lookup alongside open documents.
* **Optical Character Recognition (OCR)**: Extracts text from scanned documents using Tesseract.
* **Annotations**: Page-level notes stored in an external sidecar JSON file for transparency and portability.

### Customization and Interaction

* **Dark and Light Themes**: First-class support for both display modes (toggle with `N` or Ctrl + D).
* **Keyboard-Centric Navigation**: Extensive keyboard shortcuts inspired by modal editors.
* **Fullscreen Reading Mode**: A minimal UI mode designed for extended reading sessions.

---

## Why Riemann?

Riemann does not attempt to replace established general-purpose PDF readers such as Okular, Zathura, or browser-based viewers. Instead, it targets a narrower use case: sustained, technical reading and study of complex documents such as textbooks, research papers, standards, and scanned material.

Its design is guided by the following principles:

* **PDFs are often structurally unreliable.**
  Many technical PDFs exhibit poor logical structure, broken text flow, or consist entirely of scanned images. Riemann addresses this by combining native rendering, text reflow, and OCR rather than assuming well-formed input.

* **Deep reading benefits from an IDE-like environment.**
  Split views, persistent session state, keyboard-driven navigation, and an integrated browser support workflows where reading, cross-referencing, and searching occur continuously.

* **Performance matters for large documents.**
  Academic and engineering materials frequently exceed hundreds of pages. Rendering, virtualization, and OCR are implemented in Rust to reduce latency and avoid UI stalls.

* **Distraction-free modes should be genuinely minimal.**
  Fullscreen reading removes non-essential UI elements to support extended periods of concentration.

* **Annotations should remain transparent and portable.**
  Notes are stored in a simple sidecar JSON file rather than embedded or proprietary formats, making them inspectable and suitable for version control.

Riemann is therefore intentionally opinionated, prioritizing depth, control, and performance over breadth of features.

---

## Prerequisites

To build and run Riemann from source, the following dependencies are required:

* Rust and Cargo
* Python 3.11 or newer
* Maturin (`pip install maturin`)
* PySide6 (`pip install PySide6`)
* Tesseract OCR (system package: `tesseract-ocr`)
* Just (optional, for using the provided `justfile`)

---

## Installation and Setup

### 1. Clone the Repository

```bash
git clone https://github.com/shadow30812/riemann.git
cd riemann
```

### 2. Install libpdfium

The application requires the `libpdfium.so` shared library.

1. Download the Linux x64 release from the pdfium-binaries project.
2. Extract the archive.
3. Create a `libs/` directory in the project root.
4. Copy `lib/libpdfium.so` into `riemann/libs/`.

### 3. Set Up the Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Build and Run

Using the included `justfile`:

```bash
just run
```

Or manually:

```bash
maturin develop --release
python3 -m riemann
```

---

## Keyboard Shortcuts

| Key                   | Action                         |
| --------------------- | ------------------------------ |
| Ctrl + O              | Open PDF                       |
| Ctrl + T              | Open new PDF tab               |
| Ctrl + B              | Open new browser tab           |
| Ctrl + D              | Force dark mode for webpages   |
| Ctrl + \              | Toggle split view              |
| F or F11              | Toggle fullscreen reading mode |
| N                     | Toggle dark / light theme      |
| R                     | Toggle text reflow mode        |
| D                     | Toggle facing pages            |
| C                     | Toggle continuous scroll       |
| W                     | Zoom to fit width              |
| H                     | Zoom to fit height             |
| Ctrl + F              | Find in document               |
| Space / Shift + Space | Scroll page down / up          |

---

## Building a Standalone Executable

A single-file Linux executable can be built using the provided build script. This requires PyInstaller.

```bash
pip install pyinstaller maturin
./build.sh
```

The resulting binary will be located at:

```text
dist/Riemann
```

---

## Project Structure

```text
rust-core/          Rust backend (Pdfium bindings via PyO3)
  src/
    lib.rs
rust-ocr-worker/    Rust wrapper around the Tesseract CLI
  src/
    lib.rs
python-app/         Python / PySide6 frontend
  riemann/
    app.py          Main UI and application logic
Riemann.spec        PyInstaller configuration
```

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.
