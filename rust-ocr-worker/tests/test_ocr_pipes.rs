//! A Tesseract that writes a lot to stderr before it reads its input must not
//! hang the worker. This test puts a fake `tesseract` first on PATH, so it
//! lives in a file of its own: integration test files run as separate
//! processes, and the PATH change cannot reach the other tests.

#![cfg(unix)]

use riemann_ocr_worker::OcrEngine;
use std::os::unix::fs::PermissionsExt;
use std::sync::mpsc;
use std::time::Duration;

#[test]
fn chatty_tesseract_does_not_deadlock() {
    let dir = std::env::temp_dir().join(format!("riemann-ocr-pipes-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let fake = dir.join("tesseract");
    std::fs::write(
        &fake,
        "#!/bin/sh\nhead -c 300000 /dev/zero | tr '\\000' x >&2\ncat > /dev/null\necho recognised\n",
    )
    .unwrap();
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
    let path = format!("{}:{}", dir.display(), std::env::var("PATH").unwrap_or_default());
    unsafe { std::env::set_var("PATH", path) };

    let (width, height) = (600u32, 600u32);
    let mut seed = 12345u32;
    let pixels: Vec<u8> = (0..width * height * 4)
        .map(|_| {
            seed = seed.wrapping_mul(1_103_515_245).wrapping_add(12_345);
            (seed >> 16) as u8
        })
        .collect();
    let (done, result) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = done.send(OcrEngine::new().recognize_text_in(width, height, &pixels, "eng"));
    });
    let text = result
        .recv_timeout(Duration::from_secs(30))
        .expect("the worker hung on Tesseract's pipes")
        .unwrap();
    assert_eq!(text.trim(), "recognised");
    let _ = std::fs::remove_dir_all(&dir);
}
