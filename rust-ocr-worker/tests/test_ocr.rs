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

#[test]
fn language_lists_are_validated() {
    use riemann_ocr_worker::valid_languages;
    assert!(valid_languages("eng"));
    assert!(valid_languages("eng+deu+chi_sim"));
    assert!(!valid_languages(""));
    assert!(!valid_languages("eng+"));
    assert!(!valid_languages("--psm"));
    assert!(!valid_languages("eng deu"));
}

#[test]
fn invalid_languages_are_rejected_before_tesseract_runs() {
    let engine = riemann_ocr_worker::OcrEngine::new();
    let err = engine
        .recognize_text_in(1, 1, &[255, 255, 255, 255], "-l eng")
        .unwrap_err();
    assert!(err.to_string().contains("Invalid OCR language"));
}

#[test]
fn split_pages_matches_tesseract_list_output() {
    use riemann_ocr_worker::split_pages;
    assert_eq!(
        split_pages("one\x0c\x0cthree", 3).unwrap(),
        vec!["one", "", "three"]
    );
    assert_eq!(
        split_pages("one\x0ctwo\x0c", 2).unwrap(),
        vec!["one", "two"]
    );
    assert!(split_pages("one\x0ctwo", 3).is_err());
}

#[test]
fn recognize_pngs_reads_each_page_with_one_process() {
    if std::process::Command::new("tesseract")
        .arg("--version")
        .output()
        .is_err()
    {
        return;
    }
    let engine = OcrEngine::new();
    let blank = riemann_ocr_worker::encode_png(64, 64, &vec![255u8; 64 * 64 * 4]).unwrap();
    let pages = engine
        .recognize_pngs(&[blank.clone(), blank], "eng")
        .unwrap();
    assert_eq!(pages.len(), 2);
    assert!(pages.iter().all(|p| p.trim().is_empty()));
    assert!(engine.recognize_pngs(&[], "eng").unwrap().is_empty());
    assert!(engine.recognize_pngs(&[vec![1, 2, 3]], "--psm").is_err());
}
