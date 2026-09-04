use riemann_ocr_worker::OcrEngine;

#[test]
fn test_engine_default() {
    let _engine = OcrEngine;
}

#[test]
fn test_engine_new() {
    let _engine = OcrEngine::new();
}

#[test]
fn test_recognize_text_empty() {
    let engine = OcrEngine::new();
    let result = engine.recognize_text(0, 0, &[]);
    assert!(result.is_err());
}

#[test]
fn test_recognize_text_invalid_dimensions() {
    let engine = OcrEngine::new();
    let fake_data = vec![255, 255, 255, 255, 0, 0, 0, 255];
    let result = engine.recognize_text(9999, 9999, &fake_data);
    assert!(result.is_err());
}
