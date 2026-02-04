#!/bin/bash

# -----------------------------------------------------------------------------
# Script Name: create_model_pack.sh
# Description: Automates the creation of the 'latex_ocr_modules.zip' dependency pack.
#              1. Creates a clean workspace.
#              2. Downloads Python dependencies (torch, pix2tex, etc.) targeted for CPU.
#              3. Cleans bytecode and unnecessary metadata.
#              4. Archives the environment into a zip file.
# -----------------------------------------------------------------------------

# --- Configuration ---
WORK_DIR="model_pack"
OUTPUT_ZIP="latex_ocr_modules.zip"
PACKAGES="pix2tex torch torchvision transformers pillow numpy"
PIP_CMD="python3.11 -m pip"

# --- Helper Functions ---

cleanup_workspace() {
    # Removes previous build artifacts and directories.
    if [ -d "$WORK_DIR" ]; then
        echo "🧹 Removing existing $WORK_DIR..."
        rm -rf "$WORK_DIR"
    fi

    if [ -f "$OUTPUT_ZIP" ]; then
        echo "🧹 Removing existing $OUTPUT_ZIP..."
        rm "$OUTPUT_ZIP"
    fi
}

create_directory_structure() {
    # Creates the working directory for dependency installation.
    mkdir "$WORK_DIR"
    echo "✅ Created directory: $WORK_DIR"
}

install_dependencies() {
    # Installs required Python packages into the target directory.
    # Uses the CPU-specific wheel repository to minimize size.
    echo "📦 Installing dependencies (this may take a while)..."
    
    $PIP_CMD install $PACKAGES \
        --target "./$WORK_DIR" \
        --no-user \
        --ignore-installed \
        --extra-index-url https://download.pytorch.org/whl/cpu

    if [ $? -ne 0 ]; then
        echo "❌ Pip install failed! Exiting."
        exit 1
    fi
}

clean_bytecode() {
    # Removes __pycache__, .pyc files, and dist-info directories to reduce package size.
    echo "🧹 Cleaning up bytecode and cache..."
    find "$WORK_DIR" -type d -name "__pycache__" -exec rm -rf {} +
    find "$WORK_DIR" -type f -name "*.pyc" -delete
    find "$WORK_DIR" -type d -name "*.dist-info" -exec rm -rf {} +
}

archive_package() {
    # Zips the contents of the working directory into the output file.
    echo "🤐 Zipping contents..."
    cd "$WORK_DIR" || exit
    zip -r -q "../$OUTPUT_ZIP" .
    cd ..
}

finalize() {
    # Cleans up the working directory and displays the final file size.
    rm -rf "$WORK_DIR"
    echo "🎉 Success! Created $OUTPUT_ZIP"
    echo "Checking size..."
    du -h "$OUTPUT_ZIP"
}

# --- Main Execution ---

main() {
    echo "🚀 Starting Model Pack creation..."
    cleanup_workspace
    create_directory_structure
    install_dependencies
    clean_bytecode
    archive_package
    finalize
}

main