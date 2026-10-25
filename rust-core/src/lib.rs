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
use std::mem::ManuallyDrop;
use std::sync::{Mutex, MutexGuard};

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
/// `RiemannDocument` struct. The document is closed under the global PDFium
/// lock so that closing one document can never race a render of another.
struct DocumentWrapper(ManuallyDrop<PdfDocument<'static>>);
unsafe impl Send for DocumentWrapper {}
unsafe impl Sync for DocumentWrapper {}

impl Drop for DocumentWrapper {
    fn drop(&mut self) {
        let _guard = pdfium_guard();
        unsafe { ManuallyDrop::drop(&mut self.0) };
    }
}

/// Global singleton instance of the Pdfium library.
static PDFIUM: OnceCell<PdfiumWrapper> = OnceCell::new();

/// PDFium is not thread-safe, so every call into it is serialised through this
/// lock. Documents are rendered from background threads (with the GIL released),
/// which makes the lock mandatory rather than defensive.
static PDFIUM_LOCK: Mutex<()> = Mutex::new(());

/// Acquires the global PDFium lock, recovering from poisoning.
fn pdfium_guard() -> MutexGuard<'static, ()> {
    PDFIUM_LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// Acquires a document lock, recovering from poisoning.
fn lock_doc(inner: &Mutex<DocumentWrapper>) -> MutexGuard<'_, DocumentWrapper> {
    inner.lock().unwrap_or_else(|e| e.into_inner())
}

fn runtime_err(e: impl ToString) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())
}

fn value_err(e: impl ToString) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
}

/// Applies the page theme in place to BGRA pixels (PDFium's native order, which
/// is also what Qt reads for `Format_ARGB32` on little-endian machines).
/// Mode 0 leaves the buffer untouched, 1 inverts it, 2 inverts lightness while
/// keeping hue and saturation.
fn convert_pixels(buffer: &mut [u8], theme_mode: u8) {
    match theme_mode {
        1 => buffer.chunks_exact_mut(4).for_each(|pixel| {
            pixel[0] = 255 - pixel[0];
            pixel[1] = 255 - pixel[1];
            pixel[2] = 255 - pixel[2];
        }),
        2 => buffer.chunks_exact_mut(4).for_each(|pixel| {
            let (c0, c1, c2) = smart_dark(pixel[2], pixel[1], pixel[0]);
            pixel[0] = c0;
            pixel[1] = c1;
            pixel[2] = c2;
        }),
        _ => {}
    }
}

/// Inverts the HSL lightness of one pixel, keeping hue and saturation.
///
/// Takes `(red, green, blue)` but names them `b_raw`, `g_raw`, `r_raw` and
/// returns `(blue, green, red)`, mirroring the original RGBA-based code. HSL
/// lightness inversion commutes with swapping red and blue, so the result is
/// the same; keeping the original arithmetic keeps the output bit-identical.
fn smart_dark(b_raw: u8, g_raw: u8, r_raw: u8) -> (u8, u8, u8) {
    let b = b_raw as f32 / 255.0;
    let g = g_raw as f32 / 255.0;
    let r = r_raw as f32 / 255.0;

    let max = r.max(g).max(b);
    let min = r.min(g).min(b);
    let l = (max + min) / 2.0;

    if max == min {
        let val = ((1.0 - l) * 255.0) as u8;
        return (val, val, val);
    }

    let d = max - min;
    let s = if l > 0.5 {
        d / (2.0 - max - min)
    } else {
        d / (max + min)
    };

    let mut h = if max == r {
        (g - b) / d + (if g < b { 6.0 } else { 0.0 })
    } else if max == g {
        (b - r) / d + 2.0
    } else {
        (r - g) / d + 4.0
    };
    h /= 6.0;

    let new_l = 1.0 - l;
    let q = if new_l < 0.5 {
        new_l * (1.0 + s)
    } else {
        new_l + s - new_l * s
    };
    let p = 2.0 * new_l - q;

    let hue_to_rgb = |mut t: f32| -> f32 {
        if t < 0.0 {
            t += 1.0;
        }
        if t > 1.0 {
            t -= 1.0;
        }
        if t < 1.0 / 6.0 {
            return p + (q - p) * 6.0 * t;
        }
        if t < 1.0 / 2.0 {
            return q;
        }
        if t < 2.0 / 3.0 {
            return p + (q - p) * (2.0 / 3.0 - t) * 6.0;
        }
        p
    };

    (
        (hue_to_rgb(h + 1.0 / 3.0) * 255.0) as u8,
        (hue_to_rgb(h) * 255.0) as u8,
        (hue_to_rgb(h - 1.0 / 3.0) * 255.0) as u8,
    )
}

