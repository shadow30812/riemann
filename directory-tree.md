# Tree

```markdown
riemann/
├── README.md
├── LICENSE
├── .gitignore
├── Cargo.toml # workspace or top-level (optional)
├── pyproject.toml # for maturin building the wheel/extension
├── scripts/
│ ├── build_native.sh
│ └── build_python_dev.sh
├── rust-core/
│ ├── Cargo.toml # crate: riemann_core
│ └── src/
│ ├── lib.rs # PyO3 module exports
│ └── pdfium_wrapper.rs # small wrapper around pdfium-render
├── python-app/
│ ├── pyproject.toml (optional) # for venv tooling, dev deps
│ └── riemann/
│ ├── __init__.py
│ ├── main.py # Qt main app
│ └── ui/ # optional: HTML/CSS/katex assets for QtWebEngine
│ └── katex/ # KaTeX assets (css/js) or loaded from CDN
├── .vscode/
│ ├── tasks.json
│ └── launch.json
└── notes/ # design notes, OCR decisions, etc.```
