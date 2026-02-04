# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification for Riemann.

This configuration file defines the build process for creating the standalone
Riemann executable. It handles:
1. Resource collection (riemann package, PySide6).
2. Binary dependency management (libpdfium).
3. Exclusion of heavyweight machine learning libraries (torch, cv2) to keep
   the core executable lightweight.
"""

import sys
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

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
        print(f"WARNING: {pdfium_filename} not found in libs/ folder!")
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
    excludes=['torch', 'torchvision', 'pix2tex', 'cv2'],
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
)