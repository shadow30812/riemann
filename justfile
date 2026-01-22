# Run the Python app (builds rust extension first)

run: build
 PYTHONPATH=python-app python3 -m riemann

# Build and install the Rust extension into the current venv

build:
 maturin develop

# Run Rust tests

test-rust:
 cargo test -p riemann_core

# Clean build artifacts

clean:
 cargo clean
 find . -name "*.so" -delete
 find . -name "__pycache__" -type d -exec rm -rf {} +
