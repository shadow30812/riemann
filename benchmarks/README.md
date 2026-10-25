# Benchmarks

Scripts that measure Riemann's PDF engine and reader, plus the results they produced. Every number in the main README comes from a JSON file in `results/`.

## Corpus

Four public PDFs, downloaded and checked against SHA-256 hashes by `fetch_corpus.py`. The files themselves are not committed.

| File | Source | Pages | Why it's here |
|---|---|---|---|
| `attention.pdf` | arXiv 1706.03762v7 | 15 | a typical paper: text, vector figures, a few tables |
| `latent_diffusion.pdf` | arXiv 2112.10752v2 | 45 | 41 MB of raster images, the slowest document for both engines |
| `thinkpython2.pdf` | Green Tea Press | 244 | book typesetting, mostly text |
| `pdf17_spec.pdf` | Adobe (ISO 32000-1) | 756 | long and dense, with 3,915 internal links |

## Scripts

Run them from the repository root, so the Rust core finds `libpdfium.so`:

```bash
env -u LD_LIBRARY_PATH riemann/bin/python benchmarks/fetch_corpus.py
```

```bash
env -u LD_LIBRARY_PATH riemann/bin/python benchmarks/bench_core.py
```

| Script | What it measures |
|---|---|
| `bench_core.py` | The Rust core against PyMuPDF on the same pages: open time, render time at 1x, 2x and 3x (72, 144 and 216 DPI), text extraction speed and full-document search. Up to 40 pages sampled per document, median of 3 runs per page. |
| `bench_scroll.py` | A reader tab in a 1400x1000 window at Fit Width, scrolled at 2,000, 6,000 and 19,000 px/s with 60 ticks per second. After each tick it records whether the page in the middle of the viewport is sharp, a low-resolution stand-in, or blank, and how long the longest single pass of the Qt event loop took (how long the window froze). Median of 3 runs. |
| `bench_startup.py` | Launches the full app with one PDF and times launch to window, and launch to a sharp first page, with memory use at that point. The first launch on empty data folders (which builds a private virtual environment) is reported separately. Median of 5 runs. |
| `bench_memory.py` | Opens 1, 5 and 10 reader tabs, pages through 60 pages in each, and records resident memory and how much the shared page cache holds. |

`bench_scroll.py`, `bench_startup.py` and `bench_memory.py` take `--app-path` (the `python-app` folder of another checkout) and `--core-path` (a folder holding a different `riemann_core` build), so an older version can be measured with the same script. `bench_core.py` takes `--core-path`. Every script takes `--label`, which names its output file.

The results in `results/` compare three versions:

| Label | Version |
|---|---|
| `original` | commit `a2d54af` with the Rust core built from it |
| `session1` | commit `4f65f07`, which added the background renderer |
| `current` | `4f65f07` plus the uncommitted rendering, startup and memory work described in the main README |

## Method and caveats

- Machine: Intel Core i5-13500H (16 logical cores), 13 GB RAM, Rocky Linux 10.2, Python 3.11.16, PySide6 6.10.1, PyMuPDF 1.28.2. Measured on 2026-10-25.
- The scroll, startup and memory scripts use Qt's offscreen platform, so there is no compositor in the loop and the device pixel ratio is 1.
- The OS file cache is warm. Startup times vary by about 0.3 s from run to run on this machine.
- The source tree and virtual environment sit on an NTFS partition mounted through FUSE, which makes Python imports slower than on a native file system.
- PyMuPDF renders RGB without alpha; the Rust core returns 32-bit BGRA. Both include copying the pixels into a Python object.
- Search hit counts differ slightly between engines on two documents because MuPDF and PDFium segment text differently.
