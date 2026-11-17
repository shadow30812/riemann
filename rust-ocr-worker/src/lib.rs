//! # Riemann OCR Worker
//!
//! A specialized worker module for performing Optical Character Recognition (OCR).
//! It interfaces with the `tesseract` command-line utility to extract text from
//! raw image data.
//!
//! The language list is passed to `tesseract -l` (for example `eng+deu`). Only
//! names made of letters, digits, `_` and `+` are accepted, so the value can
//! never become another command-line option.
//!
//! `recognize_pngs` reads many pages with one Tesseract process: the images
//! go to a private temporary folder with a list file naming them, and
//! Tesseract (5.x) writes each page's text followed by a form feed, with no
//! form feed after the last page. A blank page gives an empty string. If the
//! number of pages read back does not match, it is an error rather than text
//! assigned to the wrong page.
//!
//! `recognize_text_in` feeds the image to Tesseract's stdin from a thread
//! while the caller reads its stdout and stderr: writing it all first could
//! block for ever once Tesseract, still reading, filled its stderr pipe.

use anyhow::{Context, Result};
use image::{ImageBuffer, Rgba};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

static BATCH: AtomicU64 = AtomicU64::new(0);

/// `CREATE_NO_WINDOW`: no console window for Tesseract in the windowed Windows build.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// A `tesseract` command that opens no console window on Windows.
fn tesseract() -> Command {
    #[allow(unused_mut)]
    let mut command = Command::new("tesseract");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

/// Encodes raw RGBA pixels as a PNG.
pub fn encode_png(width: u32, height: u32, data: &[u8]) -> Result<Vec<u8>> {
    let buffer: ImageBuffer<Rgba<u8>, _> = ImageBuffer::from_raw(width, height, data)
        .context("Failed to create image buffer from raw pixel data")?;
    let mut png_data = Vec::new();
    let encoder = image::codecs::png::PngEncoder::new(&mut png_data);
    buffer
        .write_with_encoder(encoder)
        .context("Failed to encode in-memory PNG for OCR processing")?;
    Ok(png_data)
}

/// Removes the batch folder when dropped, whatever happened in between.
struct BatchDir(PathBuf);

impl BatchDir {
    fn new() -> Result<Self> {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let name = format!(
            "riemann-ocr-{}-{}-{}",
            std::process::id(),
            nanos,
            BATCH.fetch_add(1, Ordering::Relaxed)
        );
        let dir = std::env::temp_dir().join(name);
        std::fs::create_dir(&dir).context("Failed to create a temporary folder for OCR")?;
        Ok(BatchDir(dir))
    }
}

impl Drop for BatchDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Splits Tesseract's text output into `pages` pages.
pub fn split_pages(output: &str, pages: usize) -> Result<Vec<String>> {
    let mut parts: Vec<String> = output.split('\x0c').map(str::to_string).collect();
    if parts.len() == pages + 1 && parts.last().is_some_and(|p| p.trim().is_empty()) {
        parts.pop();
    }
    if parts.len() != pages {
        anyhow::bail!("Tesseract returned {} pages for {} images", parts.len(), pages);
    }
    Ok(parts)
}

/// Runs the external `tesseract` program.
pub struct OcrEngine;

impl Default for OcrEngine {
    fn default() -> Self {
        Self::new()
    }
}

impl OcrEngine {
    pub fn new() -> Self {
        OcrEngine
    }

    /// Reads English text from raw RGBA pixels: they are encoded as PNG and
    /// piped to `tesseract`. Fails if Tesseract is missing from the PATH or exits
    /// with an error.
    pub fn recognize_text(&self, width: u32, height: u32, data: &[u8]) -> Result<String> {
        self.recognize_text_in(width, height, data, "eng")
    }

    /// Like `recognize_text`, in the given Tesseract languages (`eng+deu`).
    pub fn recognize_text_in(&self, width: u32, height: u32, data: &[u8], languages: &str) -> Result<String> {
        if !valid_languages(languages) {
            anyhow::bail!("Invalid OCR language list: {languages:?}");
        }
        let png_data = encode_png(width, height, data)?;

        let mut child = tesseract()
            .arg("stdin")
            .arg("stdout")
            .arg("-l")
            .arg(languages)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .context("Tesseract process failed to start. Please ensure 'tesseract-ocr' is installed and in your PATH.")?;

        let writer = child.stdin.take().map(|mut stdin| {
            std::thread::spawn(move || stdin.write_all(&png_data))
        });

        let output = child
            .wait_with_output()
            .context("Failed to wait for Tesseract process execution")?;
        if let Some(writer) = writer {
            writer
                .join()
                .map_err(|_| anyhow::anyhow!("The thread feeding Tesseract panicked"))?
                .context("Failed to pipe PNG data to Tesseract stdin")?;
        }

        if !output.status.success() {
            let err_msg = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("Tesseract execution failed with error: {}", err_msg);
        }

        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }

    /// Recognizes several PNG-encoded pages with one Tesseract process and
    /// returns one string per page, in order.
    pub fn recognize_pngs(&self, pngs: &[Vec<u8>], languages: &str) -> Result<Vec<String>> {
        if !valid_languages(languages) {
            anyhow::bail!("Invalid OCR language list: {languages:?}");
        }
        if pngs.is_empty() {
            return Ok(Vec::new());
        }
        let dir = BatchDir::new()?;
        let mut list = String::new();
        for (i, png) in pngs.iter().enumerate() {
            let path = dir.0.join(format!("page-{i:05}.png"));
            std::fs::write(&path, png).context("Failed to write a page image for OCR")?;
            list.push_str(&path.to_string_lossy());
            list.push('\n');
        }
        let list_path = dir.0.join("pages.txt");
        std::fs::write(&list_path, list).context("Failed to write the OCR page list")?;

        let output = tesseract()
            .arg(&list_path)
            .arg("stdout")
            .arg("-l")
            .arg(languages)
            .stdin(Stdio::null())
            .output()
            .context("Tesseract process failed to start. Please ensure 'tesseract-ocr' is installed and in your PATH.")?;
        if !output.status.success() {
            let err_msg = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("Tesseract execution failed with error: {}", err_msg);
        }
        split_pages(&String::from_utf8_lossy(&output.stdout), pngs.len())
    }
}

/// Whether `languages` is a `+`-separated list of Tesseract language names.
pub fn valid_languages(languages: &str) -> bool {
    !languages.is_empty()
        && languages.len() <= 200
        && languages
            .split('+')
            .all(|l| !l.is_empty() && l.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
}
