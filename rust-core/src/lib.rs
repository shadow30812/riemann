use once_cell::sync::OnceCell;
use pdfium_render::prelude::*;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::sync::Mutex;

// --- Thread Safety Wrappers ---
struct PdfiumWrapper(Pdfium);
unsafe impl Send for PdfiumWrapper {}
unsafe impl Sync for PdfiumWrapper {}

struct DocumentWrapper(PdfDocument<'static>);
unsafe impl Send for DocumentWrapper {}
unsafe impl Sync for DocumentWrapper {}

static PDFIUM: OnceCell<PdfiumWrapper> = OnceCell::new();

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

#[pyclass]
struct RenderResult {
    #[pyo3(get)]
    width: u32,
    #[pyo3(get)]
    height: u32,
    #[pyo3(get)]
    data: Py<PyBytes>,
}

#[pyclass]
struct RiemannDocument {
    inner: Mutex<DocumentWrapper>,
    #[pyo3(get)]
    page_count: usize,
}

#[pymethods]
impl RiemannDocument {
    fn render_page(
        &self,
        py: Python,
        page_index: u16,
        scale: f32,
        dark_mode_int: u8, // Use u8 to bypass strict Python bool conversion issues
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

        // Invert colors if Dark Mode is active
        if dark_mode {
            // Pdfium typically renders BGRA. We invert RGB, keep Alpha.
            for i in 0..buffer.len() {
                if (i + 1) % 4 != 0 {
                    buffer[i] = 255 - buffer[i];
                }
            }
        }

        let data = PyBytes::new_bound(py, &buffer);

        Ok(RenderResult {
            width: bitmap.width() as u32,
            height: bitmap.height() as u32,
            data: data.into(),
        })
    }

    fn get_page_text(&self, page_index: u16) -> PyResult<String> {
        let doc_guard = self.inner.lock().unwrap();

        let pages = doc_guard.0.pages();

        // FIX 1: Robust bounds check.
        // We cast everything to usize to avoid type mismatch errors (u16 vs usize).
        if (page_index as usize) >= (pages.len() as usize) {
            return Ok(String::new());
        }

        let page = pages
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        let text_accessor = page.text().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Text Access Error: {}", e))
        })?;

        // FIX 2: Check length before extraction to prevent FPDF empty-buffer segfaults.
        if text_accessor.len() == 0 {
            return Ok(String::new());
        }

        // FIX 3: Use .all() now that we have guarded against invalid pages/lengths.
        // This is safer than manual iteration which was failing trait bounds.
        Ok(text_accessor.all())
    }
}

#[pyclass]
struct PdfEngine;
#[pymethods]
impl PdfEngine {
    #[new]
    fn new() -> Self {
        get_pdfium();
        PdfEngine
    }
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
