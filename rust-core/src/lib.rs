use once_cell::sync::OnceCell;
use pdfium_render::prelude::*;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use riemann_ocr_worker::OcrEngine;
use std::sync::Mutex;

// --- Thread Safety Wrappers ---
/// Wrapper to make Pdfium thread-safe (Send + Sync).
struct PdfiumWrapper(Pdfium);
unsafe impl Send for PdfiumWrapper {}
unsafe impl Sync for PdfiumWrapper {}

/// Wrapper to make PdfDocument thread-safe (Send + Sync).
struct DocumentWrapper(PdfDocument<'static>);
unsafe impl Send for DocumentWrapper {}
unsafe impl Sync for DocumentWrapper {}

static PDFIUM: OnceCell<PdfiumWrapper> = OnceCell::new();

/// Initializes and retrieves the static Pdfium instance.
fn get_pdfium() -> &'static Pdfium {
    &PDFIUM
        .get_or_init(|| {
            let bindings = Pdfium::bind_to_system_library()
                .or_else(|_| {
                    Pdfium::bind_to_library(Pdfium::pdfium_platform_library_name_at_path("./"))
                })
                .expect("CRITICAL: Could not load Pdfium library.");
            PdfiumWrapper(Pdfium::new(bindings))
        })
        .0
}

/// Represents the result of a page render operation.
#[pyclass]
struct RenderResult {
    #[pyo3(get)]
    width: u32,
    #[pyo3(get)]
    height: u32,
    #[pyo3(get)]
    data: Py<PyBytes>,
}

/// A thread-safe wrapper around a PDF document.
#[pyclass]
struct RiemannDocument {
    inner: Mutex<DocumentWrapper>,
    #[pyo3(get)]
    page_count: usize,
}

#[pymethods]
impl RiemannDocument {
    /// Renders a specific page to a byte buffer.
    ///
    /// Args:
    ///     py (Python): The Python GIL token.
    ///     page_index (u16): The index of the page to render.
    ///     scale (f32): The zoom scale factor.
    ///     dark_mode_int (u8): 1 for dark mode (invert colors), 0 for standard.
    ///
    /// Returns:
    ///     RenderResult: Object containing width, height, and raw RGBA bytes.
    fn render_page(
        &self,
        py: Python,
        page_index: u16,
        scale: f32,
        dark_mode_int: u8,
    ) -> PyResult<RenderResult> {
        let dark_mode = dark_mode_int != 0;
        let doc_guard = self.inner.lock().unwrap();

        let page = doc_guard
            .0
            .pages()
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        let width = (page.width().value * scale) as i32;
        let height = (page.height().value * scale) as i32;

        let render_config = PdfRenderConfig::new()
            .set_target_width(width)
            .set_target_height(height)
            .rotate_if_landscape(PdfPageRenderRotation::None, true);

        let bitmap = page
            .render_with_config(&render_config)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        let mut buffer = bitmap.as_raw_bytes().to_vec();

        // Idiomatic color inversion for Dark Mode
        if dark_mode {
            // Pdfium typically renders BGRA. We invert B, G, R, but keep Alpha.
            buffer.chunks_exact_mut(4).for_each(|pixel| {
                pixel[0] = 255 - pixel[0]; // Blue
                pixel[1] = 255 - pixel[1]; // Green
                pixel[2] = 255 - pixel[2]; // Red
                                           // pixel[3] is Alpha, leave it alone
            });
        }

        let data = PyBytes::new_bound(py, &buffer);

        Ok(RenderResult {
            width: bitmap.width() as u32,
            height: bitmap.height() as u32,
            data: data.into(),
        })
    }

    /// Extracts plain text from a specific page.
    ///
    /// Args:
    ///     page_index (u16): The index of the page.
    ///
    /// Returns:
    ///     String: The extracted text content.
    fn get_page_text(&self, page_index: u16) -> PyResult<String> {
        let doc_guard = self.inner.lock().unwrap();
        let pages = doc_guard.0.pages();

        // Robust bounds check preventing u16 vs usize mismatch
        if (page_index as usize) >= (pages.len() as usize) {
            return Ok(String::new());
        }

        let page = pages
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        let text_accessor = page.text().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Text Access Error: {}", e))
        })?;

        // Guard against empty buffers (FPDF segfault prevention)
        if text_accessor.len() == 0 {
            return Ok(String::new());
        }

        Ok(text_accessor.all())
    }
    
    /// Runs OCR on the specified page and returns the recognized text.
    ///
    /// Args:
    ///     page_index (u16): The page to process.
    ///     scale (f32): Resolution multiplier (2.0 or 3.0 recommended for best OCR).
    ///
    /// Returns:
    ///     String: The text extracted by Tesseract.
    fn ocr_page(&self, page_index: u16, scale: f32) -> PyResult<String> {
        let doc_guard = self.inner.lock().unwrap();

        let page = doc_guard
            .0
            .pages()
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        // Render purely for OCR (no PyBytes needed)
        let width = (page.width().value * scale) as i32;
        let height = (page.height().value * scale) as i32;

        let render_config = PdfRenderConfig::new()
            .set_target_width(width)
            .set_target_height(height)
            .rotate_if_landscape(PdfPageRenderRotation::None, true);

        let bitmap = page
            .render_with_config(&render_config)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        let mut buffer = bitmap.as_raw_bytes().to_vec();

        // Fix Color Format: Pdfium is BGRA, image crate expects RGBA.
        // We must swap B and R channels so Tesseract gets the correct grayscale luminosity.
        buffer.chunks_exact_mut(4).for_each(|pixel| {
            let blue = pixel[0];
            let red = pixel[2];
            pixel[0] = red;
            pixel[2] = blue;
        });

        // Invoke the Worker
        let engine = OcrEngine::new();
        let text = engine
            .recognize_text(bitmap.width() as u32, bitmap.height() as u32, &buffer)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        Ok(text)
    }
}

/// The main entry point for the PDF Engine.
#[pyclass]
struct PdfEngine;

#[pymethods]
impl PdfEngine {
    #[new]
    fn new() -> Self {
        get_pdfium();
        PdfEngine
    }

    /// Loads a PDF document from the file system.
    ///
    /// Args:
    ///     path (String): The file path to the PDF.
    ///
    /// Returns:
    ///     RiemannDocument: The loaded document instance.
    fn load_document(&self, path: String) -> PyResult<RiemannDocument> {
        let doc = get_pdfium()
            .load_pdf_from_file(&path, None)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyIOError, _>(e.to_string()))?;

        Ok(RiemannDocument {
            page_count: doc.pages().len() as usize,
            inner: Mutex::new(DocumentWrapper(doc)),
        })
    }
}

#[pymodule]
fn riemann_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PdfEngine>()?;
    m.add_class::<RiemannDocument>()?;
    m.add_class::<RenderResult>()?;
    Ok(())
}
