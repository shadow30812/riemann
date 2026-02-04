//! # Riemann Core
//!
//! This module provides the Python bindings for the Riemann PDF engine.
//! It acts as a bridge between the high-level Python UI and the low-level
//! PDFium rendering library.
//!
//! Key responsibilities:
//! - Rendering PDF pages to image buffers.
//! - Extracting text and searching within documents.
//! - Managing annotations and form data.
//! - Interfacing with the OCR worker for text recognition.

use once_cell::sync::OnceCell;
use pdfium_render::prelude::*;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use riemann_ocr_worker::OcrEngine;
use std::sync::Mutex;

/// A thread-safe wrapper for the `Pdfium` library instance.
///
/// required because `pdfium_render::Pdfium` does not implement `Send` or `Sync`
/// by default, but we need to store it in a global static `OnceCell` and access
/// it across Python threads.
struct PdfiumWrapper(Pdfium);
unsafe impl Send for PdfiumWrapper {}
unsafe impl Sync for PdfiumWrapper {}

/// A thread-safe wrapper for a specific `PdfDocument`.
///
/// Allows the document to be shared across threads, guarded by a Mutex in the
/// `RiemannDocument` struct.
struct DocumentWrapper(PdfDocument<'static>);
unsafe impl Send for DocumentWrapper {}
unsafe impl Sync for DocumentWrapper {}

/// Global singleton instance of the Pdfium library.
static PDFIUM: OnceCell<PdfiumWrapper> = OnceCell::new();

/// Initializes and retrieves the static Pdfium instance.
///
/// This function attempts to load the Pdfium shared library from the system
/// path first. If that fails, it falls back to looking for a local binary
/// in the current working directory.
///
/// # Panics
/// Panics if the library cannot be located or loaded.
///
/// # Returns
/// A static reference to the initialized `Pdfium` instance.
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

/// Generates a bitmap for a specific PDF page.
///
/// Configures the renderer to respect the target dimensions and rotate
/// landscape pages automatically. It also enables annotation rendering.
///
/// # Arguments
/// * `page` - Reference to the `PdfPage` to render.
/// * `scale` - Scaling factor for the output bitmap (e.g., 2.0 for HiDPI).
///
/// # Returns
/// A `PyResult` containing the generated `PdfBitmap`, or an error if rendering fails.
fn generate_bitmap<'a>(page: &'a PdfPage<'a>, scale: f32) -> PyResult<PdfBitmap<'a>> {
    let width = (page.width().value * scale) as i32;
    let height = (page.height().value * scale) as i32;

    let render_config = PdfRenderConfig::new()
        .set_target_width(width)
        .set_target_height(height)
        .rotate_if_landscape(PdfPageRenderRotation::None, true)
        .render_annotations(true);

    page.render_with_config(&render_config)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}

/// Type definition for form widget data.
/// Tuple structure: `(index, bounds_tuple, field_type, value, is_checked)`.
type FormWidget = (usize, (f32, f32, f32, f32), String, String, bool);

/// Type definition for text segment data.
/// Tuple structure: `(text_content, bounds_tuple)`.
type TextSegment = (String, (f32, f32, f32, f32));

/// Encapsulates the output of a page render operation.
///
/// This struct is exposed to Python to provide the raw pixel data along with
/// the dimensions necessary to construct a Qt QImage or similar object.
#[pyclass]
struct RenderResult {
    /// The width of the rendered image in pixels.
    #[pyo3(get)]
    width: u32,
    /// The height of the rendered image in pixels.
    #[pyo3(get)]
    height: u32,
    /// The raw BGRA pixel data as a Python bytes object.
    #[pyo3(get)]
    data: Py<PyBytes>,
}

/// A Python-compatible wrapper around a loaded PDF document.
///
/// This struct manages the lifetime and thread-safe access to the underlying
/// PDFium document. It exposes methods for rendering, text extraction, and
/// interaction.
#[pyclass]
struct RiemannDocument {
    inner: Mutex<DocumentWrapper>,
    /// The total number of pages in the document.
    #[pyo3(get)]
    page_count: usize,
}

#[pymethods]
impl RiemannDocument {
    /// Renders a specific page into a byte buffer.
    ///
    /// This method handles scaling and optional dark mode inversion.
    ///
    /// # Arguments
    /// * `py` - The Python GIL token.
    /// * `page_index` - Zero-based index of the page to render.
    /// * `scale` - Zoom level/scaling factor.
    /// * `dark_mode_int` - Integer flag (1 for dark mode, 0 for light).
    ///
    /// # Returns
    /// A `RenderResult` object containing the image data.
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

    /// Extracts all plain text from a specific page.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    ///
    /// # Returns
    /// A string containing the text of the page. Returns an empty string
    /// if the page index is out of bounds or the page contains no text.
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

    /// Performs Optical Character Recognition (OCR) on a page.
    ///
    /// Renders the page to a bitmap, converts the color channels from BGR to RGB
    /// (required by Tesseract), and processes the image using the OCR engine.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    /// * `scale` - Scale factor for the image. Higher scales (2.0+) improve accuracy.
    ///
    /// # Returns
    /// The recognized text string.
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

    /// Searches the page for a specific text term.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    /// * `term` - The string to search for.
    ///
    /// # Returns
    /// A list of bounding boxes `(left, top, right, bottom)` for all occurrences.
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

        let search_options = PdfSearchOptions::new();

        let search = text_accessor
            .search(&term, &search_options)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        let mut rects = Vec::new();

        for segments in search.iter(PdfSearchDirection::SearchForward) {
            for segment in segments.iter() {
                let rect = segment.bounds();
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

    /// Retrieves granular text segments and their positions from a page.
    ///
    /// Useful for features that need to know the exact location of specific words
    /// or lines on the page.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    ///
    /// # Returns
    /// A list of `TextSegment` tuples containing text and bounding boxes.
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

    /// Adds a markup annotation (highlight, underline, or strikeout) to the page.
    ///
    /// This function calculates the union rectangle of all passed `rects` to set
    /// the annotation's outer bounds, and then creates specific attachment points
    /// for the individual highlighted areas.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    /// * `rects` - List of bounding boxes to annotate.
    /// * `subtype` - Type of annotation: "underline", "strikeout", or "highlight".
    /// * `color` - RGB color tuple `(r, g, b)`.
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
        page.flatten().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Failed to flatten page: {}",
                e
            ))
        })?;
        Ok(())
    }

    /// Extracts interactive form field widgets from the page.
    ///
    /// Identifies text fields, checkboxes, and radio buttons, retrieving their
    /// bounds, current values, and checked states.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    ///
    /// # Returns
    /// A list of `FormWidget` tuples.
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
///
/// This struct acts as the factory for loading documents.
#[pyclass]
struct PdfEngine;

#[pymethods]
impl PdfEngine {
    #[new]
    fn new() -> Self {
        get_pdfium();
        PdfEngine
    }

    /// Loads a PDF document from the local file system.
    ///
    /// # Arguments
    /// * `path` - The absolute or relative path to the PDF file.
    ///
    /// # Returns
    /// A `RiemannDocument` instance ready for rendering and interaction.
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

/// The Python module initializer.
///
/// Registers the classes and functions exported by this extension module.
#[pymodule]
fn riemann_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PdfEngine>()?;
    m.add_class::<RiemannDocument>()?;
    m.add_class::<RenderResult>()?;
    Ok(())
}
