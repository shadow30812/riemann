#!/bin/bash
set -e

# --- Configuration ---
GREEN='\033[0;32m'
NC='\033[0m'

log_info() { echo -e "${GREEN}${1}${NC}"; }

# 1. Check Dependencies
log_info "[1/4] Checking Environment..."
if ! command -v maturin &> /dev/null; then
    echo "Error: maturin is missing. Run 'pip install maturin'"
    exit 1
fi
if ! python3 -m nuitka --version &> /dev/null; then
    echo "Error: nuitka is missing. Run 'pip install nuitka patchelf'"
    exit 1
fi

# 2. Build Rust Core
log_info "[2/4] Building Rust Backend..."
# We run maturin to ensure the environment is prepped, but we will fetch the file manually.
maturin develop --release

# 3. Explicitly Fetch the Binary
log_info "[3/4] Positioning Rust Extension..."

# Define expected paths
SOURCE_BINARY="rust-core/target/release/libriemann_core.so"
DEST_BINARY="python-app/riemann/riemann_core.abi3.so"

# Check if the binary exists in the standard cargo location
if [ ! -f "$SOURCE_BINARY" ]; then
    # Fallback: Check root target directory (in case of workspace config)
    SOURCE_BINARY="target/release/libriemann_core.so"
fi

if [ -f "$SOURCE_BINARY" ]; then
    echo "   Found compiled binary at: $SOURCE_BINARY"
    cp "$SOURCE_BINARY" "$DEST_BINARY"
    echo "   Copied to: $DEST_BINARY"
else
    echo "FATAL ERROR: Could not locate 'libriemann_core.so'."
    echo "Checked: rust-core/target/release/ and target/release/"
    exit 1
fi

# 4. Compile with Nuitka (Optimized for Size)
log_info "[4/4] Compiling with Nuitka..."

export PYTHONPATH=$PYTHONPATH:$(pwd)/python-app

# --- EXCLUSION LIST ---
# Excluding these saves massive space (~400MB+) and prevents crashes.
EXCLUDES="--nofollow-import-to=torch --nofollow-import-to=torchvision --nofollow-import-to=cv2 --nofollow-import-to=pix2tex"
EXCLUDES="$EXCLUDES --nofollow-import-to=transformers --nofollow-import-to=scipy --nofollow-import-to=pandas"
EXCLUDES="$EXCLUDES --nofollow-import-to=nvidia" 

python3 -m nuitka \
    --onefile \
    --lto=no \
    --enable-plugin=pyside6 \
    --enable-plugin=upx \
    --include-package=riemann \
    --include-data-dir=python-app/riemann/assets=riemann/assets \
    --include-data-file=libs/libpdfium.so=libpdfium.so \
    --output-dir=dist \
    --output-filename=Riemann \
    $EXCLUDES \
    build_entry.py

# 5. Final Strip (Safety check if strip exists)
if command -v strip &> /dev/null && [ -f "dist/Riemann" ]; then
    log_info "Stripping debug symbols..."
    strip -s dist/Riemann
fi

log_info "-------------------------------------------------------"
log_info "SUCCESS! Optimized executable is at: dist/Riemann"
log_info "-------------------------------------------------------"