```
riemann/
├── .editorconfig
├── .gitignore
├── Cargo.lock
├── Cargo.toml
├── LICENSE
├── Riemann.spec
├── build_entry.py
├── justfile
├── package-lock.json
├── package.json
├── pyproject.toml
├── .github/
│   └── workflows/
│       └── release.yml
├── docs/
│   ├── DevGuide.md
│   ├── README.md
│   └── directory-tree.md
├── libs/
│   ├── libpdfium.so
│   └── pdfium.dll
├── logs/
│   └── test_results.log
├── python-app/
│   ├── riemann/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── app.py
│   │   ├── riemann_core.pyi
│   │   ├── assets/
│   │   │   ├── audio_engine.js
│   │   │   ├── video_engine.js
│   │   │   ├── browser.png
│   │   │   ├── homepage.css
│   │   │   ├── homepage.html
│   │   │   ├── homepage.js
│   │   │   ├── Icon.png
│   │   │   ├── icon.ico
│   │   │   ├── __tests__/
│   │   │   │   ├── audio_engine.test.js
│   │   │   │   └── homepage.test.js
│   │   │   ├── fonts/
│   │   │   │   └── NotoColorEmoji.ttf
│   │   │   ├── icons/
│   │   │   │   ├── airplay.svg
│   │   │   │   ├── airplay-white.svg
│   │   │   │   ├── book-open.svg
│   │   │   │   ├── book-open-white.svg
│   │   │   │   ├── bookmark.svg
│   │   │   │   ├── bookmark-white.svg
│   │   │   │   ├── bookmark-filled.svg
│   │   │   │   ├── bookmark-filled-white.svg
│   │   │   │   ├── browser.png
│   │   │   │   ├── browser.svg
│   │   │   │   ├── browser-white.svg
│   │   │   │   ├── check.svg
│   │   │   │   ├── check-white.svg
│   │   │   │   ├── chevron-down.svg
│   │   │   │   ├── chevron-down-white.svg
│   │   │   │   ├── chevron-left.svg
│   │   │   │   ├── chevron-left-white.svg
│   │   │   │   ├── chevron-right.svg
│   │   │   │   ├── chevron-right-white.svg
│   │   │   │   ├── chevron-up.svg
│   │   │   │   ├── chevron-up-white.svg
│   │   │   │   ├── circle-arrow-left.svg
│   │   │   │   ├── circle-arrow-left-white.svg
│   │   │   │   ├── circle-arrow-right.svg
│   │   │   │   ├── circle-arrow-right-white.svg
│   │   │   │   ├── circle-check.svg
│   │   │   │   ├── circle-check-white.svg
│   │   │   │   ├── circle-question-mark.svg
│   │   │   │   ├── circle-question-mark-white.svg
│   │   │   │   ├── circle-slash.svg
│   │   │   │   ├── circle-slash-white.svg
│   │   │   │   ├── circle-stop.svg
│   │   │   │   ├── circle-stop-white.svg
│   │   │   │   ├── crop.svg
│   │   │   │   ├── crop-white.svg
│   │   │   │   ├── cursor.svg
│   │   │   │   ├── cursor-white.svg
│   │   │   │   ├── download.svg
│   │   │   │   ├── download-white.svg
│   │   │   │   ├── eraser.svg
│   │   │   │   ├── eraser-white.svg
│   │   │   │   ├── file-lock.svg
│   │   │   │   ├── file-lock-white.svg
│   │   │   │   ├── file-output.svg
│   │   │   │   ├── file-output-white.svg
│   │   │   │   ├── file-text.svg
│   │   │   │   ├── file-text-white.svg
│   │   │   │   ├── gauge.svg
│   │   │   │   ├── gauge-white.svg
│   │   │   │   ├── highlighter.svg
│   │   │   │   ├── highlighter-white.svg
│   │   │   │   ├── Icon.ico
│   │   │   │   ├── Icon.png
│   │   │   │   ├── incognito.svg
│   │   │   │   ├── incognito-white.svg
│   │   │   │   ├── maximize.svg
│   │   │   │   ├── maximize-white.svg
│   │   │   │   ├── moon.svg
│   │   │   │   ├── moon-white.svg
│   │   │   │   ├── music.svg
│   │   │   │   ├── music-white.svg
│   │   │   │   ├── palette.svg
│   │   │   │   ├── palette-white.svg
│   │   │   │   ├── pdf.png
│   │   │   │   ├── pdf.svg
│   │   │   │   ├── pdf-white.svg
│   │   │   │   ├── pen-line.svg
│   │   │   │   ├── pen-line-white.svg
│   │   │   │   ├── printer.svg
│   │   │   │   ├── printer-white.svg
│   │   │   │   ├── redo.svg
│   │   │   │   ├── redo-white.svg
│   │   │   │   ├── rename.svg
│   │   │   │   ├── rename-white.svg
│   │   │   │   ├── rotate-ccw.svg
│   │   │   │   ├── rotate-ccw-white.svg
│   │   │   │   ├── rotate-cw.svg
│   │   │   │   ├── rotate-cw-white.svg
│   │   │   │   ├── save.svg
│   │   │   │   ├── save-white.svg
│   │   │   │   ├── scan-text.svg
│   │   │   │   ├── scan-text-white.svg
│   │   │   │   ├── scroll.svg
│   │   │   │   ├── scroll-white.svg
│   │   │   │   ├── search.svg
│   │   │   │   ├── search-white.svg
│   │   │   │   ├── sparkles.svg
│   │   │   │   ├── sparkles-white.svg
│   │   │   │   ├── square-dashed.svg
│   │   │   │   ├── square-dashed-white.svg
│   │   │   │   ├── sticky-note.svg
│   │   │   │   ├── sticky-note-white.svg
│   │   │   │   ├── strikethrough.svg
│   │   │   │   ├── strikethrough-white.svg
│   │   │   │   ├── sun.svg
│   │   │   │   ├── sun-white.svg
│   │   │   │   ├── sun-moon.svg
│   │   │   │   ├── sun-moon-white.svg
│   │   │   │   ├── text-quote.svg
│   │   │   │   ├── text-quote-white.svg
│   │   │   │   ├── type.svg
│   │   │   │   ├── type-white.svg
│   │   │   │   ├── underline.svg
│   │   │   │   ├── underline-white.svg
│   │   │   │   ├── undo.svg
│   │   │   │   ├── undo-white.svg
│   │   │   │   ├── volume-on.svg
│   │   │   │   ├── volume-on-white.svg
│   │   │   │   ├── volume-x.svg
│   │   │   │   ├── volume-x-white.svg
│   │   │   │   ├── x.svg
│   │   │   │   └── x-white.svg
│   │   │   ├── injections/
│   │   │   │   ├── ad_skipper.js
│   │   │   │   ├── backspace_handler.js
│   │   │   │   ├── emoji_fallback.js
│   │   │   │   └── smart_dark_mode.js
│   │   │   └── theme/
│   │   │       ├── modern_dark.css
│   │   │       ├── modern_light.css
│   │   │       ├── x.svg
│   │   │       └── x-white.svg
│   │   ├── core/
│   │   │   ├── constants.py
│   │   │   └── managers.py
│   │   └── ui/
│   │       ├── browser.py
│   │       ├── browser_handlers.py
│   │       ├── components.py
│   │       └── reader/
│   │           ├── __init__.py
│   │           ├── tab.py
│   │           ├── utils.py
│   │           ├── widgets.py
│   │           ├── workers.py
│   │           └── mixins/
│   │               ├── ai.py
│   │               ├── annotations.py
│   │               ├── metadata.py
│   │               ├── rendering.py
│   │               ├── search.py
│   │               └── signatures.py
│   └── tests/
│       ├── test_ai.py
│       ├── test_annotations.py
│       ├── test_app.py
│       ├── test_browser.py
│       ├── test_browser_handlers.py
│       ├── test_components.py
│       ├── test_constants.py
│       ├── test_managers.py
│       ├── test_metadata.py
│       ├── test_rendering.py
│       ├── test_search.py
│       ├── test_signatures.py
│       ├── test_tab.py
│       ├── test_utils.py
│       ├── test_widgets.py
│       └── test_workers.py
├── requirements/
│   ├── requirements.in
│   ├── requirements.sh
│   ├── requirements.txt
│   ├── wreqs.txt
│   └── xreqs.txt
├── riemann-ai/
│   ├── build_ai.sh
│   ├── main.py
│   ├── requirements.txt
│   └── tests/
│       └── test_main.py
├── rust-core/
│   ├── Cargo.lock
│   ├── Cargo.toml
│   ├── src/
│   │   └── lib.rs
│   └── tests/
│       └── test_core.rs
├── rust-ocr-worker/
│   ├── Cargo.lock
│   ├── Cargo.toml
│   ├── src/
│   │   └── lib.rs
│   └── tests/
│       └── test_ocr.rs
└── scripts/
    ├── build.sh
    ├── create_model_pack.sh
    ├── generate_white_icons.py
    ├── install_icon.sh
    ├── nbuild.ps1
    ├── nbuild.sh
    ├── replace_exec.sh
    └── test_runner.sh
```
