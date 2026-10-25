"""Compares the Rust core (PDFium) with PyMuPDF (MuPDF) on the same PDFs.

Measures document open time, page render time at several zoom levels, plain text
extraction and full-document search. Both engines run in this one process, one
after the other, on the same pages, with the OS file cache warm.

Run from the repository root (the Rust core looks for libpdfium there):

    env -u LD_LIBRARY_PATH riemann/bin/python benchmarks/bench_core.py

Pass --core-path (a folder holding a riemann_core package) and --label to
measure another build of the core.
"""

import argparse
import os
import sys
import time

from _common import CORPUS, corpus_path, machine_info, median, percentile, write_results

if "--core-path" in sys.argv:
    sys.path.insert(0, os.path.abspath(sys.argv[sys.argv.index("--core-path") + 1]))

import pymupdf  # noqa: E402
import riemann_core  # noqa: E402

SEARCH_TERMS = {
    "attention.pdf": "attention",
    "latent_diffusion.pdf": "diffusion",
    "thinkpython2.pdf": "function",
    "pdf17_spec.pdf": "stream",
}


def sample_pages(count, limit):
    if count <= limit:
        return list(range(count))
    step = count / limit
    return sorted({int(i * step) for i in range(limit)})


class RiemannEngine:
    name = "riemann (Rust + PDFium)"

    def __init__(self):
        self.engine = riemann_core.PdfEngine()

    def open(self, path):
        doc = self.engine.load_document(path)
        _ = doc.page_count
        return doc

    def page_count(self, doc):
        return doc.page_count

    def render(self, doc, idx, scale):
        res = doc.render_page(idx, scale, 0)
        return len(res.data)

    def text(self, doc, idx):
        return doc.get_page_text(idx)

    def search(self, doc, idx, term):
        return len(doc.search_page(idx, term))


class MuPdfEngine:
    name = f"PyMuPDF {pymupdf.VersionBind} (MuPDF)"

    def open(self, path):
        doc = pymupdf.open(path)
        _ = doc.page_count
        return doc

    def page_count(self, doc):
        return doc.page_count

    def render(self, doc, idx, scale):
        pix = doc.load_page(idx).get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return len(pix.samples)

    def text(self, doc, idx):
        return doc.load_page(idx).get_text()

    def search(self, doc, idx, term):
        return len(doc.load_page(idx).search_for(term))


def timed(fn, *args):
    t = time.perf_counter()
    out = fn(*args)
    return (time.perf_counter() - t) * 1000, out


def bench_engine(engine, path, name, scales, page_limit, repeats):
    out = {}
    open_ms = [timed(engine.open, path)[0] for _ in range(10)]
    out["open_ms"] = round(median(open_ms), 2)

    doc = engine.open(path)
    count = engine.page_count(doc)
    pages = sample_pages(count, page_limit)
    engine.render(doc, pages[0], 1.0)  # warm-up, not counted

    out["render"] = {}
    for scale in scales:
        per_page = []
        for idx in pages:
            per_page.append(median([timed(engine.render, doc, idx, scale)[0] for _ in range(repeats)]))
        out["render"][str(scale)] = {
            "pages": len(pages),
            "median_ms": round(median(per_page), 2),
            "p95_ms": round(percentile(per_page, 95), 2),
            "max_ms": round(max(per_page), 2),
        }

    text_runs, chars = [], 0
    for _ in range(repeats):
        t = time.perf_counter()
        chars = sum(len(engine.text(doc, i)) for i in range(count))
        text_runs.append(time.perf_counter() - t)
    out["text"] = {
        "pages": count,
        "chars": chars,
        "total_ms": round(median(text_runs) * 1000, 1),
        "pages_per_s": round(count / median(text_runs)),
    }

    term = SEARCH_TERMS[name]
    search_runs, hits = [], 0
    for _ in range(repeats):
        t = time.perf_counter()
        hits = sum(engine.search(doc, i, term) for i in range(count))
        search_runs.append(time.perf_counter() - t)
    out["search"] = {"term": term, "hits": hits, "total_ms": round(median(search_runs) * 1000, 1)}
    return count, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scales", default="1,2,3", help="render scales (1.0 = 72 DPI)")
    ap.add_argument("--pages", type=int, default=40, help="pages sampled per document for rendering")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--core-path", default=None, help="folder holding another riemann_core build")
    ap.add_argument("--label", default="current")
    ap.add_argument("--no-mupdf", action="store_true", help="skip the PyMuPDF comparison")
    args = ap.parse_args()
    scales = [float(s) for s in args.scales.split(",")]

    engines = [RiemannEngine()] + ([] if args.no_mupdf else [MuPdfEngine()])
    results = {"machine": machine_info(), "settings": vars(args), "documents": {}}
    for name, (_url, _sha, desc) in CORPUS.items():
        path = corpus_path(name)
        entry = {"description": desc, "engines": {}}
        for engine in engines:
            count, data = bench_engine(engine, path, name, scales, args.pages, args.repeats)
            entry["pages"] = count
            entry["engines"][engine.name] = data
            r = data["render"]
            print(
                f"{name:22} {engine.name:28} open {data['open_ms']:7.2f} ms | "
                + " | ".join(f"{s}x {r[str(s)]['median_ms']:7.2f} ms" for s in scales)
                + f" | text {data['text']['pages_per_s']:6} p/s | search {data['search']['total_ms']:8.1f} ms"
                f" ({data['search']['hits']} hits)"
            )
        results["documents"][name] = entry
    results["core_module"] = riemann_core.__file__
    write_results(f"core-{args.label}", results)


if __name__ == "__main__":
    main()
