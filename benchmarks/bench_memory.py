"""Measures memory use as PDF tabs pile up, and checks the page image cache stays in budget.

Opens up to --tabs reader tabs in one 1400x1000 window (Fit Width), cycling
through the corpus. In each new tab it pages through the first --pages pages,
waiting for every page to render sharply, so the shared image cache fills up.
After 1, 5 and 10 tabs it records the process's resident memory and how many
bytes of rendered pages the cache holds.

    env -u LD_LIBRARY_PATH riemann/bin/python benchmarks/bench_memory.py
"""

import argparse
import os
import sys
import time

from _common import CORPUS, corpus_path, isolate_user_dirs, machine_info, rss_mb, use_checkout, write_results

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--tabs", type=int, default=10)
ap.add_argument("--pages", type=int, default=60)
ap.add_argument("--app-path", default=None, help="python-app folder of the checkout to test")
ap.add_argument("--core-path", default=None, help="folder holding a riemann_core package to use")
ap.add_argument("--label", default="current")
ARGS = ap.parse_args()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolate_user_dirs(f"memory-{ARGS.label}")
use_checkout(ARGS.app_path, ARGS.core_path)

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget  # noqa: E402

app = QApplication(sys.argv)

from riemann.ui.reader.render_worker import PAGE_CACHE  # noqa: E402
from riemann.ui.reader.tab import ReaderTab  # noqa: E402


def pump(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def wait_sharp(tab, idx, timeout=5.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        w = tab.page_widgets.get(idx)
        if w is not None and getattr(w, "has_full_page", False):
            return True
        time.sleep(0.002)
    return False


def main():
    win = QMainWindow()
    tabs = QTabWidget()
    win.setCentralWidget(tabs)
    win.resize(1400, 1000)
    win.show()
    pump(0.5)

    names = list(CORPUS)
    samples = [{"tabs": 0, "rss_mb": round(rss_mb(), 1), "cache_mb": 0.0}]
    print(f"baseline (Qt + app modules, no document): {samples[0]['rss_mb']:.0f} MB")
    for n in range(1, ARGS.tabs + 1):
        tab = ReaderTab()
        tabs.addTab(tab, f"tab {n}")
        tabs.setCurrentWidget(tab)
        tab.load_document(corpus_path(names[(n - 1) % len(names)]))
        tab.apply_zoom_string("Fit Width")
        end = time.perf_counter() + 15
        while tab.current_doc is None and time.perf_counter() < end:
            pump(0.01)
        pump(0.5)
        for idx in range(min(ARGS.pages, tab.current_doc.page_count)):
            tab.jump_to_page(idx)
            wait_sharp(tab, idx)
        pump(0.5)
        if n in (1, 5, 10) or n == ARGS.tabs:
            sample = {
                "tabs": n,
                "rss_mb": round(rss_mb(), 1),
                "cache_mb": round(PAGE_CACHE.used / 1024 / 1024, 1),
                "cache_budget_mb": round(PAGE_CACHE.budget / 1024 / 1024, 1),
            }
            samples.append(sample)
            print(
                f"{n:2} tabs: RSS {sample['rss_mb']:6.0f} MB | page cache {sample['cache_mb']:5.0f} MB "
                f"of {sample['cache_budget_mb']:.0f} MB budget"
            )

    write_results(
        f"memory-{ARGS.label}",
        {
            "label": ARGS.label,
            "machine": machine_info(os.path.dirname(os.path.abspath(ARGS.app_path)) if ARGS.app_path else None),
            "settings": {**vars(ARGS), "window": "1400x1000", "zoom": "Fit Width", "platform": app.platformName()},
            "samples": samples,
        },
    )


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
