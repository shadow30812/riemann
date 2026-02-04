#!/bin/bash
set -e

# -----------------------------------------------------------------------------
# Script Name: build.sh
# Description: Orchestrates the build process for the Riemann standalone application.
#              1. Checks environment dependencies.
#              2. Builds Rust core via Maturin.
#              3. Moves artifacts to the Python source tree.
#              4. Packages the application using PyInstaller.
# -----------------------------------------------------------------------------

# --- Constants & Configuration ---
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
LIB_PDFIUM_PATH="libs/libpdfium.so"
RUST_EXT_SRC_CMD="import riemann_core; print(riemann_core.__file__)"
RUST_EXT_DST_PATH="python-app/riemann/riemann_core.abi3.so"

# --- Helper Functions ---

log_info() {
    # Prints a formatted informational message to stdout.
    # Arguments:
    #   $1: The message string.
    echo -e "${GREEN}${1}${NC}"
}

log_error() {
    # Prints a formatted error message to stdout.
    # Arguments:
    #   $1: The message string.
    echo -e "${RED}${1}${NC}"
}

check_dependencies() {
    # Verifies that all required system tools are installed.
    # Checks for: maturin, pyinstaller.
    log_info "[1/5] Checking Environment..."

    if ! command -v maturin &> /dev/null; then
        log_error "Error: maturin is not installed. Run 'pip install maturin' or change the working environment."
        exit 1
    fi

    if ! command -v pyinstaller &> /dev/null; then
        log_error "Error: pyinstaller is not installed. Run 'pip install pyinstaller' or change the working environment."
        exit 1
    fi
}

check_library_assets() {
    # Verifies the presence of external binary assets.
    # Checks for: libpdfium.so in the libs/ directory.
    if [ ! -f "$LIB_PDFIUM_PATH" ]; then
        log_error "Error: $LIB_PDFIUM_PATH is missing!"
        echo "Please download 'pdfium-linux-x64.tgz' from https://github.com/bblanchon/pdfium-binaries/releases"
        echo "Extract 'lib/libpdfium.so' and place it in the 'libs/' folder in your project root."
        exit 1
    fi
}

build_rust_backend() {
    # Compiles the Rust extension module in release mode.
    log_info "[2/5] Building Rust Backend (Release Mode)..."
    maturin develop --release
}

move_rust_extension() {
    # Locates the compiled Rust extension and moves it to the Python source tree.
    # This ensures PyInstaller can locate the binary extension during analysis.
    log_info "[3/5] Moving Rust Extension to Source Tree..."
    
    python3 -c "
import riemann_core
import shutil
import os
import sys

src = riemann_core.__file__
dst = os.path.join('python-app', 'riemann', 'riemann_core.abi3.so')

print(f'   Copying: {src}')
print(f'   To:      {dst}')

shutil.copy2(src, dst)
"
}

run_pyinstaller() {
    # Executes PyInstaller to generate the standalone executable.
    # Uses the Riemann.spec configuration file.
    log_info "[4/5] Running PyInstaller (One-File Build)..."
    pyinstaller Riemann.spec --clean --noconfirm
}

cleanup_artifacts() {
    # Removes temporary build artifacts from the source tree to maintain cleanliness.
    log_info "[5/5] Cleanup..."
    if [ -f "$RUST_EXT_DST_PATH" ]; then
        rm "$RUST_EXT_DST_PATH"
    fi
}

print_success() {
    # Prints the final success message and output location.
    log_info "-------------------------------------------------------"
    log_info "SUCCESS! Your standalone executable is ready:"
    echo -e "   dist/Riemann"
    log_info "-------------------------------------------------------"
}

# --- Main Execution ---

main() {
    check_dependencies
    check_library_assets
    build_rust_backend
    move_rust_extension
    run_pyinstaller
    cleanup_artifacts
    print_success
}

main