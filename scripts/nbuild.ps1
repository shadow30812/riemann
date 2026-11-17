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

$EXCLUDE_ARGS = Get-Content scripts/build-excludes.txt |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') } |
    ForEach-Object { "--nofollow-import-to=$_" }

python scripts/fetch_pdfium.py win-x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m nuitka `
    --onefile `
    --lto=no `
    --enable-plugin=pyside6 `
    --include-package=riemann `
    --include-data-dir=python-app/riemann/assets=riemann/assets `
    --noinclude-data-files=riemann/assets/__tests__/* `
    --noinclude-data-files=riemann/assets/*.log `
    --include-package=onnxruntime `
    --include-package-data=onnxruntime `
    --noinclude-data-files=onnxruntime/datasets/* `
    --noinclude-data-files=onnxruntime/tools/* `
    --include-package=tokenizers `
    --include-package=uvicorn `
    --include-package=yt_dlp_ejs `
    --include-package-data=yt_dlp_ejs `
    --include-data-file=libs/pdfium.dll=pdfium.dll `
    --include-windows-runtime-dlls=no `
    --windows-console-mode=attach `
    --windows-icon-from-ico=python-app/riemann/assets/icons/windows-icon.ico `
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