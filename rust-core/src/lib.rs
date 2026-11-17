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
//!
//! Notes on the code below:
//!
//! - `collect_outline` caps the walk at 20,000 entries and depth 32, because
//!   malformed files can loop their sibling or child chains.
//! - `render_page` allocates the Python `bytes` result uninitialised and lets
//!   PDFium draw straight into it with the GIL released. Nothing else can see
//!   the object until it is returned, so that is safe, and it avoids copying
//!   the image out of PDFium and again into Python, which cost more than the
//!   render itself. Riemann's own annotations are hidden only in memory for the
//!   render; the flag is never saved back to the file.
//! - `ocr_pages` renders each page under the PDFium lock but runs Tesseract
//!   outside it, once per call, so other documents keep rendering while a
//!   batch is read.

use once_cell::sync::OnceCell;
use pdfium_render::prelude::*;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use riemann_ocr_worker::OcrEngine;
use std::mem::ManuallyDrop;
use std::sync::{Mutex, MutexGuard};

/// Maps PDF user space onto the page as displayed: the crop box with the
/// page's /Rotate applied, origin at its bottom-left, y up. PDFium reports
/// text, link and widget boxes in user space, but pages are rendered (and
/// sized by `get_page_size`) rotated, so every box handed to Python goes
/// through this first.
struct DisplayFrame {
    rotation: u16,
    left: f32,
    bottom: f32,
    right: f32,
    top: f32,
}

impl DisplayFrame {
    fn of(page: &PdfPage) -> Self {
        let rotation = match page.rotation() {
            Ok(PdfPageRenderRotation::Degrees90) => 90,
            Ok(PdfPageRenderRotation::Degrees180) => 180,
            Ok(PdfPageRenderRotation::Degrees270) => 270,
            _ => 0,
        };
        let edges = |r: PdfRect| {
            let (l, rr) = (r.left().value.min(r.right().value), r.left().value.max(r.right().value));
            let (b, t) = (r.bottom().value.min(r.top().value), r.bottom().value.max(r.top().value));
            (l, b, rr, t)
        };
        let boundaries = page.boundaries();
        let media = boundaries.media().ok().map(|b| edges(b.bounds));
        let crop = boundaries.crop().ok().map(|b| edges(b.bounds));
        let (left, bottom, right, top) = match (crop, media) {
            (Some(c), Some(m)) if c.0.max(m.0) < c.2.min(m.2) && c.1.max(m.1) < c.3.min(m.3) => {
                (c.0.max(m.0), c.1.max(m.1), c.2.min(m.2), c.3.min(m.3))
            }
            (_, Some(m)) => m,
            (Some(c), None) => c,
            (None, None) => {
                let (w, h) = (page.width().value, page.height().value);
                if rotation % 180 == 90 { (0.0, 0.0, h, w) } else { (0.0, 0.0, w, h) }
            }
        };
        DisplayFrame { rotation, left, bottom, right, top }
    }

    fn point(&self, x: f32, y: f32) -> (f32, f32) {
        match self.rotation {
            90 => (y - self.bottom, self.right - x),
            180 => (self.right - x, self.top - y),
            270 => (self.top - y, x - self.left),
            _ => (x - self.left, y - self.bottom),
        }
    }

    /// Width of the page as displayed, in points.
    fn width(&self) -> f32 {
        if self.rotation % 180 == 90 { self.top - self.bottom } else { self.right - self.left }
    }

    /// Height of the page as displayed, in points.
    fn height(&self) -> f32 {
        if self.rotation % 180 == 90 { self.right - self.left } else { self.top - self.bottom }
    }

    /// Converts a (left, top, right, bottom) box.
    fn rect(&self, r: &PdfRect) -> (f32, f32, f32, f32) {
        let (x1, y1) = self.point(r.left().value, r.top().value);
        let (x2, y2) = self.point(r.right().value, r.bottom().value);
        (x1.min(x2), y1.max(y2), x1.max(x2), y1.min(y2))
    }
}

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

