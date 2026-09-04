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
│   │   ├── script.py
│   │   ├── riemann_core.pyi
│   │   ├── riemann_core.pyd
│   │   ├── riemann_core.abi3.so
│   │   ├── assets/
│   │   │   ├── ai_engine_debug.log
│   │   │   ├── audio_engine.js
│   │   │   ├── caption_engine.js
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
│   │   │   │   └── (Comprehensive suite of UI SVG and PNG assets)
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
│   │   │   ├── captions.py
│   │   │   ├── captions_server.py
│   │   │   ├── constants.py
│   │   │   ├── dependencies.py
│   │   │   ├── features.py
│   │   │   ├── links.py
│   │   │   ├── managers.py
│   │   │   └── mini_player.py
│   │   ├── ui/
│   │   │   ├── browser.py
│   │   │   ├── browser_handlers.py
│   │   │   ├── components.py
│   │   │   ├── explorer.py
│   │   │   ├── favourites.py
│   │   │   └── reader/
│   │   │       ├── __init__.py
│   │   │       ├── tab.py
│   │   │       ├── utils.py
│   │   │       ├── widgets.py
│   │   │       ├── workers.py
│   │   │       └── mixins/
│   │   │           ├── ai.py
│   │   │           ├── annotations.py
│   │   │           ├── metadata.py
│   │   │           ├── rendering.py
│   │   │           ├── search.py
│   │   │           └── signatures.py
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
