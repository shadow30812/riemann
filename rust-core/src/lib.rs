use once_cell::sync::OnceCell;
use pdfium_render::prelude::*;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use riemann_ocr_worker::OcrEngine;
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
        render_flags: u32,
    ) -> PyResult<RenderResult> {
        let doc_guard = self.inner.lock().unwrap();
        let page = doc_guard
            .0
            .pages()
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        let render_config = PdfRenderConfig::new()
            .set_target_width((page.width().value * scale) as i32)
            .set_target_height((page.height().value * scale) as i32);

        let bitmap = page.render_with_config(&render_config).unwrap();
        let mut bytes = bitmap.as_raw_bytes().to_vec();

        // Alpha Blending + Inversion
        for chunk in bytes.chunks_exact_mut(4) {
            let a = chunk[3] as f32 / 255.0;
            for i in 0..3 {
                let mut c = (chunk[i] as f32 * a + 255.0 * (1.0 - a)) as u8;
                if render_flags == 1 {
                    c = 255 - c;
                }
                chunk[i] = c;
            }
            chunk[3] = 255;
        }

        Ok(RenderResult {
            width: bitmap.width() as u32,
            height: bitmap.height() as u32,
            data: PyBytes::new(py, &bytes).into(),
        })
    }

    fn get_page_text(&self, page_index: u16) -> PyResult<String> {
        let doc_guard = self.inner.lock().unwrap();
        let page = doc_guard
            .0
            .pages()
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
        Ok(page.text().map(|t| t.all()).unwrap_or_default())
    }

    fn get_page_text_with_ocr(&self, page_index: u16) -> PyResult<String> {
        let doc_guard = self.inner.lock().unwrap();
        let page = doc_guard
            .0
            .pages()
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        // Render high-res for OCR (Scale 3.0)
        let render_config =
            PdfRenderConfig::new().set_target_width((page.width().value * 3.0) as i32);
        let bitmap = page.render_with_config(&render_config).unwrap();

        let engine = OcrEngine::new();
        // FIXED: Added '&' borrow for bytes
        engine
            .recognize_text(
                bitmap.width() as u32,
                bitmap.height() as u32,
                &bitmap.as_raw_bytes(),
            )
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
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
fn riemann_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<PdfEngine>()?;
    m.add_class::<RiemannDocument>()?;
    m.add_class::<RenderResult>()?;
    Ok(())
}