/// Encodes a link destination as `#page=N`, adding `&top=Y` (PDF user space,
/// origin at the bottom-left) when the destination names a vertical position.
fn destination_target(dest: &PdfDestination) -> Option<String> {
    let page_index = dest.page_index().ok()?;
    let mut target = format!("#page={}", page_index as u32 + 1);

    let top = match dest.view_settings() {
        Ok(PdfDestinationViewSettings::SpecificCoordinatesAndZoom(_, y, _)) => y,
        Ok(PdfDestinationViewSettings::FitPageHorizontallyToWindow(y)) => y,
        Ok(PdfDestinationViewSettings::FitBoundsHorizontallyToWindow(y)) => y,
        Ok(PdfDestinationViewSettings::FitPageToRectangle(rect)) => Some(rect.top()),
        _ => None,
    };
    if let Some(y) = top {
        if y.value.is_finite() {
            target.push_str(&format!("&top={:.2}", y.value));
        }
    }
    Some(target)
}

/// Resolves what a link points at: an internal destination (direct or via a
/// GoTo action, named destinations included) or an external URI.
fn link_target(link: &PdfLink) -> Option<String> {
    if let Some(dest) = link.destination() {
        if let Some(target) = destination_target(&dest) {
            return Some(target);
        }
    }

    let action = link.action()?;
    if let Some(local) = action.as_local_destination_action() {
        if let Ok(dest) = local.destination() {
            if let Some(target) = destination_target(&dest) {
                return Some(target);
            }
        }
    }
    if let Some(uri_action) = action.as_uri_action() {
        if let Ok(uri) = uri_action.uri() {
            if !uri.is_empty() {
                return Some(uri);
            }
        }
    }
    None
}

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
                .or_else(|_| {
                    Pdfium::bind_to_library(Pdfium::pdfium_platform_library_name_at_path("libs/"))
                })
                .or_else(|_| {
                    Pdfium::bind_to_library(Pdfium::pdfium_platform_library_name_at_path("../libs/"))
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
    page.render_with_config(&render_config(width, height))
        .map_err(runtime_err)
}

/// Render settings shared by every page render: annotations on, white
/// background, and PDFium's native BGRA byte order (what Qt reads for
/// `Format_ARGB32`), so no channel swap is needed afterwards.
fn render_config(width: i32, height: i32) -> PdfRenderConfig {
    PdfRenderConfig::new()
        .set_fixed_size(width, height)
        .render_annotations(true)
        .set_reverse_byte_order(false)
        .set_clear_color(PdfColor::new(255, 255, 255, 255))
}

/// Type definition for form widget data.
/// Tuple structure: `(index, bounds_tuple, field_type, value, is_checked)`.
type FormWidget = (usize, (f32, f32, f32, f32), String, String, bool);

/// Type definition for text segment data.
/// Tuple structure: `(text_content, bounds_tuple)`.
type TextSegment = (String, (f32, f32, f32, f32));

/// Type definition for link data.
/// Tuple structure: `(url, bounds_tuple)`.
type Link = (String, (f32, f32, f32, f32));

/// Type definition for a comment made in another application.
/// Tuple structure: `(subtype, contents, author, date, bounds_tuple)`.
type Comment = (String, String, String, String, (f32, f32, f32, f32));

/// The PDF `/Subtype` name for an annotation type.
fn annotation_subtype_name(kind: PdfPageAnnotationType) -> String {
    match kind {
        PdfPageAnnotationType::Strikeout => "StrikeOut".to_string(),
        PdfPageAnnotationType::Redacted => "Redact".to_string(),
        PdfPageAnnotationType::ThreeD => "3D".to_string(),
        other => format!("{other:?}"),
    }
}

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
    /// This method handles scaling and optional dark mode inversion. The GIL is
    /// released while PDFium renders, so background render threads do not stall
    /// the UI thread.
    ///
    /// # Arguments
    /// * `py` - The Python GIL token.
    /// * `page_index` - Zero-based index of the page to render.
    /// * `scale` - Zoom level/scaling factor.
    /// * `theme_mode` - Integer flag (0 for Light, 1 for Fast Dark, 2 for Smart Dark).
    ///
    /// # Returns
    /// A `RenderResult` object containing the image data.
    fn render_page(
        &self,
        py: Python,
        page_index: u16,
        scale: f32,
        theme_mode: u8,
    ) -> PyResult<RenderResult> {
        let inner = &self.inner;

        // The page size comes from the document's page tree, so the page itself
        // is only loaded once, for the render below.
        let (width, height) = py.allow_threads(|| -> PyResult<(i32, i32)> {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let size = doc_guard.0.pages().page_size(page_index).map_err(value_err)?;
            Ok((
                (size.width().value * scale) as i32,
                (size.height().value * scale) as i32,
            ))
        })?;
        if width <= 0 || height <= 0 {
            return Err(value_err(format!("page {page_index} renders to an empty image")));
        }
        let len = width as usize * height as usize * 4;

        // Allocate the result uninitialised and let PDFium draw straight into it.
        // Nothing else can see the object until it is returned, so it is safe to
        // fill with the GIL released. This avoids copying the image out of
        // PDFium and again into Python, which cost more than the render itself.
        let data: Py<PyBytes> = unsafe {
            let ptr = pyo3::ffi::PyBytes_FromStringAndSize(
                std::ptr::null(),
                len as pyo3::ffi::Py_ssize_t,
            );
            if ptr.is_null() {
                return Err(PyErr::fetch(py));
            }
            Py::from_owned_ptr(py, ptr)
        };
        let addr = unsafe { pyo3::ffi::PyBytes_AsString(data.as_ptr()) } as usize;

        py.allow_threads(move || -> PyResult<()> {
            let buffer = unsafe { std::slice::from_raw_parts_mut(addr as *mut u8, len) };
            {
                let _guard = pdfium_guard();
                let doc_guard = lock_doc(inner);
                let page = doc_guard.0.pages().get(page_index).map_err(value_err)?;
                let mut bitmap = unsafe {
                    PdfBitmap::from_bytes(
                        width,
                        height,
                        PdfBitmapFormat::BGRA,
                        &mut buffer[..],
                        doc_guard.0.bindings(),
                    )
                }
                .map_err(runtime_err)?;
                page.render_into_bitmap_with_config(&mut bitmap, &render_config(width, height))
                    .map_err(runtime_err)?;
            }
            convert_pixels(buffer, theme_mode);
            Ok(())
        })?;

        Ok(RenderResult {
            width: width as u32,
            height: height as u32,
            data,
        })
    }

    /// Returns how many digital signatures the document contains. Reading the
    /// count is cheap; validating them is left to the Python side.
    fn signature_count(&self, py: Python) -> usize {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            doc_guard.0.signatures().len() as usize
        })
    }

    /// Returns the size of a page in PDF points as `(width, height)`.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    fn get_page_size(&self, py: Python, page_index: u16) -> PyResult<(f32, f32)> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let rect = doc_guard.0.pages().page_size(page_index).map_err(value_err)?;
            Ok((rect.width().value, rect.height().value))
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
    fn get_page_text(&self, py: Python, page_index: u16) -> PyResult<String> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(String::new());
            }

            let page = pages.get(page_index).map_err(value_err)?;
            let text_accessor = page
                .text()
                .map_err(|e| runtime_err(format!("Text Access Error: {}", e)))?;

            if text_accessor.is_empty() {
                return Ok(String::new());
            }

            Ok(text_accessor.all())
        })
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
    fn ocr_page(&self, py: Python, page_index: u16, scale: f32) -> PyResult<String> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let (buffer, width, height) = {
                let _guard = pdfium_guard();
                let doc_guard = lock_doc(inner);
                let page = doc_guard.0.pages().get(page_index).map_err(value_err)?;
                let bitmap = generate_bitmap(&page, scale)?;
                (
                    bitmap.as_raw_bytes(),
                    bitmap.width() as u32,
                    bitmap.height() as u32,
                )
            };

            let engine = OcrEngine::new();
            engine
                .recognize_text(width, height, &buffer)
                .map_err(runtime_err)
        })
    }

    /// Searches the page for a specific text term.
    ///
    /// # Arguments
    /// * `page_index` - Zero-based index of the page.
    /// * `term` - The string to search for.
    ///
    /// # Returns
    /// A list of bounding boxes `(left, top, right, bottom)` for all occurrences.
    fn search_page(
        &self,
        py: Python,
        page_index: u16,
        term: String,
    ) -> PyResult<Vec<(f32, f32, f32, f32)>> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_index).map_err(value_err)?;
            let text_accessor = page.text().map_err(runtime_err)?;
            let search_options = PdfSearchOptions::new();
            let search = text_accessor
                .search(&term, &search_options)
                .map_err(runtime_err)?;

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
        })
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
    fn get_text_segments(&self, py: Python, page_index: u16) -> PyResult<Vec<TextSegment>> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_index).map_err(value_err)?;
            let text_accessor = page
                .text()
                .map_err(|e| runtime_err(format!("Text Access Error: {}", e)))?;

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
        })
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
        let _guard = pdfium_guard();
        let mut doc_guard = lock_doc(&self.inner);
        let pages = doc_guard.0.pages_mut();

        let mut page = pages.get(page_index).map_err(value_err)?;

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
                annot.set_bounds(u).map_err(runtime_err)?;
            }
            Ok(())
        };

        match subtype.to_lowercase().as_str() {
            "underline" => {
                let mut annot = page
                    .annotations_mut()
                    .create_underline_annotation()
                    .map_err(runtime_err)?;
                annot.set_stroke_color(pdf_color).map_err(runtime_err)?;
                for (left, top, right, bottom) in &rects {
                    let rect = PdfRect::new_from_values(*left, *bottom, *right, *top);
                    annot
                        .attachment_points_mut()
                        .create_attachment_point_at_end(PdfQuadPoints::from_rect(&rect))
                        .map_err(runtime_err)?;
                }
                apply_bounds(&mut annot)?;
            }
            "strikeout" => {
                let mut annot = page
                    .annotations_mut()
                    .create_strikeout_annotation()
                    .map_err(runtime_err)?;
                annot.set_stroke_color(pdf_color).map_err(runtime_err)?;
                for (left, top, right, bottom) in &rects {
                    let rect = PdfRect::new_from_values(*left, *bottom, *right, *top);
                    annot
                        .attachment_points_mut()
                        .create_attachment_point_at_end(PdfQuadPoints::from_rect(&rect))
                        .map_err(runtime_err)?;
                }
                apply_bounds(&mut annot)?;
            }
            _ => {
                let mut annot = page
                    .annotations_mut()
                    .create_highlight_annotation()
                    .map_err(runtime_err)?;
                annot.set_stroke_color(pdf_color).map_err(runtime_err)?;
                for (left, top, right, bottom) in &rects {
                    let rect = PdfRect::new_from_values(*left, *bottom, *right, *top);
                    annot
                        .attachment_points_mut()
                        .create_attachment_point_at_end(PdfQuadPoints::from_rect(&rect))
                        .map_err(runtime_err)?;
                }
                apply_bounds(&mut annot)?;
            }
        };
        page.flatten()
            .map_err(|e| runtime_err(format!("Failed to flatten page: {}", e)))?;
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
    fn get_form_widgets(&self, py: Python, page_index: u16) -> PyResult<Vec<FormWidget>> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_index).map_err(value_err)?;
            let annotations = page.annotations();
            let mut widgets = Vec::new();

            for (idx, annotation) in annotations.iter().enumerate() {
                if let Some(field) = annotation.as_form_field() {
                    let rect = annotation.bounds().map_err(runtime_err)?;
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
        })
    }

    /// Lists comments left on a page by other applications (Acrobat, Preview,
    /// Okular): every annotation with text contents except links, form
    /// widgets and popups.
    ///
    /// # Returns
    /// `(crop_box, comments)`: the crop box as `(left, bottom, right, top)` and
    /// a list of `(subtype, contents, author, date, (left, top, right, bottom))`.
    /// Dates are the raw PDF date strings (`D:YYYYMMDDHHmmSS...`).
    fn get_comments(
        &self,
        py: Python,
        page_index: u16,
    ) -> PyResult<((f32, f32, f32, f32), Vec<Comment>)> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();
            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(((0.0, 0.0, 0.0, 0.0), Vec::new()));
            }
            let page = pages.get(page_index).map_err(value_err)?;

            let crop = match page.boundaries().crop() {
                Ok(b) => (
                    b.bounds.left().value,
                    b.bounds.bottom().value,
                    b.bounds.right().value,
                    b.bounds.top().value,
                ),
                Err(_) => (0.0, 0.0, page.width().value, page.height().value),
            };

            let mut comments = Vec::new();
            for annotation in page.annotations().iter() {
                let kind = annotation.annotation_type();
                if matches!(
                    kind,
                    PdfPageAnnotationType::Link
                        | PdfPageAnnotationType::Widget
                        | PdfPageAnnotationType::XfaWidget
                        | PdfPageAnnotationType::Popup
                ) {
                    continue;
                }
                let contents = annotation.contents().unwrap_or_default().trim().to_string();
                if contents.is_empty() {
                    continue;
                }
                let author = annotation.creator().unwrap_or_default().trim().to_string();
                let date = annotation
                    .modification_date()
                    .or_else(|| annotation.creation_date())
                    .unwrap_or_default()
                    .trim()
                    .to_string();
                let bounds = annotation
                    .bounds()
                    .map(|r| (r.left().value, r.top().value, r.right().value, r.bottom().value))
                    .unwrap_or((0.0, 0.0, 0.0, 0.0));
                comments.push((annotation_subtype_name(kind), contents, author, date, bounds));
            }
            Ok((crop, comments))
        })
    }

    /// Extracts clickable links and their bounding boxes.
    ///
    /// Internal links (explicit or named destinations, and GoTo actions) are
    /// reported as `#page=N`, with `&top=Y` appended when the destination names
    /// a vertical position in PDF user space. External links carry their URI.
    ///
    /// # Returns
    /// A list of tuples `(target, (left, top, right, bottom))`.
    fn get_links(&self, py: Python, page_index: u16) -> PyResult<Vec<Link>> {
        let inner = &self.inner;
        py.allow_threads(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_index).map_err(value_err)?;
            let mut links = Vec::new();

            for link in page.links().iter() {
                let rect = match link.rect() {
                    Ok(rect) => rect,
                    Err(_) => continue,
                };
                if let Some(target) = link_target(&link) {
                    links.push((
                        target,
                        (
                            rect.left().value,
                            rect.top().value,
                            rect.right().value,
                            rect.bottom().value,
                        ),
                    ));
                }
            }
            Ok(links)
        })
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
        let _guard = pdfium_guard();
        get_pdfium();
        PdfEngine
    }

    /// Loads a PDF document from the local file system.
    ///
    /// The GIL is released while PDFium parses the file, so loading a large
    /// document on a worker thread keeps the interface responsive.
    ///
    /// # Arguments
    /// * `path` - The absolute or relative path to the PDF file.
    /// * `password` - Password to the PDF file.
    ///
    /// # Returns
    /// A `RiemannDocument` instance ready for rendering and interaction.
    #[pyo3(signature = (path, password=None))]
    fn load_document(
        &self,
        py: Python,
        path: &str,
        password: Option<String>,
    ) -> PyResult<RiemannDocument> {
        let path = path.to_owned();
        let (wrapper, page_count) = py.allow_threads(move || -> PyResult<(DocumentWrapper, usize)> {
            let pwd_static: Option<&'static str> =
                password.map(|p| Box::leak(p.into_boxed_str()) as &'static str);
            let _guard = pdfium_guard();
            let doc = get_pdfium()
                .load_pdf_from_file(&path, pwd_static)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyIOError, _>(e.to_string()))?;
            let page_count = doc.pages().len() as usize;
            Ok((DocumentWrapper(ManuallyDrop::new(doc)), page_count))
        })?;

        Ok(RiemannDocument {
            page_count,
            inner: Mutex::new(wrapper),
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
