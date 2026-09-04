$ErrorActionPreference = "Stop"

Write-Host "[1/4] Checking Environment..." -ForegroundColor Green
if (!(Get-Command maturin -ErrorAction SilentlyContinue)) {
    Write-Host "Error: maturin is missing. Run 'pip install maturin'" -ForegroundColor Red
    exit 1
}
if (!(Get-Command nuitka -ErrorAction SilentlyContinue)) {
    Write-Host "Error: nuitka is missing. Run 'pip install nuitka'" -ForegroundColor Red
    exit 1
}

Write-Host "[2/4] Building Rust Backend..." -ForegroundColor Green
maturin develop --release
if ($LASTEXITCODE -ne 0) { 
    Write-Host "FATAL ERROR: Maturin/Cargo failed to build the Rust core." -ForegroundColor Red
    exit $LASTEXITCODE 
}

Write-Host "[3/4] Positioning Rust Extension..." -ForegroundColor Green
$LOCATOR_SCRIPT = @"
import riemann_core, shutil, os, glob
base_dir = os.path.dirname(riemann_core.__file__)
pyd_files = glob.glob(os.path.join(base_dir, '*.pyd'))
if not pyd_files:
    raise FileNotFoundError('FATAL: No .pyd file found in ' + base_dir)
src = pyd_files[0]
dst = os.path.join('python-app', 'riemann', 'riemann_core.pyd')
print(f'   Copying {src} -> {dst}')
shutil.copy2(src, dst)
"@

python -c $LOCATOR_SCRIPT
if ($LASTEXITCODE -ne 0) { 
    Write-Host "FATAL ERROR: Failed to locate and copy the compiled Rust extension." -ForegroundColor Red
    exit $LASTEXITCODE 
}

Write-Host "[4/4] Compiling with Nuitka..." -ForegroundColor Green

if ($env:PYTHONPATH) {
    $env:PYTHONPATH += ";$(Get-Location)\python-app"
} else {
    $env:PYTHONPATH = "$(Get-Location)\python-app"
}

$EXCLUDES = "--nofollow-import-to=torch --nofollow-import-to=torchvision --nofollow-import-to=cv2 --nofollow-import-to=pix2tex --nofollow-import-to=transformers --nofollow-import-to=scipy --nofollow-import-to=pandas --nofollow-import-to=nvidia --nofollow-import-to=fitz --nofollow-import-to=pymupdf"
$EXCLUDE_ARGS = $EXCLUDES.Split(" ")

python -m nuitka `
    --onefile `
    --lto=no `
    --enable-plugin=pyside6 `
    --include-package=riemann `
    --include-data-dir=python-app/riemann/assets=riemann/assets `
    --include-data-file=libs/pdfium.dll=pdfium.dll `
    --include-windows-runtime-dlls=no `
    --windows-console-mode=disable `
    --windows-icon-from-ico=python-app/riemann/assets/icons/icon.ico `
    --output-dir=dist `
    --output-filename=Riemann.exe `
    @EXCLUDE_ARGS `
    build_entry.py

if ($LASTEXITCODE -ne 0) { 
    Write-Host "FATAL ERROR: Nuitka compilation failed." -ForegroundColor Red
    exit $LASTEXITCODE 
}

Write-Host "-------------------------------------------------------" -ForegroundColor Green
Write-Host "SUCCESS! Optimized executable is at: dist\Riemann.exe" -ForegroundColor Green
Write-Host "-------------------------------------------------------" -ForegroundColor Green