//! # Riemann OCR Worker
//!
//! A specialized worker module for performing Optical Character Recognition (OCR).
//! It interfaces with the `tesseract` command-line utility to extract text from
//! raw image data.

use anyhow::{Context, Result};
use image::{ImageBuffer, Rgba};
use std::io::Write;
use std::process::{Command, Stdio};

/// A worker struct for handling Optical Character Recognition (OCR) tasks.
///
/// Wraps the external `tesseract` binary process to perform text extraction
/// on raw image data.
pub struct OcrEngine;

impl Default for OcrEngine {
    fn default() -> Self {
        Self::new()
    }
}

impl OcrEngine {
    /// Creates a new instance of the OCR engine.
    ///
    /// This is a lightweight operation as the heavy lifting is done by the
    /// spawned processes during `recognize_text`.
    pub fn new() -> Self {
        OcrEngine
    }

    /// Recognizes text from raw pixel data.
    ///
    /// This function performs the following pipeline:
    /// 1. Wraps raw RGBA bytes into an image buffer.
    /// 2. Encodes the buffer to PNG format in memory.
    /// 3. Pipes the PNG data to a spawned `tesseract` process via stdin.
    /// 4. Captures and returns the text output from stdout.
    ///
    /// # Arguments
    /// * `width` - Image width in pixels.
    /// * `height` - Image height in pixels.
    /// * `data` - Raw slice of RGBA pixel data.
    ///
    /// # Returns
    /// A `Result` containing the extracted String or an error if the process fails.
    /// Errors may occur if `tesseract` is missing from the PATH, if the image encoding
    /// fails, or if the process exits with a non-zero status.
    pub fn recognize_text(&self, width: u32, height: u32, data: &[u8]) -> Result<String> {
        let buffer: ImageBuffer<Rgba<u8>, _> = ImageBuffer::from_raw(width, height, data)
            .context("Failed to create image buffer from raw pixel data")?;

        let mut png_data = Vec::new();
        let encoder = image::codecs::png::PngEncoder::new(&mut png_data);
        buffer
            .write_with_encoder(encoder)
            .context("Failed to encode in-memory PNG for OCR processing")?;

        let mut child = Command::new("tesseract")
            .arg("stdin")
            .arg("stdout")
            .arg("-l")
            .arg("eng")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .context("Tesseract process failed to start. Please ensure 'tesseract-ocr' is installed and in your PATH.")?;

        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(&png_data)
                .context("Failed to pipe PNG data to Tesseract stdin")?;
        }

        let output = child
            .wait_with_output()
            .context("Failed to wait for Tesseract process execution")?;

        if !output.status.success() {
            let err_msg = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("Tesseract execution failed with error: {}", err_msg);
        }

        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}
