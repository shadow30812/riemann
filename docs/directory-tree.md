The repository's files, as tracked by git plus new files not yet committed. Build output, the virtual environment and the benchmark corpus are left out.

```
riemann/
├── AGENTS.md
├── build_entry.py
├── Cargo.lock
├── Cargo.toml
├── .editorconfig
├── .gitignore
├── justfile
├── LICENSE
├── package-lock.json
├── package.json
├── pyproject.toml
├── Riemann.spec
├── benchmarks/
│   ├── _common.py
│   ├── bench_core.py
│   ├── bench_memory.py
│   ├── bench_scroll.py
│   ├── bench_startup.py
│   ├── fetch_corpus.py
│   ├── README.md
│   └── results/
│       └── (JSON results per benchmark and label, coverage.json)
├── docs/
│   ├── DevGuide.md
│   ├── directory-tree.md
│   ├── README.md
│   └── images/
│       ├── collapsed-tabs.png
│       ├── office.png
│       ├── preview-markdown.png
│       ├── split-view.png
│       ├── table.png
│       └── workspace.png
├── .github/
│   └── workflows/
│       ├── release.yml
│       └── tests.yml
├── libs/
│   ├── libatomic.a
│   └── (libpdfium.so and pdfium.dll, downloaded by just fetch-pdfium, not in git)
├── python-app/
│   ├── riemann/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── app.py
│   │   ├── riemann_core.pyi
│   │   ├── assets/
│   │   │   ├── audio_engine.js
│   │   │   ├── caption_engine.js
│   │   │   ├── video_engine.js
│   │   │   ├── __tests__/
│   │   │   │   ├── audio_engine.test.js
│   │   │   │   └── homepage.test.js
│   │   │   ├── fonts/
│   │   │   │   ├── Inter-OFL.txt
│   │   │   │   ├── Inter-Variable.ttf
│   │   │   │   └── NotoColorEmoji.ttf
│   │   │   ├── homepage/
│   │   │   │   ├── browser.png
│   │   │   │   ├── homepage.css
│   │   │   │   ├── homepage.html
│   │   │   │   └── homepage.js
│   │   │   ├── icons/
│   │   │   │   └── (UI icons: SVG and PNG, each SVG with a -white variant for dark themes)
│   │   │   ├── injections/
│   │   │   │   ├── ad_skipper.js
│   │   │   │   ├── backspace_handler.js
│   │   │   │   ├── emoji_fallback.js
│   │   │   │   └── smart_dark_mode.js
│   │   │   ├── requirements/
│   │   │   │   └── (hashed lock files for the optional dependency groups)
│   │   │   └── theme/
│   │   │       ├── modern_dark.css
│   │   │       ├── modern_light.css
│   │   │       ├── x-white.svg
│   │   │       └── x.svg
│   │   ├── core/
│   │   │   ├── ai_engine.py
│   │   │   ├── ai_server.py
│   │   │   ├── annotation_store.py
│   │   │   ├── assets.py
│   │   │   ├── bibliography.py
│   │   │   ├── captions.py
│   │   │   ├── captions_server.py
│   │   │   ├── citations.py
│   │   │   ├── constants.py
│   │   │   ├── convert_process.py
│   │   │   ├── dependencies.py
│   │   │   ├── doctypes.py
│   │   │   ├── endpoints.py
│   │   │   ├── epub.py
│   │   │   ├── features.py
│   │   │   ├── html_clean.py
│   │   │   ├── images.py
│   │   │   ├── log.py
│   │   │   ├── managers.py
│   │   │   ├── mini_player.py
│   │   │   ├── office.py
│   │   │   ├── office_basic.py
│   │   │   ├── office_install.py
│   │   │   ├── page_edit.py
│   │   │   ├── pdf_annotations.py
│   │   │   ├── pdf_forms.py
│   │   │   ├── permissions.py
│   │   │   ├── processes.py
│   │   │   ├── redaction.py
│   │   │   ├── render_process.py
│   │   │   ├── settings.py
│   │   │   ├── signature_appearance.py
│   │   │   ├── storage.py
│   │   │   ├── tables.py
│   │   │   └── window_snap.py
│   │   ├── ui/
│   │   │   ├── browser.py
│   │   │   ├── browser_handlers.py
│   │   │   ├── command_palette.py
│   │   │   ├── components.py
│   │   │   ├── explorer.py
│   │   │   ├── favourites.py
│   │   │   ├── office_install.py
│   │   │   ├── palette.py
│   │   │   ├── shortcuts.py
│   │   │   ├── sidecar.py
│   │   │   ├── threads.py
│   │   │   ├── toast.py
│   │   │   ├── tooltip.py
│   │   │   ├── reader/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── citation_card.py
│   │   │   │   ├── notes.py
│   │   │   │   ├── presentation.py
│   │   │   │   ├── render_worker.py
│   │   │   │   ├── replace_text.py
│   │   │   │   ├── sidebar.py
│   │   │   │   ├── tab.py
│   │   │   │   ├── toolbar_layout.py
│   │   │   │   ├── utils.py
│   │   │   │   ├── widgets.py
│   │   │   │   ├── workers.py
│   │   │   │   └── mixins/
│   │   │   │       ├── ai.py
│   │   │   │       ├── annotations.py
│   │   │   │       ├── appearance.py
│   │   │   │       ├── citations.py
│   │   │   │       ├── document.py
│   │   │   │       ├── forms.py
│   │   │   │       ├── host.py
│   │   │   │       ├── input.py
│   │   │   │       ├── metadata.py
│   │   │   │       ├── navigation.py
│   │   │   │       ├── pages.py
│   │   │   │       ├── read_aloud.py
│   │   │   │       ├── rendering.py
│   │   │   │       ├── search.py
│   │   │   │       ├── selection.py
│   │   │   │       ├── signatures.py
│   │   │   │       └── toolbar.py
│   │   │   ├── system/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py
│   │   │   │   ├── help_tab.py
│   │   │   │   ├── lists.py
│   │   │   │   ├── settings_tab.py
│   │   │   │   └── widgets.py
│   │   │   └── viewers/
│   │   │       ├── __init__.py
│   │   │       ├── image_tab.py
│   │   │       ├── spreadsheet_tab.py
│   │   │       └── table_tab.py
│   │   └── window/
│   │       ├── __init__.py
│   │       ├── chrome.py
│   │       ├── common.py
│   │       ├── desktop.py
│   │       ├── dialogs.py
│   │       ├── documents.py
│   │       ├── folders.py
│   │       ├── instance.py
│   │       ├── menus.py
│   │       ├── panes.py
│   │       ├── session.py
│   │       ├── system.py
│   │       └── tabs.py
│   └── tests/
│       ├── conftest.py
│       ├── form_fixtures.py
│       ├── test_ai.py
│       ├── test_ai_server.py
│       ├── test_annotation_store.py
│       ├── test_annotations.py
│       ├── test_app.py
│       ├── test_assets.py
│       ├── test_bibliography.py
│       ├── test_browser.py
│       ├── test_browser_handlers.py
│       ├── test_build_files.py
│       ├── test_captions_server.py
│       ├── test_citations.py
│       ├── test_command_palette.py
│       ├── test_components.py
│       ├── test_compression.py
│       ├── test_constants.py
│       ├── test_core_bindings.py
│       ├── test_dependency_lists.py
│       ├── test_desktop.py
│       ├── test_ebooks.py
│       ├── test_endpoints.py
│       ├── test_help.py
│       ├── test_images.py
│       ├── test_imports.py
│       ├── test_managers.py
│       ├── test_metadata.py
│       ├── test_mini_player.py
│       ├── test_notes.py
│       ├── test_ocr_pages.py
│       ├── test_office.py
│       ├── test_office_basic.py
│       ├── test_office_install.py
│       ├── test_page_edit.py
│       ├── test_palette.py
│       ├── test_pdf_annotations.py
│       ├── test_pdf_forms.py
│       ├── test_permissions.py
│       ├── test_presentation.py
│       ├── test_protect.py
│       ├── test_read_aloud.py
│       ├── test_reader_features.py
│       ├── test_redaction.py
│       ├── test_render_process.py
│       ├── test_rendering.py
│       ├── test_replace_text.py
│       ├── test_search.py
│       ├── test_settings_keys.py
│       ├── test_shortcuts.py
│       ├── test_sidecar.py
│       ├── test_signature_appearance.py
│       ├── test_signatures.py
│       ├── test_storage.py
│       ├── test_tab.py
│       ├── test_table_edits.py
│       ├── test_tables.py
│       ├── test_text_view.py
│       ├── test_threads.py
│       ├── test_toolbar_layout.py
│       ├── test_tooltip.py
│       ├── test_user_sessions.py
│       ├── test_utils.py
│       ├── test_widgets.py
│       ├── test_window_snap.py
│       ├── test_workers.py
│       ├── text_fixtures.py
│       └── user_session.py
├── requirements/
│   ├── optional-all.txt
│   ├── requirements.sh
│   └── xreqs.txt
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
│       ├── test_ocr.rs
│       └── test_ocr_pipes.rs
└── scripts/
    ├── build-excludes.txt
    ├── build.sh
    ├── create_model_pack.sh
    ├── fetch_pdfium.py
    ├── generate_white_icons.py
    ├── install_icon.sh
    ├── lock_optional.py
    ├── nbuild.ps1
    ├── nbuild.sh
    ├── replace_exec.sh
    ├── take_screenshots.py
    └── test_runner.sh
```
