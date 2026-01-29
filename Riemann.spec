# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# --- CONFIGURATION ---
datas = []
binaries = []
hiddenimports = []

# 1. Collect your app resources
tmp_ret = collect_all('riemann')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# 2. Collect QtWebEngine
qt_ret = collect_all('PySide6')
datas += qt_ret[0]
binaries += qt_ret[1]
hiddenimports += qt_ret[2]

# 3. Bundle PDFium (Cross-Platform Logic)
# Determine the correct filename for the current OS
pdfium_filename = "libpdfium.so"
if sys.platform == "win32":
    pdfium_filename = "pdfium.dll"
elif sys.platform == "darwin":
    pdfium_filename = "libpdfium.dylib"

# We assume the CI/Script placed it in 'libs/'
pdfium_path = os.path.abspath(os.path.join("libs", pdfium_filename))

if os.path.exists(pdfium_path):
    # This puts it at the root of the temp folder at runtime
    binaries.append((pdfium_path, "."))
else:
    print(f"WARNING: {pdfium_filename} not found in libs/ folder!")

a = Analysis(
    ['build_entry.py'],
    pathex=['python-app'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
    console=False, # Set to True if debugging crashes
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)