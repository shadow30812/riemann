# Task runner for the Riemann project.
#
#   just run     fetch PDFium if needed, rebuild the Rust extension, start the app
#   just fetch-pdfium  download PDFium into libs/, checked against pinned SHA-256s
#   just build   rebuild the Rust extension (release) into the active environment
#   just test    run every test suite (scripts/test_runner.sh)
#   just lint    rustfmt and clippy on the Rust code (as CI runs them)
#   just version 7.2.0  set the version in every file (or major, minor, patch)
#   just clean   remove build output and caches inside the project's own folders
#
# `clean` only touches rust-core, rust-ocr-worker, python-app and riemann-ai,
# never the riemann/ or .venv/ environments or libs/ (which holds PDFium).

python := if path_exists("./riemann/bin/python") == "true" { "./riemann/bin/python" } else if path_exists("./.venv/bin/python") == "true" { "./.venv/bin/python" } else { "python3" }

run: fetch-pdfium build
	env -u LD_LIBRARY_PATH PYTHONPATH=python-app {{python}} -m riemann

build:
	maturin develop --release

fetch-pdfium:
	{{python}} scripts/fetch_pdfium.py

# Rewrites the hashed locks of optional packages and requirements/xreqs.txt after a version in pyproject.toml changes. Needs uv and network.
lock:
	{{python}} scripts/lock_optional.py
	bash requirements/requirements.sh

test:
	bash scripts/test_runner.sh

lint:
	cargo fmt --all --check
	PYO3_PYTHON="$(realpath {{python}})" cargo clippy --workspace --all-targets --no-default-features -- -D warnings

# Sets the version everywhere it is written: just version 7.2.0, or major, minor or patch; --check lists them.
version new:
	{{python}} scripts/set_version.py {{new}}

test-rust:
	cargo test -p riemann_ocr_worker
	PYO3_PYTHON="$(realpath {{python}})" LD_LIBRARY_PATH="$({{python}} -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')" cargo test --manifest-path rust-core/Cargo.toml --no-default-features

clean:
	cargo clean
	find rust-core rust-ocr-worker python-app riemann-ai -path python-app/riemann/assets/riemann_ai_engine -prune -o \( -name "*.so" -o -name "*.pyd" \) -type f -print -delete
	find rust-core rust-ocr-worker python-app riemann-ai benchmarks -path python-app/riemann/assets/riemann_ai_engine -prune -o -name "__pycache__" -type d -print -exec rm -rf {} +
