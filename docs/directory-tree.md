```
riemann/
├── .editorconfig
├── .github/
│   └── workflows/
│       └── release.yml
├── .gitignore
├── Cargo.lock
├── Cargo.toml
├── LICENSE
├── Riemann.spec
├── build_entry.py
├── docs/
│   ├── DevGuide.md
│   ├── README.md
│   └── directory-tree.md
├── justfile
├── libs/
│   └── libpdfium.so
├── logs/
│   └── test_results.log
├── package-lock.json
├── package.json
├── pyproject.toml
├── python-app/
│   ├── riemann/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── app.py
│   │   ├── assets/
│   │   │   ├── Icon.png
│   │   │   ├── __tests__/
│   │   │   │   ├── audio_engine.test.js
│   │   │   │   └── homepage.test.js
│   │   │   ├── audio_engine.js
│   │   │   ├── browser.png
│   │   │   ├── fonts/
│   │   │   │   └── NotoColorEmoji.ttf
│   │   │   ├── homepage.css
│   │   │   ├── homepage.html
│   │   │   ├── homepage.js
│   │   │   ├── icon.ico
│   │   │   ├── icons/
│   │   │   │   ├── book-open-white.svg
│   │   │   │   ├── book-open.svg
│   │   │   │   ├── bookmark-filled-white.svg
│   │   │   │   ├── bookmark-filled.svg
│   │   │   │   ├── bookmark-white.svg
│   │   │   │   ├── bookmark.svg
│   │   │   │   ├── browser-white.svg
│   │   │   │   ├── browser.png
│   │   │   │   ├── browser.svg
│   │   │   │   ├── check-white.svg
│   │   │   │   ├── check.svg
│   │   │   │   ├── chevron-down-white.svg
│   │   │   │   ├── chevron-down.svg
│   │   │   │   ├── chevron-left-white.svg
│   │   │   │   ├── chevron-left.svg
│   │   │   │   ├── chevron-right-white.svg
│   │   │   │   ├── chevron-right.svg
│   │   │   │   ├── chevron-up-white.svg
│   │   │   │   ├── chevron-up.svg
│   │   │   │   ├── circle-arrow-left-white.svg
│   │   │   │   ├── circle-arrow-left.svg
│   │   │   │   ├── circle-arrow-right-white.svg
│   │   │   │   ├── circle-arrow-right.svg
│   │   │   │   ├── circle-check-white.svg
│   │   │   │   ├── circle-check.svg
│   │   │   │   ├── circle-question-mark-white.svg
│   │   │   │   ├── circle-question-mark.svg
│   │   │   │   ├── circle-slash-white.svg
│   │   │   │   ├── circle-slash.svg
│   │   │   │   ├── circle-stop-white.svg
│   │   │   │   ├── circle-stop.svg
│   │   │   │   ├── crop-white.svg
│   │   │   │   ├── crop.svg
│   │   │   │   ├── cursor-white.svg
│   │   │   │   ├── cursor.svg
│   │   │   │   ├── download-white.svg
│   │   │   │   ├── download.svg
│   │   │   │   ├── eraser-white.svg
│   │   │   │   ├── eraser.svg
│   │   │   │   ├── file-lock-white.svg
│   │   │   │   ├── file-lock.svg
│   │   │   │   ├── file-output-white.svg
│   │   │   │   ├── file-output.svg
│   │   │   │   ├── file-text-white.svg
│   │   │   │   ├── file-text.svg
│   │   │   │   ├── highlighter-white.svg
│   │   │   │   ├── highlighter.svg
│   │   │   │   ├── incognito-white.svg
│   │   │   │   ├── incognito.svg
│   │   │   │   ├── maximize-white.svg
│   │   │   │   ├── maximize.svg
│   │   │   │   ├── moon-white.svg
│   │   │   │   ├── moon.svg
│   │   │   │   ├── music-white.svg
│   │   │   │   ├── music.svg
│   │   │   │   ├── palette-white.svg
│   │   │   │   ├── palette.svg
│   │   │   │   ├── pdf-white.svg
│   │   │   │   ├── pdf.png
│   │   │   │   ├── pdf.svg
│   │   │   │   ├── pen-line-white.svg
│   │   │   │   ├── pen-line.svg
│   │   │   │   ├── printer-white.svg
│   │   │   │   ├── printer.svg
│   │   │   │   ├── redo-white.svg
│   │   │   │   ├── redo.svg
│   │   │   │   ├── rename-white.svg
│   │   │   │   ├── rename.svg
│   │   │   │   ├── rotate-ccw-white.svg
│   │   │   │   ├── rotate-ccw.svg
│   │   │   │   ├── rotate-cw-white.svg
│   │   │   │   ├── rotate-cw.svg
│   │   │   │   ├── save-white.svg
│   │   │   │   ├── save.svg
│   │   │   │   ├── scan-text-white.svg
│   │   │   │   ├── scan-text.svg
│   │   │   │   ├── scroll-white.svg
│   │   │   │   ├── scroll.svg
│   │   │   │   ├── search-white.svg
│   │   │   │   ├── search.svg
│   │   │   │   ├── sparkles-white.svg
│   │   │   │   ├── sparkles.svg
│   │   │   │   ├── square-dashed-white.svg
│   │   │   │   ├── square-dashed.svg
│   │   │   │   ├── sticky-note-white.svg
│   │   │   │   ├── sticky-note.svg
│   │   │   │   ├── strikethrough-white.svg
│   │   │   │   ├── strikethrough.svg
│   │   │   │   ├── sun-moon-white.svg
│   │   │   │   ├── sun-moon.svg
│   │   │   │   ├── sun-white.svg
│   │   │   │   ├── sun.svg
│   │   │   │   ├── text-quote-white.svg
│   │   │   │   ├── text-quote.svg
│   │   │   │   ├── type-white.svg
│   │   │   │   ├── type.svg
│   │   │   │   ├── underline-white.svg
│   │   │   │   ├── underline.svg
│   │   │   │   ├── undo-white.svg
│   │   │   │   ├── undo.svg
│   │   │   │   ├── volume-on-white.svg
│   │   │   │   ├── volume-on.svg
│   │   │   │   ├── volume-x-white.svg
│   │   │   │   ├── volume-x.svg
│   │   │   │   ├── x-white.svg
│   │   │   │   └── x.svg
│   │   │   ├── injections/
│   │   │   │   ├── ad_skipper.js
│   │   │   │   ├── backspace_handler.js
│   │   │   │   ├── emoji_fallback.js
│   │   │   │   └── smart_dark_mode.js
│   │   │   └── theme/
│   │   │       ├── modern_dark.css
│   │   │       └── modern_light.css
│   │   ├── core/
│   │   │   ├── constants.py
│   │   │   └── managers.py
│   │   ├── riemann_core.pyi
│   │   └── ui/
│   │       ├── browser.py
│   │       ├── browser_handlers.py
│   │       ├── components.py
│   │       └── reader/
│   │           ├── __init__.py
│   │           ├── mixins/
│   │           │   ├── ai.py
│   │           │   ├── annotations.py
│   │           │   ├── metadata.py
│   │           │   ├── rendering.py
│   │           │   ├── search.py
│   │           │   └── signatures.py
│   │           ├── tab.py
│   │           ├── utils.py
│   │           ├── widgets.py
│   │           └── workers.py
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
│   └── requirements.txt
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
    ├── nbuild.sh
    └── test_runner.sh
```