/// Converts a page index from Python to PDFium's type, rejecting ones it can't hold.
fn page_at(index: u32) -> PyResult<PdfPageIndex> {
    PdfPageIndex::try_from(index).map_err(|_| value_err(format!("page index {index} is out of range")))
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

/// Resolves the page a bookmark points at, directly or through a GoTo action.
fn bookmark_page(bookmark: &PdfBookmark) -> i32 {
    if let Some(dest) = bookmark.destination() {
        if let Ok(idx) = dest.page_index() {
            return idx as i32;
        }
    }
    if let Some(action) = bookmark.action() {
        if let Some(local) = action.as_local_destination_action() {
            if let Ok(dest) = local.destination() {
                if let Ok(idx) = dest.page_index() {
                    return idx as i32;
                }
            }
        }
    }
    -1
}

/// Appends `first` and its following siblings (and their children) to `out`.
fn collect_outline(first: Option<PdfBookmark>, depth: u32, out: &mut Vec<OutlineEntry>) {
    let mut current = first;
    while let Some(bookmark) = current {
        if out.len() >= 20_000 || depth > 32 {
            return;
        }
        let title = bookmark.title().unwrap_or_default();
        out.push((depth, title.trim().to_string(), bookmark_page(&bookmark)));
        collect_outline(bookmark.first_child(), depth + 1, out);
        current = bookmark.next_sibling();
    }
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
/// The library is looked up in this order: the directory named by
/// `RIEMANN_PDFIUM_DIR`, the system library path, each of `extra_dirs` (the
/// folders around this extension module) and the running executable's folder,
/// each with and without `libs/`, then `./`, `libs/` and `../libs/` relative to
/// the working directory.
///
/// # Errors
/// Returns a `RuntimeError` listing the places searched when the library cannot
/// be loaded, instead of panicking: a panic reaches Python as
/// `PanicException`, which `except Exception` does not catch.
fn get_pdfium(extra_dirs: &[std::path::PathBuf]) -> PyResult<&'static Pdfium> {
    PDFIUM
        .get_or_try_init(|| {
            let env_dir = std::env::var_os("RIEMANN_PDFIUM_DIR").map(std::path::PathBuf::from);
            let mut search: Vec<std::path::PathBuf> = extra_dirs.to_vec();
            if let Some(exe_dir) = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.to_path_buf())) {
                search.push(exe_dir);
            }

            let mut tried = Vec::new();
            let mut try_dir = |dir: &std::path::Path| {
                let path = Pdfium::pdfium_platform_library_name_at_path(dir);
                tried.push(path.display().to_string());
                Pdfium::bind_to_library(&path).ok()
            };

            let bindings = env_dir
                .as_deref()
                .and_then(&mut try_dir)
                .or_else(|| Pdfium::bind_to_system_library().ok())
                .or_else(|| {
                    search
                        .iter()
                        .find_map(|d| try_dir(d).or_else(|| try_dir(&d.join("libs"))))
                })
                .or_else(|| ["./", "libs/", "../libs/"].iter().find_map(|d| try_dir(std::path::Path::new(d))));

            match bindings {
                Some(b) => Ok(PdfiumWrapper(Pdfium::new(b))),
                None => Err(runtime_err(format!(
                    "Could not load the PDFium library (searched the system library path and {})",
                    tried.join(", ")
                ))),
            }
        })
        .map(|w| &w.0)
}

/// Renders a page to a new bitmap at `scale`, with annotations, turning
/// landscape pages as the page asks.
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

/// Type definition for per-character geometry.
/// Tuple structure: `(character, loose_bounds_tuple)`.
type CharBox = (String, (f32, f32, f32, f32));

/// Type definition for one outline (bookmark) entry, flattened depth-first.
/// Tuple structure: `(depth, title, page_index)`, with -1 when the entry has no
/// destination inside the document.
type OutlineEntry = (u32, String, i32);

/// Type definition for link data.
/// Tuple structure: `(url, bounds_tuple)`.
type Link = (String, (f32, f32, f32, f32));

/// Type definition for a comment made in another application.
/// Tuple structure: `(subtype, contents, author, date, bounds_tuple)`.
type Comment = (String, String, String, String, (f32, f32, f32, f32));

/// Prefix of the `/NM` (annotation name) Riemann gives the annotations it
/// writes into PDF files. Riemann draws those itself, so they are hidden from
/// PDFium's renderer and left out of the comments made in other applications.
const RIEMANN_ANNOT_PREFIX: &str = "riemann:";

fn is_riemann_annotation(annotation: &PdfPageAnnotation) -> bool {
    annotation
        .name()
        .is_some_and(|name| name.starts_with(RIEMANN_ANNOT_PREFIX))
}

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
    /// The scale actually rendered at: the one requested, or less when that
    /// would exceed `MAX_RENDER_PIXELS`.
    #[pyo3(get)]
    scale: f32,
}

