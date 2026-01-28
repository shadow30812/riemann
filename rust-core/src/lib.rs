use once_cell::sync::OnceCell;
use pdfium_render::prelude::*;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use riemann_ocr_worker::OcrEngine;
use std::sync::Mutex;

/// Wrapper to ensure Pdfium is treated as thread-safe (Send + Sync) for PyO3.
struct PdfiumWrapper(Pdfium);
unsafe impl Send for PdfiumWrapper {}
unsafe impl Sync for PdfiumWrapper {}

/// Wrapper to ensure PdfDocument is treated as thread-safe (Send + Sync) for PyO3.
struct DocumentWrapper(PdfDocument<'static>);
unsafe impl Send for DocumentWrapper {}
unsafe impl Sync for DocumentWrapper {}

static PDFIUM: OnceCell<PdfiumWrapper> = OnceCell::new();

/// Initializes and retrieves the static Pdfium instance.
///
/// Attempts to bind to the system library first, falling back to a local
/// binary if necessary. Panics if the library cannot be loaded.
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

/// Helper function to generate a bitmap from a specific page at a given scale.
///
/// Encapsulates the configuration logic shared between standard rendering and OCR.
fn generate_bitmap<'a>(page: &'a PdfPage<'a>, scale: f32) -> PyResult<PdfBitmap<'a>> {
    let width = (page.width().value * scale) as i32;
    let height = (page.height().value * scale) as i32;

    let render_config = PdfRenderConfig::new()
        .set_target_width(width)
        .set_target_height(height)
        .rotate_if_landscape(PdfPageRenderRotation::None, true);

    page.render_with_config(&render_config)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}

/// Represents the result of a page render operation passed back to Python.
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
///
/// Manages the underlying Pdfium document state and provides methods for
/// rendering, text extraction, and OCR.
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
    ///     dark_mode_int (u8): 1 to invert colors for dark mode, 0 for standard.
    ///
    /// Returns:
    ///     RenderResult: Object containing width, height, and raw BGRA bytes.
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

        let bitmap = generate_bitmap(&page, scale)?;
        let mut buffer = bitmap.as_raw_bytes().to_vec();

        if dark_mode {
            buffer.chunks_exact_mut(4).for_each(|pixel| {
                pixel[0] = 255 - pixel[0]; // Blue
                pixel[1] = 255 - pixel[1]; // Green
                pixel[2] = 255 - pixel[2]; // Red
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
    ///     String: The extracted text content, or an empty string if bounds are exceeded.
    fn get_page_text(&self, page_index: u16) -> PyResult<String> {
        let doc_guard = self.inner.lock().unwrap();
        let pages = doc_guard.0.pages();

        if (page_index as usize) >= (pages.len() as usize) {
            return Ok(String::new());
        }

        let page = pages
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        let text_accessor = page.text().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Text Access Error: {}", e))
        })?;

        if text_accessor.is_empty() {
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

        let bitmap = generate_bitmap(&page, scale)?;
        let mut buffer = bitmap.as_raw_bytes().to_vec();

        buffer.chunks_exact_mut(4).for_each(|pixel| {
            let blue = pixel[0];
            let red = pixel[2];
            pixel[0] = red;
            pixel[2] = blue;
        });

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
