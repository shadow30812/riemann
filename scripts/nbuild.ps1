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

Write-Host "[3/4] Positioning Rust Extension..." -ForegroundColor Green
python -c "import riemann_core, shutil, os; src = riemann_core.__file__; dst = os.path.join('python-app', 'riemann', 'riemann_core.pyd'); print(f'   Copying {src} -> {dst}'); shutil.copy2(src, dst)"

Write-Host "[4/4] Compiling with Nuitka..." -ForegroundColor Green
$env:PYTHONPATH += ";$(Get-Location)\python-app"

$EXCLUDES = "--nofollow-import-to=torch --nofollow-import-to=torchvision --nofollow-import-to=cv2 --nofollow-import-to=pix2tex --nofollow-import-to=transformers --nofollow-import-to=scipy --nofollow-import-to=pandas --nofollow-import-to=nvidia --nofollow-import-to=fitz --nofollow-import-to=pymupdf"

python -m nuitka `
    --onefile `
    --lto=no `
    --enable-plugin=pyside6 `
    --enable-plugin=upx `
    --include-package=riemann `
    --include-data-dir=python-app/riemann/assets=riemann/assets `
    --include-data-file=libs/pdfium.dll=pdfium.dll `
    --windows-console-mode=disable `
    --windows-icon-from-ico=python-app/riemann/assets/icons/icon.ico `
    --output-dir=dist `
    --output-filename=Riemann.exe `
    $EXCLUDES.Split(" ") `
    build_entry.py

Write-Host "-------------------------------------------------------" -ForegroundColor Green
Write-Host "SUCCESS! Optimized executable is at: dist\Riemann.exe" -ForegroundColor Green
Write-Host "-------------------------------------------------------" -ForegroundColor Green