/// The most pixels one page render may have (64 MP, 256 MB). Large-format
/// pages (posters, maps, drawings) at high zoom on HiDPI screens would
/// otherwise need gigabytes: an A0 page at 10x is 3 GB, before Qt copies it.
/// Bigger renders are made at the largest scale that fits and stretched.
const MAX_RENDER_PIXELS: f64 = 64_000_000.0;

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
    /// Renders a page to BGRA pixels. `theme_mode` is 0 (light), 1 (inverted)
    /// or 2 (smart dark). The GIL is released while PDFium renders, so background
    /// render threads do not stall the UI thread. The page size comes from the
    /// document's page tree, so the page itself is only loaded once, for the render.
    fn render_page(
        &self,
        py: Python,
        page_index: u32,
        scale: f32,
        theme_mode: u8,
    ) -> PyResult<RenderResult> {
        let inner = &self.inner;

        let (page_w, page_h) = py.detach(|| -> PyResult<(f32, f32)> {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let size = doc_guard.0.pages().page_size(page_at(page_index)?).map_err(value_err)?;
            Ok((size.width().value, size.height().value))
        })?;
        let mut scale = scale;
        let pixels = (page_w * scale) as f64 * (page_h * scale) as f64;
        if pixels > MAX_RENDER_PIXELS {
            scale *= (MAX_RENDER_PIXELS / pixels).sqrt() as f32;
        }
        let (width, height) = ((page_w * scale) as i32, (page_h * scale) as i32);
        if width <= 0 || height <= 0 {
            return Err(value_err(format!("page {page_index} renders to an empty image")));
        }
        let len = width as usize * height as usize * 4;

        let data: Py<PyBytes> = unsafe {
            let ptr = pyo3::ffi::PyBytes_FromStringAndSize(
                std::ptr::null(),
                len as pyo3::ffi::Py_ssize_t,
            );
            if ptr.is_null() {
                return Err(PyErr::fetch(py));
            }
            Bound::from_owned_ptr(py, ptr).cast_into_unchecked::<PyBytes>().unbind()
        };
        let addr = unsafe { pyo3::ffi::PyBytes_AsString(data.as_ptr()) } as usize;

        py.detach(move || -> PyResult<()> {
            let buffer = unsafe { std::slice::from_raw_parts_mut(addr as *mut u8, len) };
            {
                let _guard = pdfium_guard();
                let doc_guard = lock_doc(inner);
                let mut page = doc_guard.0.pages().get(page_at(page_index)?).map_err(value_err)?;
                for mut annotation in page.annotations_mut().iter() {
                    if is_riemann_annotation(&annotation) && !annotation.is_hidden() {
                        let _ = annotation.set_is_hidden(true);
                    }
                }
                let mut bitmap = PdfBitmap::from_bytes(width, height, PdfBitmapFormat::BGRA, &mut buffer[..])
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
            scale,
        })
    }

    /// Returns how many digital signatures the document contains. Reading the
    /// count is cheap; validating them is left to the Python side.
    fn signature_count(&self, py: Python) -> usize {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            doc_guard.0.signatures().len() as usize
        })
    }

    /// The size of a page in PDF points, as `(width, height)`.
    fn get_page_size(&self, py: Python, page_index: u32) -> PyResult<(f32, f32)> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let rect = doc_guard.0.pages().page_size(page_at(page_index)?).map_err(value_err)?;
            Ok((rect.width().value, rect.height().value))
        })
    }

    /// The plain text of a page; empty when the page has none.
    fn get_page_text(&self, py: Python, page_index: u32) -> PyResult<String> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(String::new());
            }

            let page = pages.get(page_at(page_index)?).map_err(value_err)?;
            let text_accessor = page
                .text()
                .map_err(|e| runtime_err(format!("Text Access Error: {}", e)))?;

            if text_accessor.is_empty() {
                return Ok(String::new());
            }

            Ok(text_accessor.all())
        })
    }

    /// Reads one page with Tesseract. The page is rendered, its BGRA pixels are
    /// swapped to the RGBA order the OCR worker encodes, and the result is OCRed.
    /// A scale of 2.0 or more reads small text better. `languages` are Tesseract
    /// names joined by `+`.
    #[pyo3(signature = (page_index, scale, languages = "eng".to_string()))]
    fn ocr_page(&self, py: Python, page_index: u32, scale: f32, languages: String) -> PyResult<String> {
        let inner = &self.inner;
        py.detach(|| {
            let (mut buffer, width, height) = {
                let _guard = pdfium_guard();
                let doc_guard = lock_doc(inner);
                let page = doc_guard.0.pages().get(page_at(page_index)?).map_err(value_err)?;
                let bitmap = generate_bitmap(&page, scale)?;
                (
                    bitmap.as_raw_bytes(),
                    bitmap.width() as u32,
                    bitmap.height() as u32,
                )
            };
            buffer.chunks_exact_mut(4).for_each(|pixel| pixel.swap(0, 2));

            let engine = OcrEngine::new();
            engine
                .recognize_text_in(width, height, &buffer, &languages)
                .map_err(runtime_err)
        })
    }

    /// Reads several pages with one Tesseract process and returns their text
    /// in the order given.
    #[pyo3(signature = (page_indices, scale, languages = "eng".to_string()))]
    fn ocr_pages(
        &self,
        py: Python,
        page_indices: Vec<u32>,
        scale: f32,
        languages: String,
    ) -> PyResult<Vec<String>> {
        let inner = &self.inner;
        py.detach(|| {
            let mut pngs = Vec::with_capacity(page_indices.len());
            for &page_index in &page_indices {
                let (mut buffer, width, height) = {
                    let _guard = pdfium_guard();
                    let doc_guard = lock_doc(inner);
                    let page = doc_guard.0.pages().get(page_at(page_index)?).map_err(value_err)?;
                    let bitmap = generate_bitmap(&page, scale)?;
                    (
                        bitmap.as_raw_bytes(),
                        bitmap.width() as u32,
                        bitmap.height() as u32,
                    )
                };
                buffer.chunks_exact_mut(4).for_each(|pixel| pixel.swap(0, 2));
                pngs.push(
                    riemann_ocr_worker::encode_png(width, height, &buffer).map_err(runtime_err)?,
                );
            }
            OcrEngine::new()
                .recognize_pngs(&pngs, &languages)
                .map_err(runtime_err)
        })
    }

    /// Every occurrence of `term` on the page, as `(left, top, right, bottom)` boxes.
    fn search_page(
        &self,
        py: Python,
        page_index: u32,
        term: String,
    ) -> PyResult<Vec<(f32, f32, f32, f32)>> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_at(page_index)?).map_err(value_err)?;
            let text_accessor = page.text().map_err(runtime_err)?;
            let search_options = PdfSearchOptions::new();
            let search = text_accessor
                .search(&term, &search_options)
                .map_err(runtime_err)?;

            let frame = DisplayFrame::of(&page);
            let mut rects = Vec::new();
            for segments in search.iter(PdfSearchDirection::SearchForward) {
                for segment in segments.iter() {
                    rects.push(frame.rect(&segment.bounds()));
                }
            }

            Ok(rects)
        })
    }

    /// The page's text in segments, each with its bounding box.
    fn get_text_segments(&self, py: Python, page_index: u32) -> PyResult<Vec<TextSegment>> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_at(page_index)?).map_err(value_err)?;
            let text_accessor = page
                .text()
                .map_err(|e| runtime_err(format!("Text Access Error: {}", e)))?;

            let frame = DisplayFrame::of(&page);
            let mut segments = Vec::new();
            for segment in text_accessor.segments().iter() {
                segments.push((segment.text(), frame.rect(&segment.bounds())));
            }

            Ok(segments)
        })
    }

    /// Every character on a page with its loose bounding box (the full advance
    /// width and font height PDFium reports), in content order. Generated
    /// characters (synthesised spaces and line breaks) are included so callers
    /// see the same text as `get_page_text`.
    fn get_char_boxes(&self, py: Python, page_index: u32) -> PyResult<Vec<CharBox>> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_at(page_index)?).map_err(value_err)?;
            let text_accessor = page
                .text()
                .map_err(|e| runtime_err(format!("Text Access Error: {}", e)))?;

            let frame = DisplayFrame::of(&page);
            let mut boxes = Vec::new();
            for ch in text_accessor.chars().iter() {
                let Some(s) = ch.unicode_string() else { continue };
                let rect = match ch.loose_bounds() {
                    Ok(r) => r,
                    Err(_) => continue,
                };
                boxes.push((s, frame.rect(&rect)));
            }
            Ok(boxes)
        })
    }

    /// Returns the document outline (bookmarks) flattened depth-first as
    /// `(depth, title, page_index)` tuples. Empty when the file has none.
    fn get_outline(&self, py: Python) -> PyResult<Vec<OutlineEntry>> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let mut out = Vec::new();
            collect_outline(doc_guard.0.bookmarks().root(), 0, &mut out);
            Ok(out)
        })
    }

    /// The page's text fields, checkboxes and radio buttons, with their bounds,
    /// values and checked states.
    fn get_form_widgets(&self, py: Python, page_index: u32) -> PyResult<Vec<FormWidget>> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_at(page_index)?).map_err(value_err)?;
            let frame = DisplayFrame::of(&page);
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
                        frame.rect(&rect),
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
    /// Okular): every annotation with text contents except links, form widgets
    /// and popups.
    ///
    /// Returns `(page_box, comments)`: the page as displayed, `(0, 0, width,
    /// height)`, and a list of `(subtype, contents, author, date, (left, top,
    /// right, bottom))` with each box mapped through `DisplayFrame`, like every
    /// other box this module returns, so comments on rotated pages line up.
    /// Dates are the raw PDF date strings (`D:YYYYMMDDHHmmSS...`).
    fn get_comments(
        &self,
        py: Python,
        page_index: u32,
    ) -> PyResult<((f32, f32, f32, f32), Vec<Comment>)> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();
            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(((0.0, 0.0, 0.0, 0.0), Vec::new()));
            }
            let page = pages.get(page_at(page_index)?).map_err(value_err)?;

            let frame = DisplayFrame::of(&page);
            let crop = (0.0, 0.0, frame.width(), frame.height());

            let mut comments = Vec::new();
            for annotation in page.annotations().iter() {
                let kind = annotation.annotation_type();
                if matches!(
                    kind,
                    PdfPageAnnotationType::Link
                        | PdfPageAnnotationType::Widget
                        | PdfPageAnnotationType::XfaWidget
                        | PdfPageAnnotationType::Popup
                ) || is_riemann_annotation(&annotation)
                {
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
                    .map(|r| frame.rect(&r))
                    .unwrap_or((0.0, 0.0, 0.0, 0.0));
                comments.push((annotation_subtype_name(kind), contents, author, date, bounds));
            }
            Ok((crop, comments))
        })
    }

    /// The page's links as `(target, (left, top, right, bottom))`. Internal
    /// links (explicit or named destinations, and GoTo actions) are reported as
    /// `#page=N`, with `&top=Y` appended when the destination names a vertical
    /// position in PDF user space. External links carry their URI.
    fn get_links(&self, py: Python, page_index: u32) -> PyResult<Vec<Link>> {
        let inner = &self.inner;
        py.detach(|| {
            let _guard = pdfium_guard();
            let doc_guard = lock_doc(inner);
            let pages = doc_guard.0.pages();

            if (page_index as usize) >= (pages.len() as usize) {
                return Ok(Vec::new());
            }

            let page = pages.get(page_at(page_index)?).map_err(value_err)?;
            let frame = DisplayFrame::of(&page);
            let mut links = Vec::new();

            for link in page.links().iter() {
                let rect = match link.rect() {
                    Ok(rect) => rect,
                    Err(_) => continue,
                };
                if let Some(target) = link_target(&link) {
                    links.push((target, frame.rect(&rect)));
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
    fn new(py: Python) -> PyResult<Self> {
        let mut dirs = Vec::new();
        if let Ok(file) = py
            .import("riemann_core")
            .and_then(|m| m.getattr("__file__"))
            .and_then(|f| f.extract::<std::path::PathBuf>())
        {
            dirs.extend(file.ancestors().skip(1).take(6).map(|d| d.to_path_buf()));
        }
        let _guard = pdfium_guard();
        get_pdfium(&dirs)?;
        Ok(PdfEngine)
    }

    /// Loads a PDF. The GIL is released while PDFium parses the file, so loading
    /// a large document on a worker thread keeps the interface responsive.
    ///
    /// # Safety
    /// pdfium-render ties the document's lifetime to the password, but the
    /// password is only copied into a C string for the duration of
    /// `FPDF_LoadCustomDocument` and never referenced afterwards. The document
    /// is therefore extended to `'static` (as `Pdfium` itself is) instead of
    /// leaking every password, as earlier versions did.
    #[pyo3(signature = (path, password=None))]
    fn load_document(
        &self,
        py: Python,
        path: &str,
        password: Option<String>,
    ) -> PyResult<RiemannDocument> {
        if password.as_deref().is_some_and(|p| p.contains('\0')) {
            return Err(value_err("password must not contain NUL characters"));
        }
        let path = path.to_owned();
        let (wrapper, page_count) = py.detach(move || -> PyResult<(DocumentWrapper, usize)> {
            let _guard = pdfium_guard();
            let doc = get_pdfium(&[])?
                .load_pdf_from_file(&path, password.as_deref())
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyIOError, _>(e.to_string()))?;
            let doc: PdfDocument<'static> = unsafe { std::mem::transmute(doc) };
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
