#!/bin/bash
set -e

cd "$(dirname "$0")/.."

# With --sessions the script builds the session driver (python-app/tests/
# nuitka_session.py) instead of Riemann: the same modules and flags, as a
# folder rather than one file, so the user-session tests can play their
# scenarios inside a Nuitka build:
#   cd python-app && RIEMANN_SESSION_DRIVER=../dist/sessions/nuitka_session.dist/riemann-sessions \
#     ../riemann/bin/python -m pytest tests/test_user_sessions.py
ENTRY=build_entry.py
PACKAGING=--onefile
OUTPUT_DIR=dist
OUTPUT_NAME=Riemann
if [ "${1:-}" = "--sessions" ]; then
    ENTRY=python-app/tests/nuitka_session.py
    PACKAGING=--standalone
    OUTPUT_DIR=dist/sessions
    OUTPUT_NAME=riemann-sessions
fi

GREEN='\033[0;32m'
NC='\033[0m'

log_info() { echo -e "${GREEN}${1}${NC}"; }

log_info "[1/4] Checking Environment..."
if ! command -v maturin &> /dev/null; then
    echo "Error: maturin is missing. Run 'pip install maturin'"
    exit 1
fi
NUITKA_PIN=$(sed -n 's/^nuitka==//p' scripts/build-requirements.txt)
NUITKA_HAVE=$(python3 -m nuitka --version 2>/dev/null | head -n 1 || true)
if [ "$NUITKA_HAVE" != "$NUITKA_PIN" ]; then
    echo "Error: the build needs Nuitka ${NUITKA_PIN} (found: ${NUITKA_HAVE:-none})."
    echo "Run 'pip install -r scripts/build-requirements.txt patchelf'"
    exit 1
fi

log_info "[2/4] Building Rust Backend..."
maturin develop --release

log_info "[3/4] Positioning Rust Extension..."

SOURCE_BINARY="rust-core/target/release/libriemann_core.so"
DEST_BINARY="python-app/riemann/riemann_core.abi3.so"

if [ ! -f "$SOURCE_BINARY" ]; then
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

python3 scripts/fetch_pdfium.py linux-x64

log_info "[4/4] Compiling with Nuitka..."

export PYTHONPATH=$PYTHONPATH:$(pwd)/python-app:$(pwd)/python-app/tests:$(pwd)
export LIBRARY_PATH="$(pwd)/libs:${LIBRARY_PATH:-}"

# ── Discover PySide6's bundled Qt directories ──
# These contain ALL Qt6 shared libraries (.so), ICU libs, and Qt plugins.
# We selectively include ONLY the ones the app needs to save ~150 MB.
PYSIDE6_DIR=$(python3 -c "import os, PySide6; print(os.path.dirname(PySide6.__file__))")
PYSIDE6_QT_LIB="${PYSIDE6_DIR}/Qt/lib"
PYSIDE6_QT_PLUGINS="${PYSIDE6_DIR}/Qt/plugins"

if [ ! -d "$PYSIDE6_QT_LIB" ]; then
    echo "FATAL: PySide6 Qt lib directory not found at: $PYSIDE6_QT_LIB"
    exit 1
fi

# ── Stage the Qt6 libraries Nuitka can't see ──
# Nuitka follows the links of every Qt library and plugin it includes, as ldd
# resolves them. LD_LIBRARY_PATH can point those links at another Qt build
# (on the main dev machine it names /opt/plecs), and then Nuitka leaves out
# PySide6's own copies. Unsetting it keeps the build the same in any shell;
# staging those libraries by hand as well makes Nuitka stop on a data file
# that clashes with a DLL. Only the FFmpeg stubs, which Qt Multimedia loads
# at run time rather than links, are staged.
unset LD_LIBRARY_PATH
QT_STAGING=$(mktemp -d)
trap "rm -rf '$QT_STAGING'" EXIT
mkdir -p "${QT_STAGING}/lib"
copied=0

# Copy multimedia FFmpeg stubs if present
for f in "${PYSIDE6_QT_LIB}"/libQt6FFmpegStub*; do
  if [ -f "$f" ]; then
    cp "$f" "${QT_STAGING}/lib/"
    copied=$((copied + 1))
  fi
done
echo "   Staged ${copied} Qt6 FFmpeg stubs into onefile root"

EXCLUDES=$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' -e 's/^[[:space:]]*/--nofollow-import-to=/' scripts/build-excludes.txt | tr '\n' ' ')

UPX_PLUGIN=""
if command -v upx &> /dev/null; then
    UPX_PLUGIN="--enable-plugin=upx"
else
    echo "Notice: 'upx' binary not found. Compiling without UPX compression."
fi

python3 -m nuitka \
    $PACKAGING \
    --lto=no \
    --jobs=$(nproc) \
    --enable-plugin=pyside6 \
    --include-module=PySide6.QtDBus \
    $UPX_PLUGIN \
    --include-package=riemann \
    --include-data-dir=python-app/riemann/assets=riemann/assets \
    --noinclude-data-files='riemann/assets/__tests__/*' \
    --noinclude-data-files='riemann/assets/*.log' \
    --include-package=onnxruntime \
    --include-package-data=onnxruntime \
    --noinclude-data-files='onnxruntime/datasets/*' \
    --noinclude-data-files='onnxruntime/tools/*' \
    --noinclude-data-files='onnxruntime/capi/*.so*' \
    --include-package=tokenizers \
    --include-package=uvicorn \
    --include-package=yt_dlp_ejs \
    --include-package-data=yt_dlp_ejs \
    --include-package=mammoth \
    --include-package=pptx \
    --include-package=openpyxl \
    --include-package=xlrd \
    --include-package=striprtf \
    --include-data-file=libs/libpdfium.so=libpdfium.so \
    --include-data-files=${QT_STAGING}/lib/*=./ \
    --output-dir=$OUTPUT_DIR \
    --output-filename=$OUTPUT_NAME \
    $EXCLUDES \
    $ENTRY

if [ "$ENTRY" != build_entry.py ]; then
    log_info "Session driver: ${OUTPUT_DIR}/nuitka_session.dist/${OUTPUT_NAME}"
    exit 0
fi

if command -v strip &> /dev/null && [ -f "dist/Riemann" ]; then
    log_info "Stripping debug symbols..."
    strip -s dist/Riemann
fi

log_info "-------------------------------------------------------"
log_info "SUCCESS! Optimized executable is at: dist/Riemann"
log_info "-------------------------------------------------------"

if command -v gio &> /dev/null && [ -f "dist/Riemann" ]; then
    log_info "Setting custom GNOME file manager icon for the binary..."
    ICON_PATH="$(pwd)/python-app/riemann/assets/icons/Icon.ico"
    gio set dist/Riemann metadata::custom-icon "file://$ICON_PATH" || true
fi
log_info "Done!"