#!/bin/bash

set -e

echo "========================================"
echo " Riemann AI Sidecar Compilation Script"
echo "========================================"

CONDA_ENV_NAME="rmai"
OUTPUT_DIR="build_ai"
FINAL_ENGINE_NAME="riemann_ai_engine"
DESTINATION_ASSETS_DIR="../python-app/riemann/assets"

echo "--> Initializing Conda and activating '$CONDA_ENV_NAME'..."
eval "$(conda shell.bash hook)"
conda activate "$CONDA_ENV_NAME"

echo "--> Cleaning up previous builds..."
rm -rf "$OUTPUT_DIR"

EXCLUDES="--nofollow-import-to=torch --nofollow-import-to=torchvision --nofollow-import-to=transformers --nofollow-import-to=scipy --nofollow-import-to=numpy --nofollow-import-to=sentence_transformers --nofollow-import-to=faiss --nofollow-import-to=pydantic --nofollow-import-to=nvidia"

echo "--> Starting Nuitka compilation (Lightning Fast Mode)..."
python -m nuitka \
    --standalone \
    --output-dir="$OUTPUT_DIR" \
    --include-package=uvicorn \
    --include-package=fastapi \
    --include-package=fitz \
    $EXCLUDES \
    main.py

echo "--> Injecting excluded ML dependencies as raw bytecode..."
SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")

cp -r "$SITE_PACKAGES/torch" "$OUTPUT_DIR/main.dist/"
cp -r "$SITE_PACKAGES/transformers" "$OUTPUT_DIR/main.dist/"
cp -r "$SITE_PACKAGES/sentence_transformers" "$OUTPUT_DIR/main.dist/"
cp -r "$SITE_PACKAGES/scipy" "$OUTPUT_DIR/main.dist/"
cp -r "$SITE_PACKAGES/numpy" "$OUTPUT_DIR/main.dist/"
cp -r "$SITE_PACKAGES/faiss" "$OUTPUT_DIR/main.dist/"
cp -r "$SITE_PACKAGES/pydantic" "$OUTPUT_DIR/main.dist/"
cp -r "$SITE_PACKAGES/pydantic_core" "$OUTPUT_DIR/main.dist/"

echo "--> Compilation complete! Deploying to Riemann assets..."
mkdir -p "$DESTINATION_ASSETS_DIR"
rm -rf "$DESTINATION_ASSETS_DIR/$FINAL_ENGINE_NAME"

mv "$OUTPUT_DIR/main.dist" "$DESTINATION_ASSETS_DIR/$FINAL_ENGINE_NAME"
rm -rf "$OUTPUT_DIR"

echo "========================================"
echo " Success! The AI engine is ready at:"
echo " $DESTINATION_ASSETS_DIR/$FINAL_ENGINE_NAME"
echo "========================================"