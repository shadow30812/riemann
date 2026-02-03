#!/bin/bash

# Configuration
WORK_DIR="model_pack"
OUTPUT_ZIP="latex_ocr_modules.zip"
# Dependencies from Instructions.md
PACKAGES="pix2tex torch torchvision transformers pillow numpy"

echo "🚀 Starting Model Pack creation..."

# 1. Clean up previous artifacts
if [ -d "$WORK_DIR" ]; then
    echo "🧹 Removing existing $WORK_DIR..."
    rm -rf "$WORK_DIR"
fi

if [ -f "$OUTPUT_ZIP" ]; then
    echo "🧹 Removing existing $OUTPUT_ZIP..."
    rm "$OUTPUT_ZIP"
fi

# 2. Create clean directory
mkdir "$WORK_DIR"
echo "✅ Created directory: $WORK_DIR"

# 3. Install dependencies
# Using --no-deps for torch/torchvision might be safer if we want STRICT control, 
# but here we follow the standard pip install which resolves deps.
# We use the CPU wheel URL to save massive amounts of space.
echo "📦 Installing dependencies (this may take a while)..."
python3.11 -m pip install $PACKAGES \
    --target "./$WORK_DIR" \
    --no-user \
    --ignore-installed \
    --extra-index-url https://download.pytorch.org/whl/cpu

if [ $? -ne 0 ]; then
    echo "❌ Pip install failed! Exiting."
    exit 1
fi

# 4. Clean up unnecessary files to save more space (Optional but recommended)
# Removes cache directories and compiled python files
echo "🧹 Cleaning up bytecode and cache..."
find "$WORK_DIR" -type d -name "__pycache__" -exec rm -rf {} +
find "$WORK_DIR" -type f -name "*.pyc" -delete
find "$WORK_DIR" -type d -name "*.dist-info" -exec rm -rf {} +

# 5. Zip the content
echo "🤐 Zipping contents..."
cd "$WORK_DIR" || exit
# -r recurses, -q is quiet
zip -r -q "../$OUTPUT_ZIP" .
cd ..

# 6. Final cleanup (optional - comment out if you want to inspect the folder)
rm -rf "$WORK_DIR"

echo "🎉 Success! Created $OUTPUT_ZIP"
echo "Checking size..."
du -h "$OUTPUT_ZIP"