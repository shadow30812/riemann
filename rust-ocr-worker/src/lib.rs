use anyhow::{Context, Result};
use image::{ImageBuffer, Rgba};
use std::io::Write;
use std::process::{Command, Stdio};

pub struct OcrEngine;

impl OcrEngine {
    pub fn new() -> Self {
        OcrEngine
    }

    /// Takes raw RGBA pixels (from PDFium), encodes to PNG, and pipes to Tesseract
    pub fn recognize_text(&self, width: u32, height: u32, data: &[u8]) -> Result<String> {
        // 1. Create image buffer
        let buffer: ImageBuffer<Rgba<u8>, _> = ImageBuffer::from_raw(width, height, data)
            .context("Failed to create image buffer for OCR")?;

        // 2. Encode to PNG in memory to send to Tesseract
        let mut png_data = Vec::new();
        let encoder = image::codecs::png::PngEncoder::new(&mut png_data);
        buffer
            .write_with_encoder(encoder)
            .context("Failed to encode PNG for OCR")?;

        // 3. Spawn Tesseract process (stdin -> stdout)
        let mut child = Command::new("tesseract")
            .arg("stdin")
            .arg("stdout")
            .arg("-l")
            .arg("eng")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .context("Tesseract process failed to start. Ensure 'tesseract-ocr' is installed.")?;

        // 4. Pipe PNG to stdin
        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(&png_data)
                .context("Failed to pipe image to Tesseract")?;
        }

        // 5. Capture results
        let output = child
            .wait_with_output()
            .context("Failed to wait for Tesseract")?;

        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!("Tesseract Error: {}", err);
        }

        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}
