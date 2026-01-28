#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

# Colors for pretty output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}[1/5] Checking Environment...${NC}"

# Check for maturin and pyinstaller
if ! command -v maturin &> /dev/null; then
    echo -e "${RED}Error: maturin is not installed. Run 'pip install maturin'${NC}"
    exit 1
fi
if ! command -v pyinstaller &> /dev/null; then
    echo -e "${RED}Error: pyinstaller is not installed. Run 'pip install pyinstaller'${NC}"
    exit 1
fi

# Check for libpdfium.so
if [ ! -f "libs/libpdfium.so" ]; then
    echo -e "${RED}Error: libs/libpdfium.so is missing!${NC}"
    echo "Please download 'pdfium-linux-x64.tgz' from https://github.com/bblanchon/pdfium-binaries/releases"
    echo "Extract 'lib/libpdfium.so' and place it in the 'libs/' folder in your project root."
    exit 1
fi

echo -e "${GREEN}[2/5] Building Rust Backend (Release Mode)...${NC}"
maturin develop --release

echo -e "${GREEN}[3/5] Moving Rust Extension to Source Tree...${NC}"
# Use Python to accurately find the built extension and move it to the correct place for PyInstaller
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

echo -e "${GREEN}[4/5] Running PyInstaller (One-File Build)...${NC}"
# Run the build using your Riemann.spec
pyinstaller Riemann.spec --clean --noconfirm

echo -e "${GREEN}[5/5] Cleanup...${NC}"
# Optional: Remove the artifacts from the source tree to keep it clean
rm python-app/riemann/riemann_core.abi3.so

echo -e "${GREEN}-------------------------------------------------------${NC}"
echo -e "${GREEN}SUCCESS! Your standalone executable is ready:${NC}"
echo -e "   dist/Riemann"
echo -e "${GREEN}-------------------------------------------------------${NC}"