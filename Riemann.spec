# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification for Riemann.

This configuration file defines the build process for creating the standalone
Riemann executable. It handles:
1. Resource collection (riemann package, PySide6, and onnxruntime, tokenizers
   and uvicorn's dynamically imported modules for the AI engine, which runs
   as this executable with --riemann-ai-sidecar; the readers behind the
   basic Office views, imported only inside the converter's functions). Asset files that only
   matter in a checkout (the JavaScript tests, logs) are left out.
2. Binary dependency management (libpdfium).
3. Exclusion of the modules listed in scripts/build-excludes.txt, the list
   the Nuitka builds use too: on-demand and unused libraries (torch, cv2),
   and onnxruntime's and tokenizers' tool subpackages, which pull in sympy
   and IPython.
"""

import sys
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

with open(os.path.join('scripts', 'build-excludes.txt'), encoding='utf-8') as f:
    BUILD_EXCLUDES = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]


def _excluded(module):
    return any(module == name or module.startswith(name + '.') for name in BUILD_EXCLUDES)

# --- Configuration & Resource Collection ---

def collect_resources():
    """
    Collects necessary data, binaries, and hidden imports for the application.

    Returns:
        tuple: A tuple containing lists of (datas, binaries, hiddenimports).
    """
    datas = []
    binaries = []
    hiddenimports = []

    # Collect main application resources
    r_datas, r_binaries, r_hidden = collect_all('riemann')
    datas += r_datas
    binaries += r_binaries
    hiddenimports += r_hidden

    # Collect QtWebEngine resources
    q_datas, q_binaries, q_hidden = collect_all('PySide6')
    datas += q_datas
    binaries += q_binaries
    hiddenimports += q_hidden

    for package in ('onnxruntime', 'tokenizers', 'yt_dlp_ejs', 'mammoth', 'pptx', 'openpyxl', 'xlrd', 'striprtf'):
        p_datas, p_binaries, p_hidden = collect_all(package)
        datas += p_datas
        binaries += p_binaries
        hiddenimports += p_hidden
    hiddenimports += [
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'riemann.core.ai_server',
    ]
    datas = [d for d in datas if '__tests__' not in d[0] and not d[0].endswith('.log')]
    hiddenimports = [h for h in hiddenimports if not _excluded(h)]

    icon_path = os.path.join('python-app', 'riemann', 'assets', 'icons', 'windows-icon.ico')
    png_path = os.path.join('python-app', 'riemann', 'assets', 'icons', 'Icon.png')

    if os.path.exists(icon_path):
        datas.append((icon_path, 'riemann/assets'))
    elif os.path.exists(png_path):
        datas.append((png_path, 'riemann/assets'))
    else:
        print(f"WARNING: Icon not found at {icon_path} or {png_path}")

    return datas, binaries, hiddenimports

def get_pdfium_binary():
    """
    Determines the path and filename of the platform-specific PDFium library.

    Returns:
        tuple or None: A tuple (source_path, dest_folder) if found, else None.
    """
    pdfium_filename = "libpdfium.so"
    if sys.platform == "win32":
        pdfium_filename = "pdfium.dll"
    elif sys.platform == "darwin":
        pdfium_filename = "libpdfium.dylib"

    # The build script is expected to place the library in 'libs/'
    pdfium_path = os.path.abspath(os.path.join("libs", pdfium_filename))

    if os.path.exists(pdfium_path):
        return (pdfium_path, ".")
    else:
        print(f"WARNING: {pdfium_filename} not found in libs/; run python scripts/fetch_pdfium.py")
        return None

# --- Build Analysis ---

datas, binaries, hiddenimports = collect_resources()
pdfium_bin = get_pdfium_binary()

if pdfium_bin:
    binaries.append(pdfium_bin)

a = Analysis(
    ['build_entry.py'],
    pathex=['python-app'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=BUILD_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Packaging ---

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Riemann',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='python-app/riemann/assets/icons/windows-icon.ico',
)