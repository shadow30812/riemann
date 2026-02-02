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

/// Type alias for form widget data: (index, bounds, field_type, value, checked)
type FormWidget = (usize, (f32, f32, f32, f32), String, String, bool);

/// Type alias for text segment data: (text, bounds)
type TextSegment = (String, (f32, f32, f32, f32));

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

    /// Searches for a string on a page and returns a list of bounding boxes.
    /// Returns a list of (left, top, right, bottom) tuples.
    fn search_page(&self, page_index: u16, term: String) -> PyResult<Vec<(f32, f32, f32, f32)>> {
        let doc_guard = self.inner.lock().unwrap();
        let pages = doc_guard.0.pages();

        if (page_index as usize) >= (pages.len() as usize) {
            return Ok(Vec::new());
        }

        let page = pages
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

        let text_accessor = page
            .text()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        // 1. Configure Search Options (Case-insensitive, default)
        let search_options = PdfSearchOptions::new();

        // 2. Execute Search
        let search = text_accessor
            .search(&term, &search_options)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        let mut rects = Vec::new();

        // 3. Iterate Results (API Fix: iter requires a direction)
        for segments in search.iter(PdfSearchDirection::SearchForward) {
            // 4. Iterate Segments inside the result
            for segment in segments.iter() {
                // 5. Get Bounds
                let rect = segment.bounds();

                // Pdfium coordinates: (left, bottom, right, top) usually,
                // but PdfRect usually provides left/bottom/right/top.
                // We extract the raw f32 values.
                rects.push((
                    rect.left().value,
                    rect.top().value,
                    rect.right().value,
                    rect.bottom().value,
                ));
            }
        }

        Ok(rects)
    }
    /// Retrieves all text segments and their bounding boxes for a specific page.
    fn get_text_segments(&self, page_index: u16) -> PyResult<Vec<TextSegment>> {
        let doc_guard = self.inner.lock().unwrap();
        let pages = doc_guard.0.pages();

        if (page_index as usize) >= (pages.len() as usize) {
            return Ok(Vec::new());
        }

        let page = pages
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>((e.to_string(),)))?;

        let text_accessor = page.text().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                (format!("Text Access Error: {}", e),),
            )
        })?;

        let mut segments = Vec::new();

        for segment in text_accessor.segments().iter() {
            // segment.bounds() returns PdfRect, not Result
            let rect = segment.bounds();

            segments.push((
                segment.text(),
                (
                    rect.left().value,
                    rect.top().value,
                    rect.right().value,
                    rect.bottom().value,
                ),
            ));
        }

        Ok(segments)
    }

    /// Creates a markup annotation (Highlight, Underline, Strikeout) using the provided rectangles.
    fn create_markup_annotation(
        &self,
        page_index: u16,
        rects: Vec<(f32, f32, f32, f32)>,
        subtype: String,
        color: (u8, u8, u8),
    ) -> PyResult<()> {
        let mut doc_guard = self.inner.lock().unwrap();
        let pages = doc_guard.0.pages_mut();

        let mut page = pages
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>((e.to_string(),)))?;

        let pdf_color = PdfColor::new(color.0, color.1, color.2, 255);

        // Calculate union rect first
        let mut union_rect: Option<PdfRect> = None;
        for (left, top, right, bottom) in &rects {
            let u = PdfRect::new_from_values(*left, *bottom, *right, *top);
            if let Some(r) = union_rect {
                let ul = r.left().value.min(*left);
                let ub = r.bottom().value.min(*bottom);
                let ur = r.right().value.max(*right);
                let ut = r.top().value.max(*top);
                union_rect = Some(PdfRect::new_from_values(ul, ub, ur, ut));
            } else {
                union_rect = Some(u);
            }
        }

        // Helper to apply union bounds
        let apply_bounds = |annot: &mut dyn PdfPageAnnotationCommon| -> PyResult<()> {
            if let Some(u) = union_rect {
                annot.set_bounds(u).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                })?;
            }
            Ok(())
        };

        match subtype.to_lowercase().as_str() {
            "underline" => {
                let mut annot = page
                    .annotations_mut()
                    .create_underline_annotation()
                    .map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                    })?;

                annot.set_stroke_color(pdf_color).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                })?;

                for (left, top, right, bottom) in &rects {
                    let rect = PdfRect::new_from_values(*left, *bottom, *right, *top);
                    annot
                        .attachment_points_mut()
                        .create_attachment_point_at_end(PdfQuadPoints::from_rect(&rect))
                        .map_err(|e| {
                            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                        })?;
                }
                apply_bounds(&mut annot)?;
            }
            "strikeout" => {
                let mut annot = page
                    .annotations_mut()
                    .create_strikeout_annotation()
                    .map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                    })?;

                annot.set_stroke_color(pdf_color).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                })?;

                for (left, top, right, bottom) in &rects {
                    let rect = PdfRect::new_from_values(*left, *bottom, *right, *top);
                    annot
                        .attachment_points_mut()
                        .create_attachment_point_at_end(PdfQuadPoints::from_rect(&rect))
                        .map_err(|e| {
                            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                        })?;
                }
                apply_bounds(&mut annot)?;
            }
            _ => {
                // Highlight
                let mut annot = page
                    .annotations_mut()
                    .create_highlight_annotation()
                    .map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                    })?;

                annot.set_stroke_color(pdf_color).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                })?;

                for (left, top, right, bottom) in &rects {
                    let rect = PdfRect::new_from_values(*left, *bottom, *right, *top);
                    annot
                        .attachment_points_mut()
                        .create_attachment_point_at_end(PdfQuadPoints::from_rect(&rect))
                        .map_err(|e| {
                            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                        })?;
                }
                apply_bounds(&mut annot)?;
            }
        };

        Ok(())
    }

    /// Retrieves form field widgets for a page to support form filling.
    fn get_form_widgets(&self, page_index: u16) -> PyResult<Vec<FormWidget>> {
        let doc_guard = self.inner.lock().unwrap();
        let pages = doc_guard.0.pages();

        if (page_index as usize) >= (pages.len() as usize) {
            return Ok(Vec::new());
        }

        let page = pages
            .get(page_index)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>((e.to_string(),)))?;

        let annotations = page.annotations();
        let mut widgets = Vec::new();

        for (idx, annotation) in annotations.iter().enumerate() {
            if let Some(field) = annotation.as_form_field() {
                let rect = annotation.bounds().map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>((e.to_string(),))
                })?;

                let f_type = format!("{:?}", field.field_type());

                let value = if let Some(tf) = field.as_text_field() {
                    tf.value().unwrap_or_default()
                } else {
                    String::new()
                };

                let checked = if let Some(cb) = field.as_checkbox_field() {
                    cb.is_checked().unwrap_or(false)
                } else if let Some(rb) = field.as_radio_button_field() {
                    rb.is_checked().unwrap_or(false)
                } else {
                    false
                };

                widgets.push((
                    idx,
                    (
                        rect.left().value,
                        rect.top().value,
                        rect.right().value,
                        rect.bottom().value,
                    ),
                    f_type,
                    value,
                    checked,
                ));
            }
        }
        Ok(widgets)
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
