#!/bin/bash
set -e

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'
NC='\033[0m'

log_info() { echo -e "${GREEN}${1}${NC}"; }

log_info "[1/4] Checking Environment..."
if ! command -v maturin &> /dev/null; then
    echo "Error: maturin is missing. Run 'pip install maturin'"
    exit 1
fi
if ! python3 -m nuitka --version &> /dev/null; then
    echo "Error: nuitka is missing. Run 'pip install nuitka patchelf'"
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

export PYTHONPATH=$PYTHONPATH:$(pwd)/python-app
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

# ── Stage only the Qt6 shared libraries that Nuitka's PySide6 plugin misses ──
# Nuitka automatically detects and bundles Core, Gui, Widgets, Network, DBus, Svg, Pdf,
# Qml, Quick, and all Qt plugins. Including them as data causes duplicate/conflict errors.
# We ONLY stage the transitive libraries that Nuitka's static analysis overlooks:
# - Qml transitive deps: libQt6QmlMeta, libQt6QmlModels, libQt6QmlWorkerScript
# - Platform support:    libQt6WaylandClient, libQt6XcbQpa, libQt6EglFSDeviceIntegration
# - Widgets/Concurrent:  libQt6QuickWidgets, libQt6Concurrent
# - Multimedia stubs:    libQt6FFmpegStub*
QT_STAGING=$(mktemp -d)
trap "rm -rf '$QT_STAGING'" EXIT
mkdir -p "${QT_STAGING}/lib"

NEEDED_LIBS="
  libQt6Concurrent.so.6
  libQt6EglFSDeviceIntegration.so.6
  libQt6QmlMeta.so.6
  libQt6QmlModels.so.6
  libQt6QmlWorkerScript.so.6
  libQt6QuickWidgets.so.6
  libQt6WaylandClient.so.6
  libQt6XcbQpa.so.6
"
copied=0
for lib in $NEEDED_LIBS; do
  src="${PYSIDE6_QT_LIB}/${lib}"
  if [ -f "$src" ]; then
    cp "$src" "${QT_STAGING}/lib/"
    copied=$((copied + 1))
  else
    echo "WARNING: Expected Qt lib not found: $lib"
  fi
done

# Copy multimedia FFmpeg stubs if present
for f in "${PYSIDE6_QT_LIB}"/libQt6FFmpegStub*; do
  if [ -f "$f" ]; then
    cp "$f" "${QT_STAGING}/lib/"
    copied=$((copied + 1))
  fi
done
echo "   Staged ${copied} missing Qt6 shared libraries into onefile root"

EXCLUDES=$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' -e 's/^[[:space:]]*/--nofollow-import-to=/' scripts/build-excludes.txt | tr '\n' ' ')

UPX_PLUGIN=""
if command -v upx &> /dev/null; then
    UPX_PLUGIN="--enable-plugin=upx"
else
    echo "Notice: 'upx' binary not found. Compiling without UPX compression."
fi

python3 -m nuitka \
    --onefile \
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
    --output-dir=dist \
    --output-filename=Riemann \
    $EXCLUDES \
    build_entry.py

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